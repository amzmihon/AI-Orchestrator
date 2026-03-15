"""
Output Validator — validates LLM responses against expected structure
and enforces quality constraints before returning to the user.

Inspired by Guardrails AI's structured output validation approach.

Capabilities:
  • SQL syntax pre-validation (before DB execution)
  • Response quality checks (hallucination markers, length limits)
  • JSON structure validation when structured output is expected
  • Confidence scoring for LLM responses
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from enum import Enum


class ValidationSeverity(str, Enum):
    ERROR = "error"         # Must be fixed — reject or retry
    WARNING = "warning"     # Log but allow through
    INFO = "info"           # Informational only


@dataclass(frozen=True)
class ValidationIssue:
    severity: ValidationSeverity
    code: str
    message: str


class OutputValidationError(Exception):
    """Raised when output validation fails with ERROR-level issues."""

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        messages = [f"[{i.code}] {i.message}" for i in issues if i.severity == ValidationSeverity.ERROR]
        super().__init__("; ".join(messages))


# ── Hallucination markers ────────────────────────────────
# Phrases that indicate the LLM is confabulating or hedging on data it
# should have retrieved from the database.

_HALLUCINATION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bI (?:don'?t|do not) have access to\b", re.I),
     "LLM claims no access — data should come from DB"),
    (re.compile(r"\bas an AI(?: language model)?,? I\b", re.I),
     "LLM breaking character with meta-commentary"),
    (re.compile(r"\bI cannot (?:query|access|retrieve)\b", re.I),
     "LLM refusing to use available data pipeline"),
    (re.compile(r"\baccording to (?:my training|my knowledge)\b", re.I),
     "LLM using training data instead of DB results"),
    (re.compile(r"\bI (?:would |might )?(?:suggest|recommend) (?:checking|looking at)\b", re.I),
     "LLM deflecting instead of answering from data"),
]

# ── SQL quality patterns ─────────────────────────────────

_SQL_QUALITY_CHECKS: list[tuple[re.Pattern, str, ValidationSeverity]] = [
    (re.compile(r"SELECT\s+\*", re.I),
     "SELECT * detected — prefer explicit column names",
     ValidationSeverity.WARNING),
    (re.compile(r"(?:^|\s)(?:WHERE|AND|OR)\s+1\s*=\s*1", re.I),
     "Tautology WHERE 1=1 — may indicate injection or lazy generation",
     ValidationSeverity.WARNING),
    (re.compile(r"UNION\s+(?:ALL\s+)?SELECT", re.I),
     "UNION SELECT detected — verify this is intentional",
     ValidationSeverity.WARNING),
    (re.compile(r"(?:CROSS|NATURAL)\s+JOIN", re.I),
     "CROSS/NATURAL JOIN detected — potentially expensive or unintended",
     ValidationSeverity.WARNING),
    (re.compile(r"(?:^|\s)LIMIT\s+(\d+)", re.I),
     "Check LIMIT value is reasonable",
     ValidationSeverity.INFO),
]


def validate_sql_quality(sql: str) -> list[ValidationIssue]:
    """
    Run quality checks on generated SQL (post-guardrail, pre-execution).

    These are softer checks than the security guardrail — they flag
    potential quality issues without blocking execution.
    """
    issues: list[ValidationIssue] = []

    for pattern, message, severity in _SQL_QUALITY_CHECKS:
        if pattern.search(sql):
            # For LIMIT, extract the value and check if it's too high
            if "LIMIT" in message:
                m = re.search(r"LIMIT\s+(\d+)", sql, re.I)
                if m:
                    limit_val = int(m.group(1))
                    if limit_val > 10000:
                        issues.append(ValidationIssue(
                            severity=ValidationSeverity.WARNING,
                            code="HIGH_LIMIT",
                            message=f"LIMIT {limit_val} is very large — may cause slow response",
                        ))
                continue

            issues.append(ValidationIssue(
                severity=severity,
                code=pattern.pattern[:30].replace(r"\s+", "_").upper(),
                message=message,
            ))

    # Check for missing LIMIT (queries without any LIMIT clause)
    if not re.search(r"LIMIT\s+\d+", sql, re.I):
        issues.append(ValidationIssue(
            severity=ValidationSeverity.WARNING,
            code="NO_LIMIT",
            message="Query has no LIMIT clause — may return excessive rows",
        ))

    return issues


def validate_response_quality(
    response: str,
    intent: str,
    has_data: bool = True,
) -> list[ValidationIssue]:
    """
    Validate the LLM's natural language response for quality.

    Checks for hallucination markers, appropriate length, and
    consistency with the data pipeline context.
    """
    issues: list[ValidationIssue] = []

    # 1. Hallucination detection
    for pattern, reason in _HALLUCINATION_PATTERNS:
        if pattern.search(response):
            issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="HALLUCINATION_MARKER",
                message=reason,
            ))

    # 2. Empty or very short response for data queries
    if intent in ("data_query", "multi_step_analysis") and has_data:
        if len(response.strip()) < 20:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="SHORT_RESPONSE",
                message="Response is suspiciously short for a data query with results",
            ))

    # 3. Very long response (possible runaway generation)
    if len(response) > 15000:
        issues.append(ValidationIssue(
            severity=ValidationSeverity.WARNING,
            code="LONG_RESPONSE",
            message=f"Response is very long ({len(response)} chars) — may need truncation",
        ))

    # 4. Response contains raw SQL (should be separated)
    if intent in ("data_query", "multi_step_analysis"):
        if re.search(r"\bSELECT\b.*\bFROM\b.*\bWHERE\b", response, re.I | re.DOTALL):
            if len(re.findall(r"\bSELECT\b", response, re.I)) > 0:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.INFO,
                    code="SQL_IN_RESPONSE",
                    message="Response may contain raw SQL — should be in metadata only",
                ))

    return issues


def validate_json_output(
    text: str,
    required_keys: list[str] | None = None,
) -> tuple[dict | list | None, list[ValidationIssue]]:
    """
    Attempt to parse LLM output as JSON and validate structure.

    Useful when the prompt requests structured output (e.g., task
    decomposition plans, classification results).
    """
    issues: list[ValidationIssue] = []

    # Try to extract JSON from markdown fences
    json_text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", json_text, re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1).strip()

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        issues.append(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            code="INVALID_JSON",
            message=f"Failed to parse JSON: {exc}",
        ))
        return None, issues

    # Validate required keys if specified
    if required_keys and isinstance(parsed, dict):
        missing = [k for k in required_keys if k not in parsed]
        if missing:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="MISSING_KEYS",
                message=f"Missing required keys: {', '.join(missing)}",
            ))

    return parsed, issues
