"""
DB Executor — runs validated SQL on the Staging Database
and returns rows as a list of dicts.
"""

from __future__ import annotations

import decimal
import datetime
import time
from typing import Any

from sqlalchemy import text

from db import engine


class DBExecutionError(Exception):
    """Raised when SQL execution fails on the Staging DB."""


def _sanitize_value(val: Any) -> Any:
    """Convert DB-native types to JSON-friendly Python types."""
    if isinstance(val, decimal.Decimal):
        return float(val)
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    return val


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _sanitize_value(v) for k, v in row.items()}


async def execute(sql: str) -> tuple[list[dict[str, Any]], float]:
    """
    Execute a SELECT query on the staging database.

    Args:
        sql: A validated SELECT statement.

    Returns:
        A tuple of (rows_as_dicts, execution_time_ms).

    Raises:
        DBExecutionError on any database error.
    """
    start = time.perf_counter()

    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql))
            columns = list(result.keys())
            rows = [_sanitize_row(dict(zip(columns, row))) for row in result.fetchall()]
    except Exception as exc:
        raise DBExecutionError(
            f"Staging DB query failed: {exc}"
        ) from exc

    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    return rows, elapsed_ms
