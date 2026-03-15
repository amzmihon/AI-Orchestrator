"""
SQL Guardrail — validates LLM-generated SQL before execution.

Ensures queries are:
  1. SELECT-only (no writes, no DDL)
  2. Free of injection patterns
  3. Referencing only allowed tables
"""

from __future__ import annotations

import re


class SQLGuardrailError(Exception):
    """Raised when generated SQL fails validation."""


# ── Blocked keywords (case-insensitive, whole-word) ──────
BLOCKED_KEYWORDS: list[str] = [
    "DROP",
    "DELETE",
    "UPDATE",
    "INSERT",
    "ALTER",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "CREATE",
    "EXEC",
    "EXECUTE",
    "MERGE",
    "REPLACE",
    "CALL",
]

# ── Suspicious patterns (regex) ──────────────────────────
BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE)", "SQL injection attempt (stacked statement)"),
    (r"--\s", "SQL comment (potential injection)"),
    (r"/\*.*?\*/", "Block comment (potential injection)"),
    (r"xp_cmdshell", "Dangerous SQL Server function"),
    (r"pg_sleep", "DoS via pg_sleep"),
    (r"INTO\s+OUTFILE", "File write attempt"),
    (r"LOAD_FILE", "File read attempt"),
    (r"INTO\s+DUMPFILE", "File dump attempt"),
    (r"INFORMATION_SCHEMA", "Schema enumeration attempt"),
    (r"pg_catalog", "System catalog access attempt"),
]

# Table-name extraction pattern: matches "FROM table" and "JOIN table"
_TABLE_REF_PATTERN = re.compile(
    r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)

# CTE alias extraction: WITH alias AS (...)
_CTE_ALIAS_PATTERN = re.compile(
    r"\bWITH\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(",
    re.IGNORECASE,
)
# Also match additional CTEs:  , alias AS (...)
_CTE_ADDITIONAL_PATTERN = re.compile(
    r",\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(",
    re.IGNORECASE,
)


def _clean_sql(raw: str) -> str:
    """
    Strip markdown code fences and whitespace that the LLM might wrap
    around the SQL.
    """
    sql = raw.strip()
    # Remove ```sql ... ``` fences
    if sql.startswith("```"):
        lines = sql.split("\n")
        # Drop first line (```sql) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        sql = "\n".join(lines).strip()
    return sql


def extract_cte_aliases(sql: str) -> set[str]:
    """Extract CTE alias names (WITH x AS ...) so they aren't flagged as real tables."""
    aliases = set()
    for m in _CTE_ALIAS_PATTERN.finditer(sql):
        aliases.add(m.group(1).lower())
    for m in _CTE_ADDITIONAL_PATTERN.finditer(sql):
        aliases.add(m.group(1).lower())
    return aliases


def extract_table_names(sql: str) -> list[str]:
    """Extract all table names referenced in FROM / JOIN clauses."""
    return [m.group(1).lower() for m in _TABLE_REF_PATTERN.finditer(sql)]


def validate(raw_sql: str, allowed_tables: list[str]) -> str:
    """
    Validate and clean LLM-generated SQL.

    Args:
        raw_sql: The raw SQL string from the LLM.
        allowed_tables: Tables the current user is permitted to query.

    Returns:
        The cleaned, validated SQL string.

    Raises:
        SQLGuardrailError if any validation check fails.
    """
    sql = _clean_sql(raw_sql)

    if not sql:
        raise SQLGuardrailError("LLM returned empty SQL")

    # ── 1. Must start with SELECT or WITH (CTEs) ────────
    first_keyword = sql.split()[0].upper()
    if first_keyword not in ("SELECT", "WITH"):
        raise SQLGuardrailError(
            f"Query must start with SELECT or WITH, got: {first_keyword}"
        )

    # ── 2. Block dangerous keywords ─────────────────────
    sql_upper = sql.upper()
    for keyword in BLOCKED_KEYWORDS:
        # Whole-word match to avoid false positives (e.g. "UPDATED_AT")
        pattern = rf"\b{keyword}\b"
        if re.search(pattern, sql_upper):
            raise SQLGuardrailError(
                f"Forbidden keyword detected: {keyword}"
            )

    # ── 3. Block suspicious patterns ────────────────────
    for pattern, reason in BLOCKED_PATTERNS:
        if re.search(pattern, sql, re.IGNORECASE | re.DOTALL):
            raise SQLGuardrailError(f"Blocked pattern: {reason}")

    # ── 4. Multiple statements (semicolons) ─────────────
    # Allow a trailing semicolon but reject multiple statements
    stripped = sql.rstrip(";").strip()
    if ";" in stripped:
        raise SQLGuardrailError(
            "Multiple SQL statements detected (possible injection)"
        )

    # ── 5. Table access check ───────────────────────────
    referenced_tables = extract_table_names(sql)
    cte_aliases = extract_cte_aliases(sql)
    allowed_set = {t.lower() for t in allowed_tables}

    # Don't flag CTE aliases or subquery aliases as forbidden tables
    forbidden = [
        t for t in referenced_tables
        if t not in allowed_set and t not in cte_aliases
    ]
    if forbidden:
        raise SQLGuardrailError(
            f"Access denied to table(s): {', '.join(forbidden)}"
        )

    return sql
