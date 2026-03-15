"""
Data Freshness Checker
======================

Queries the staging database for the most recent timestamps across
key tables to determine how current the data is. The staging DB
syncs from production every ~30 minutes, so users need to know
exactly how stale the data they're seeing might be.

Usage:
    from data_freshness import get_data_freshness
    info = await get_data_freshness()
    # {
    #   "status": "fresh",           # fresh | stale | unknown
    #   "last_updated": "2026-03-04T10:23:00Z",
    #   "minutes_ago": 12,
    #   "message": "Data last synced: 12 minutes ago",
    #   "table_details": { ... }
    # }
"""

from __future__ import annotations

import asyncio
import structlog
from datetime import datetime, timezone
from sqlalchemy import text

from db import engine

logger = structlog.get_logger(__name__)

# Tables to check for freshness — these are the most actively updated
# and give the best signal for "when did the staging DB last sync?"
# Format: (table_name, timestamp_column)
# Django uses app_model naming convention for SQLite dev mode.
_FRESHNESS_TABLES_PG = [
    ("sales",            "created_at"),
    ("attendance",       "created_at"),
    ("audit_log",        "created_at"),
    ("employees",        "updated_at"),
    ("leave_requests",   "created_at"),
    ("payroll",          "created_at"),
    ("expenses",         "created_at"),
]
_FRESHNESS_TABLES_SQLITE = [
    ("orders_order",       "created_at"),
    ("hr_attendance",      "date"),
    ("core_auditlog",      "timestamp"),
    ("hr_employee",        "created_at"),
    ("hr_leaverequest",    "created_at"),
    ("hr_payroll",         "created_at"),
    ("finance_invoice",    "created_at"),
]

from config import settings as _settings
_FRESHNESS_TABLES = _FRESHNESS_TABLES_SQLITE if _settings.is_sqlite else _FRESHNESS_TABLES_PG

# Thresholds (in minutes)
FRESH_THRESHOLD = 45       # < 45 min = "fresh"
STALE_THRESHOLD = 120      # 45–120 min = "slightly stale", > 120 = "stale"


async def _get_latest_timestamp(table: str, column: str) -> datetime | None:
    """Query a single table for its most recent timestamp."""
    try:
        async with asyncio.timeout(3):
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(f"SELECT MAX({column}) AS latest FROM {table}")  # noqa: S608
                )
                row = result.fetchone()
                val = row[0] if row and row[0] else None
                if val is None:
                    return None
                # SQLite returns strings; parse to datetime
                if isinstance(val, str):
                    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                        try:
                            return datetime.strptime(val, fmt)
                        except ValueError:
                            continue
                    return None
                return val
    except Exception as e:
        logger.warning("freshness_query_failed", table=table, error=str(e))
        return None


async def get_data_freshness() -> dict:
    """
    Query key staging-DB tables and return a freshness report.

    Returns a dict with:
      - status: "fresh" | "stale" | "unknown"
      - last_updated: ISO timestamp of most recent data
      - minutes_ago: how many minutes since the latest record
      - message: human-readable string like "Data last synced: 12 minutes ago"
      - table_details: per-table latest timestamps
    """
    now = datetime.now(timezone.utc)

    # Query all tables in parallel
    tasks = [
        _get_latest_timestamp(table, column)
        for table, column in _FRESHNESS_TABLES
    ]
    results = await asyncio.gather(*tasks)

    # Build per-table details
    table_details: dict[str, dict] = {}
    latest_overall: datetime | None = None

    for (table, column), ts in zip(_FRESHNESS_TABLES, results):
        if ts is not None:
            # Ensure timezone-aware
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            minutes = int((now - ts).total_seconds() / 60)
            table_details[table] = {
                "latest": ts.isoformat(),
                "minutes_ago": minutes,
                "column": column,
            }
            if latest_overall is None or ts > latest_overall:
                latest_overall = ts
        else:
            table_details[table] = {
                "latest": None,
                "minutes_ago": None,
                "column": column,
            }

    # Compute overall status
    if latest_overall is None:
        return {
            "status": "unknown",
            "last_updated": None,
            "minutes_ago": None,
            "message": "Unable to determine data freshness",
            "sync_interval_minutes": 30,
            "table_details": table_details,
        }

    minutes_ago = int((now - latest_overall).total_seconds() / 60)

    if minutes_ago < FRESH_THRESHOLD:
        status = "fresh"
    elif minutes_ago < STALE_THRESHOLD:
        status = "slightly_stale"
    else:
        status = "stale"

    # Human-readable message
    if minutes_ago < 1:
        message = "Data last synced: just now"
    elif minutes_ago < 60:
        message = f"Data last synced: {minutes_ago} minute{'s' if minutes_ago != 1 else ''} ago"
    elif minutes_ago < 1440:
        hours = minutes_ago // 60
        message = f"Data last synced: {hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = minutes_ago // 1440
        message = f"Data last synced: {days} day{'s' if days != 1 else ''} ago"

    return {
        "status": status,
        "last_updated": latest_overall.isoformat(),
        "minutes_ago": minutes_ago,
        "message": message,
        "sync_interval_minutes": 30,
        "table_details": table_details,
    }
