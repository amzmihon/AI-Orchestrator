"""
Role-Based Access Control (RBAC) — determines which tables and columns
a user may query based on their role from the Main App.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from config import settings


@lru_cache(maxsize=1)
def _load_role_config() -> dict[str, Any]:
    """Load and cache the role config JSON."""
    path = Path(__file__).parent / "role_config.json"
    if not path.exists():
        # Fall back to path from settings
        path = Path(settings.role_config_path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def reload_role_config() -> None:
    """Clear the cached config (useful after editing role_config.json)."""
    _load_role_config.cache_clear()


def get_all_known_tables() -> list[str]:
    """Return the master list of all tables the system knows about."""
    config = _load_role_config()
    return config.get("all_tables", [])


def get_allowed_tables(role: str) -> list[str]:
    """
    Return the list of table names this role is permitted to query.

    A wildcard ("*") means all known tables.
    Unknown roles get an empty list (no access).
    """
    config = _load_role_config()
    role_key = role.lower().replace(" ", "_")

    role_cfg = config.get("roles", {}).get(role_key)
    if role_cfg is None:
        return []

    tables = role_cfg.get("allowed_tables", [])
    if tables == ["*"]:
        return get_all_known_tables()

    return tables


def get_denied_columns(role: str) -> dict[str, list[str]]:
    """
    Return a mapping of table → list of denied column names for this role.

    Example return: {"employees": ["salary", "bank_account"]}
    """
    config = _load_role_config()
    role_key = role.lower().replace(" ", "_")

    role_cfg = config.get("roles", {}).get(role_key)
    if role_cfg is None:
        return {}

    return role_cfg.get("denied_columns", {})


def check_table_access(role: str, table_names: list[str]) -> list[str]:
    """
    Given a role and a list of table names referenced in a query,
    return any table names that are NOT allowed.

    Returns an empty list if all tables are permitted.
    """
    allowed = set(get_allowed_tables(role))
    return [t for t in table_names if t not in allowed]
