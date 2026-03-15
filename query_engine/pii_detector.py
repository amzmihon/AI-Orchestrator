"""
PII Detector — scans user input for Personally Identifiable Information
and redacts or blocks it before the LLM processes the prompt.

Inspired by LLM Guard and NVIDIA NeMo Guardrails' Colang safety rules.

Supported PII types:
  • Email addresses
  • Phone numbers (international formats)
  • Social Security Numbers (SSN)
  • Credit card numbers (Luhn-validated)
  • IP addresses (v4)
  • National ID / passport patterns
  • Dates of birth in common formats
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class PIIAction(str, Enum):
    """What to do when PII is detected."""
    REDACT = "redact"       # Replace PII with [REDACTED_TYPE]
    BLOCK = "block"         # Reject the entire request
    WARN = "warn"           # Allow but log a warning


class PIIDetectionError(Exception):
    """Raised when PII is detected and action is BLOCK."""


@dataclass(frozen=True)
class PIIMatch:
    pii_type: str
    value: str
    start: int
    end: int


# ── PII Patterns ─────────────────────────────────────────

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    )),
    ("PHONE", re.compile(
        r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
    )),
    ("SSN", re.compile(
        r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"
    )),
    ("CREDIT_CARD", re.compile(
        r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))"
        r"[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{1,4}\b"
    )),
    ("IP_ADDRESS", re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    )),
    ("DATE_OF_BIRTH", re.compile(
        r"\b(?:DOB|date of birth|born on|birthday)[:\s]*"
        r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\b",
        re.IGNORECASE,
    )),
    ("NATIONAL_ID", re.compile(
        r"\b(?:passport|national id|id number)[:\s#]*([A-Z0-9]{6,12})\b",
        re.IGNORECASE,
    )),
]

# ── Canonical Safety Rules (NeMo Colang-inspired) ────────
# These are non-negotiable topics that get blocked outright.

_BLOCKED_TOPIC_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("SALARY_QUERY_BY_NAME", re.compile(
        r"\b(salary|compensation|pay|wage)\b.*\b(of|for)\b\s+[A-Z][a-z]+",
        re.IGNORECASE,
    )),
    ("PASSWORD_REQUEST", re.compile(
        r"\b(password|credential|secret key|api key|token)\b.*\b(of|for|show|give|tell)\b",
        re.IGNORECASE,
    )),
    ("DATA_EXFILTRATION", re.compile(
        r"\b(dump|export|download|extract)\s+(all|entire|every|complete)\b.*"
        r"\b(database|table|record|employee|customer)\b",
        re.IGNORECASE,
    )),
]


def _luhn_check(card_number: str) -> bool:
    """Validate credit card number using the Luhn algorithm."""
    digits = [int(d) for d in card_number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse = digits[::-1]
    for i, d in enumerate(reverse):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def detect_pii(text: str) -> list[PIIMatch]:
    """
    Scan text for PII patterns and return all matches.

    Returns:
        List of PIIMatch objects with type, value, and position.
    """
    matches: list[PIIMatch] = []

    for pii_type, pattern in _PII_PATTERNS:
        for m in pattern.finditer(text):
            value = m.group(0)

            # Extra validation for credit cards (Luhn check)
            if pii_type == "CREDIT_CARD":
                if not _luhn_check(value):
                    continue

            # Skip common false positives for SSN (e.g., years like 2024)
            if pii_type == "SSN":
                digits_only = re.sub(r"[\s-]", "", value)
                if digits_only.startswith("000") or digits_only[3:5] == "00":
                    continue

            matches.append(PIIMatch(
                pii_type=pii_type,
                value=value,
                start=m.start(),
                end=m.end(),
            ))

    return matches


def detect_blocked_topics(text: str) -> list[tuple[str, str]]:
    """
    Check for canonically blocked topics (NeMo Colang-style safety rules).

    Returns:
        List of (topic_name, matched_text) tuples.
    """
    blocked: list[tuple[str, str]] = []
    for topic, pattern in _BLOCKED_TOPIC_PATTERNS:
        m = pattern.search(text)
        if m:
            blocked.append((topic, m.group(0)))
    return blocked


def redact_pii(text: str, matches: list[PIIMatch] | None = None) -> str:
    """
    Replace detected PII with redaction placeholders.

    Example: "Call me at 555-123-4567" → "Call me at [REDACTED_PHONE]"
    """
    if matches is None:
        matches = detect_pii(text)

    if not matches:
        return text

    # Sort by position descending so replacements don't shift indices
    sorted_matches = sorted(matches, key=lambda m: m.start, reverse=True)
    result = text
    for m in sorted_matches:
        placeholder = f"[REDACTED_{m.pii_type}]"
        result = result[:m.start] + placeholder + result[m.end:]

    return result


def scan_and_enforce(
    text: str,
    action: PIIAction = PIIAction.REDACT,
) -> tuple[str, list[PIIMatch], list[tuple[str, str]]]:
    """
    Full input scanning pipeline:
    1. Check for blocked topics (always raises on match).
    2. Detect PII and apply the chosen action.

    Args:
        text: The user's raw input.
        action: REDACT, BLOCK, or WARN.

    Returns:
        Tuple of (processed_text, pii_matches, blocked_topics).

    Raises:
        PIIDetectionError if action is BLOCK and PII is found,
        or if any blocked topic is detected.
    """
    # 1. Blocked topics — always block
    blocked = detect_blocked_topics(text)
    if blocked:
        topics = ", ".join(t[0] for t in blocked)
        raise PIIDetectionError(
            f"Request blocked by safety policy. Flagged topics: {topics}. "
            "Please rephrase your question without referencing sensitive "
            "personal information or requesting bulk data exports."
        )

    # 2. PII detection
    pii_matches = detect_pii(text)

    if not pii_matches:
        return text, [], []

    if action == PIIAction.BLOCK:
        types = ", ".join(set(m.pii_type for m in pii_matches))
        raise PIIDetectionError(
            f"Request contains sensitive personal information ({types}). "
            "Please remove PII before submitting your question."
        )

    if action == PIIAction.REDACT:
        processed = redact_pii(text, pii_matches)
        return processed, pii_matches, []

    # WARN — return original text with matches for logging
    return text, pii_matches, []
