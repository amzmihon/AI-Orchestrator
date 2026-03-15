"""
Schema Loader — reads schema_map.json and returns filtered table
definitions based on the user's allowed tables.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from config import settings


@lru_cache(maxsize=1)
def _load_schema_map() -> dict[str, Any]:
    """Load and cache schema_map.json."""
    path = Path(settings.schema_map_path)
    if not path.is_absolute():
        path = Path(__file__).parent.parent / path
    with open(path) as f:
        return json.load(f)


def reload_schema() -> None:
    """Clear cached schema (call after editing schema_map.json)."""
    _load_schema_map.cache_clear()


def get_full_schema() -> dict[str, Any]:
    """Return the entire schema map."""
    return _load_schema_map()


def get_table_names() -> list[str]:
    """Return all table names in the schema."""
    schema = _load_schema_map()
    return list(schema.get("tables", {}).keys())


def get_filtered_schema(
    allowed_tables: list[str],
    denied_columns: dict[str, list[str]] | None = None,
) -> str:
    """
    Build a text description of the DB schema, filtered to only
    the tables and columns the current user is allowed to see.

    Returns a string suitable for embedding in an LLM system prompt.
    """
    schema = _load_schema_map()
    tables = schema.get("tables", {})
    denied = denied_columns or {}

    lines: list[str] = []
    lines.append(f"Database: {schema.get('database', 'staging')}")
    dialect_label = "SQLite" if settings.is_sqlite else "PostgreSQL"
    lines.append(f"Dialect: {dialect_label}")
    lines.append("")

    for table_name in allowed_tables:
        table_def = tables.get(table_name)
        if table_def is None:
            continue

        desc = table_def.get("description", "")
        lines.append(f"TABLE: {table_name}")
        if desc:
            lines.append(f"  -- {desc}")

        columns = table_def.get("columns", {})
        blocked = set(denied.get(table_name, []))

        for col_name, col_info in columns.items():
            if col_name in blocked:
                continue
            col_type = col_info.get("type", "unknown")
            col_desc = col_info.get("description", "")
            pk = " [PK]" if col_info.get("primary_key") else ""
            lines.append(f"  - {col_name} ({col_type}){pk}: {col_desc}")

        lines.append("")

    return "\n".join(lines)
