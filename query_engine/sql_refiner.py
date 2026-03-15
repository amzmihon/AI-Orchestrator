"""
SQL Refiner — iterative refinement with checkpoint validation.

When an SQL query fails execution, this module:
  1. Analyzes the error to determine the failure type.
  2. Constructs a refinement prompt with the error context.
  3. Asks the LLM to regenerate the SQL with corrections.
  4. Validates the new SQL through the guardrail before retry.

Supports up to N retries (configurable via config.max_sql_retries).
Records each attempt in episodic memory for future learning.

Inspired by Vanna.ai's RAG-enhanced error recovery and LangGraph's
iterative checkpoint pattern.
"""

from __future__ import annotations

import re
import structlog
from dataclasses import dataclass

from config import settings
from llm_client import generate, LLMClientError
from query_engine.sql_guardrail import validate, SQLGuardrailError
from query_engine.prompt_builder import _IDENTITY, _AGENTIC_PREAMBLE

logger = structlog.get_logger()


@dataclass
class RefinementAttempt:
    """Record of a single refinement attempt."""
    attempt_number: int
    original_sql: str
    error_message: str
    error_type: str
    refined_sql: str | None = None
    success: bool = False


# ── Error classification ─────────────────────────────────

_ERROR_CATEGORIES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"column .+ does not exist", re.I),
     "COLUMN_NOT_FOUND",
     "The query references a column that doesn't exist in the table. Check the schema for correct column names."),
    (re.compile(r"relation .+ does not exist", re.I),
     "TABLE_NOT_FOUND",
     "The query references a table that doesn't exist. Use only tables from the provided schema."),
    (re.compile(r"syntax error at or near", re.I),
     "SYNTAX_ERROR",
     "The SQL has a syntax error. Check for missing keywords, parentheses, or commas."),
    (re.compile(r"aggregate .+ must appear in", re.I),
     "GROUP_BY_ERROR",
     "A column in SELECT is not in GROUP BY. Add it to GROUP BY or use an aggregate function."),
    (re.compile(r"division by zero", re.I),
     "DIVISION_BY_ZERO",
     "The query divides by zero. Use NULLIF(divisor, 0) or CASE WHEN to handle zero values."),
    (re.compile(r"operator does not exist", re.I),
     "TYPE_MISMATCH",
     "A type mismatch in an operation. Check column types and add explicit CASTs if needed."),
    (re.compile(r"ambiguous column name", re.I),
     "AMBIGUOUS_COLUMN",
     "A column name is ambiguous (exists in multiple tables). Use table aliases to qualify it."),
    (re.compile(r"timeout|canceling statement", re.I),
     "TIMEOUT",
     "The query timed out. Simplify the query, add LIMIT, or optimize JOINs."),
    (re.compile(r"too many rows", re.I),
     "TOO_MANY_ROWS",
     "The query returns too many rows. Add a LIMIT clause or narrow the WHERE conditions."),
]


def classify_error(error_message: str) -> tuple[str, str]:
    """
    Classify a database error into a category with guidance.

    Returns:
        Tuple of (error_type, human_readable_guidance).
    """
    for pattern, error_type, guidance in _ERROR_CATEGORIES:
        if pattern.search(error_message):
            return error_type, guidance

    return "UNKNOWN", f"Unexpected error: {error_message}. Review the SQL for correctness."


# ── Refinement prompt ────────────────────────────────────

_REFINE_SQL_SYSTEM = _IDENTITY + _AGENTIC_PREAMBLE + """CURRENT TASK: The previous SQL query failed with an error. Fix the query based on the error message and guidance.

REFINEMENT APPROACH:
1. Read the original query carefully.
2. Understand the error message and its cause.
3. Apply the specific fix described in the guidance.
4. Return ONLY the corrected SQL — no explanations, no markdown.
5. Ensure the corrected query still answers the original question.

IMPORTANT:
- Do NOT change the intent of the query — only fix the error.
- Use the same tables and logic, just correct the mistake.
- If the error is about a missing column, check the SCHEMA for the correct name.
- If the error is about GROUP BY, ensure all non-aggregated columns are grouped.

SCHEMA:
{schema}"""

_REFINE_SQL_USER = """ORIGINAL QUESTION: {question}

FAILED SQL:
{failed_sql}

ERROR: {error_message}

ERROR TYPE: {error_type}
GUIDANCE: {guidance}

{previous_attempts}

Generate the corrected SQL query:"""


async def refine_sql(
    question: str,
    failed_sql: str,
    error_message: str,
    schema_text: str,
    allowed_tables: list[str],
    department: str | None = None,
    previous_attempts: list[RefinementAttempt] | None = None,
) -> tuple[str, list[RefinementAttempt]]:
    """
    Attempt to refine a failed SQL query through iterative LLM correction.

    Args:
        question: The user's original question.
        failed_sql: The SQL that failed execution.
        error_message: The database error message.
        schema_text: The filtered schema for the user's role.
        allowed_tables: Tables the user is permitted to query.
        department: User's department for API key routing.
        previous_attempts: History of prior refinement attempts.

    Returns:
        Tuple of (corrected_sql, all_attempts).

    Raises:
        LLMClientError if the LLM fails to generate a refinement.
        SQLGuardrailError if the refined SQL fails guardrail checks.
    """
    attempts = list(previous_attempts or [])
    max_retries = settings.max_sql_retries
    current_sql = failed_sql
    current_error = error_message

    for attempt_num in range(1, max_retries + 1):
        error_type, guidance = classify_error(current_error)

        logger.info(
            "sql_refinement_attempt",
            attempt=attempt_num,
            max_retries=max_retries,
            error_type=error_type,
        )

        # Build previous attempts context
        prev_context = ""
        if attempts:
            prev_lines = ["PREVIOUS FAILED ATTEMPTS:"]
            for a in attempts[-3:]:  # Show last 3 attempts max
                prev_lines.append(f"  Attempt {a.attempt_number}: {a.error_type} — {a.error_message[:100]}")
            prev_context = "\n".join(prev_lines)

        messages = [
            {"role": "system", "content": _REFINE_SQL_SYSTEM.format(schema=schema_text)},
            {"role": "user", "content": _REFINE_SQL_USER.format(
                question=question,
                failed_sql=current_sql,
                error_message=current_error,
                error_type=error_type,
                guidance=guidance,
                previous_attempts=prev_context,
            )},
        ]

        try:
            refined_sql = await generate(messages, department=department)
        except LLMClientError:
            attempt = RefinementAttempt(
                attempt_number=attempt_num,
                original_sql=current_sql,
                error_message=current_error,
                error_type=error_type,
                success=False,
            )
            attempts.append(attempt)
            raise

        # Validate through guardrail
        try:
            validated_sql = validate(refined_sql, allowed_tables)
        except SQLGuardrailError as exc:
            attempt = RefinementAttempt(
                attempt_number=attempt_num,
                original_sql=current_sql,
                error_message=str(exc),
                error_type="GUARDRAIL_BLOCKED",
                refined_sql=refined_sql,
                success=False,
            )
            attempts.append(attempt)
            current_sql = refined_sql
            current_error = str(exc)
            continue

        # Success
        attempt = RefinementAttempt(
            attempt_number=attempt_num,
            original_sql=current_sql,
            error_message=current_error,
            error_type=error_type,
            refined_sql=validated_sql,
            success=True,
        )
        attempts.append(attempt)

        logger.info(
            "sql_refinement_success",
            attempt=attempt_num,
            error_type=error_type,
        )

        return validated_sql, attempts

    # All retries exhausted
    logger.warning(
        "sql_refinement_exhausted",
        total_attempts=len(attempts),
    )
    raise LLMClientError(
        f"SQL refinement failed after {max_retries} attempts. "
        f"Last error: {current_error}"
    )
