"""
Admin Dashboard Router — No-Code Management APIs.

Provides REST endpoints for the 12 admin dashboard modules:
  1. Connection Settings    — LLM / DB / Auth config
  2. Schema Map Editor      — CRUD on schema_map.json
  3. AI System Prompt       — View/edit system prompt & skills
  4. AI RBAC Matrix         — Role-to-table permission grid
  5. Intent & Pattern Mgr   — Manage intent trigger phrases
  6. Memory & Knowledge     — Browse/edit chat_history.db
  7. PII & Security         — Toggle PII detection, manage blocked topics
  8. Data Freshness         — Sync status & manual trigger
  9. All User Sessions      — Admin view of all chat sessions
 10. AI RAG DB              — RAG database management
 11. Admin Users            — Orchestrator admin user CRUD (via admin_auth)
 12. Logs                   — Application log viewer
"""

from __future__ import annotations

import json
import os
import re
import pathlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/dashboard", tags=["admin-dashboard"])

# ── Paths ────────────────────────────────────────────────
_BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
_SCHEMA_MAP_PATH = _BASE_DIR / settings.schema_map_path
_ROLE_CONFIG_PATH = _BASE_DIR / settings.role_config_path
_ENV_PATH = _BASE_DIR.parent / ".env"  # monorepo root
_CHAT_DB_PATH = _BASE_DIR / "chat_history.db"


def _read_json(path: pathlib.Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: pathlib.Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _parse_env_file(path: pathlib.Path) -> dict[str, str]:
    """Parse .env file into a dict (preserves comments/order is lost)."""
    env = {}
    if not path.exists():
        return env
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def _update_env_file(path: pathlib.Path, updates: dict[str, str]) -> None:
    """Update specific keys in the .env file, preserving comments/structure."""
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line if line.endswith("\n") else line + "\n")
    # Append any new keys not already in the file
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}\n")
    path.write_text("".join(new_lines), encoding="utf-8")


# ═════════════════════════════════════════════════════════
# 1. CONNECTION SETTINGS  (multi-provider/multi-db/multi-app)
# ═════════════════════════════════════════════════════════

_CONNECTIONS_PATH = _BASE_DIR / "connections.json"


def _read_connections() -> dict:
    """Load connections.json — bootstrap from .env if missing."""
    if _CONNECTIONS_PATH.exists():
        return _read_json(_CONNECTIONS_PATH)

    # Bootstrap default connections from current .env / settings
    env = _parse_env_file(_ENV_PATH)
    default = {
        "llm_providers": [
            {
                "id": "llm-default",
                "label": "Primary LLM",
                "provider": "groq",
                "base_url": settings.llm_base_url,
                "api_key": settings.llm_api_key,
                "model": settings.llm_model,
                "fast_model": settings.llm_fast_model,
                "temperature": settings.llm_temperature,
                "max_tokens": settings.llm_max_tokens,
                "context_window": settings.llm_context_window,
                "purpose": "General SQL + Summarisation",
                "is_active": True,
            }
        ],
        "databases": [
            {
                "id": "db-default",
                "label": "Main ERP Database",
                "engine": "sqlite" if settings.is_sqlite else "postgresql",
                "url_override": settings.staging_db_url_override if settings.is_sqlite else "",
                "host": settings.staging_db_host,
                "port": settings.staging_db_port,
                "name": settings.staging_db_name,
                "user": settings.staging_db_user,
                "password": env.get("ATL_STAGING_DB_PASSWORD", settings.staging_db_password),
                "purpose": "Primary ERP staging data",
                "is_active": True,
            }
        ],
        "applications": [
            {
                "id": "app-default",
                "label": "ERP-BGS Django App",
                "base_url": settings.main_app_base_url,
                "auth_mode": settings.auth_mode,
                "jwt_secret": settings.jwt_secret_or_public_key,
                "jwt_algorithm": settings.jwt_algorithm,
                "jwt_issuer": settings.jwt_issuer,
                "jwt_audience": settings.jwt_audience,
                "token_cache_ttl": settings.token_cache_ttl_seconds,
                "is_active": True,
            }
        ],
    }
    _write_json(_CONNECTIONS_PATH, default)
    return default


def _save_connections(data: dict) -> None:
    _write_json(_CONNECTIONS_PATH, data)


def _mask_key(key: str) -> str:
    """Return masked version of a secret key."""
    if not key or key in ("sk-change-me", ""):
        return ""
    if len(key) <= 10:
        return key[:2] + "•" * (len(key) - 2)
    return key[:6] + "•" * (len(key) - 10) + key[-4:]


def _sync_active_to_env(conns: dict) -> None:
    """Write the active LLM/DB/App settings back to .env."""
    updates = {}
    # Active LLM
    active_llm = next((c for c in conns.get("llm_providers", []) if c.get("is_active")), None)
    if active_llm:
        updates["ATL_LLM_BASE_URL"] = active_llm["base_url"]
        updates["ATL_LLM_API_KEY"] = active_llm["api_key"]
        updates["ATL_LLM_MODEL"] = active_llm["model"]
        updates["ATL_LLM_FAST_MODEL"] = active_llm.get("fast_model", "")
        updates["ATL_LLM_TEMPERATURE"] = str(active_llm.get("temperature", 0.1))
        updates["ATL_LLM_MAX_TOKENS"] = str(active_llm.get("max_tokens", 8192))
    # Active DB
    active_db = next((c for c in conns.get("databases", []) if c.get("is_active")), None)
    if active_db:
        if active_db.get("engine") == "sqlite" and active_db.get("url_override"):
            updates["ATL_STAGING_DB_URL_OVERRIDE"] = active_db["url_override"]
        else:
            updates["ATL_STAGING_DB_URL_OVERRIDE"] = ""
            updates["ATL_STAGING_DB_HOST"] = active_db.get("host", "localhost")
            updates["ATL_STAGING_DB_PORT"] = str(active_db.get("port", 5432))
            updates["ATL_STAGING_DB_NAME"] = active_db.get("name", "staging")
            updates["ATL_STAGING_DB_USER"] = active_db.get("user", "ai_reader")
            updates["ATL_STAGING_DB_PASSWORD"] = active_db.get("password", "changeme")
    # Active App
    active_app = next((c for c in conns.get("applications", []) if c.get("is_active")), None)
    if active_app:
        updates["ATL_AUTH_MODE"] = active_app.get("auth_mode", "jwt")
        updates["ATL_JWT_SECRET_OR_PUBLIC_KEY"] = active_app.get("jwt_secret", "")
        updates["AI_JWT_SECRET"] = active_app.get("jwt_secret", "")
        updates["ATL_MAIN_APP_BASE_URL"] = active_app.get("base_url", "")
    if updates:
        _update_env_file(_ENV_PATH, updates)


@router.get("/connections")
async def get_connections():
    """Return all connection configs (secrets masked)."""
    conns = _read_connections()
    # Deep-copy and mask secrets for GET response
    safe = json.loads(json.dumps(conns))
    for llm in safe.get("llm_providers", []):
        llm["api_key_preview"] = _mask_key(llm.get("api_key", ""))
        llm["api_key_set"] = bool(llm.get("api_key") and llm["api_key"] != "sk-change-me")
        del llm["api_key"]
    for db in safe.get("databases", []):
        if db.get("password"):
            db["password_set"] = True
            del db["password"]
        else:
            db["password_set"] = False
    for app in safe.get("applications", []):
        app["jwt_secret_preview"] = _mask_key(app.get("jwt_secret", ""))
        app["jwt_secret_set"] = bool(app.get("jwt_secret"))
        del app["jwt_secret"]
    return safe


def _gen_id(prefix: str, items: list) -> str:
    """Generate a unique ID for a new connection entry."""
    existing = {c["id"] for c in items}
    i = 1
    while f"{prefix}-{i}" in existing:
        i += 1
    return f"{prefix}-{i}"


@router.post("/connections/{conn_type}")
async def add_connection(conn_type: str, request: Request):
    """Add a new LLM / database / application connection."""
    if conn_type not in ("llm_providers", "databases", "applications"):
        raise HTTPException(400, "conn_type must be llm_providers, databases, or applications")
    body = await request.json()
    conns = _read_connections()
    prefix = {"llm_providers": "llm", "databases": "db", "applications": "app"}[conn_type]
    body["id"] = _gen_id(prefix, conns[conn_type])
    body.setdefault("is_active", False)
    conns[conn_type].append(body)
    _save_connections(conns)
    return {"status": "created", "id": body["id"]}


@router.put("/connections/{conn_type}/{conn_id}")
async def update_connection(conn_type: str, conn_id: str, request: Request):
    """Update an existing connection entry."""
    if conn_type not in ("llm_providers", "databases", "applications"):
        raise HTTPException(400, "Invalid conn_type")
    body = await request.json()
    conns = _read_connections()
    items = conns[conn_type]
    idx = next((i for i, c in enumerate(items) if c["id"] == conn_id), None)
    if idx is None:
        raise HTTPException(404, f"Connection '{conn_id}' not found")
    # Preserve secrets if not supplied
    if conn_type == "llm_providers" and not body.get("api_key"):
        body["api_key"] = items[idx].get("api_key", "")
    if conn_type == "databases" and not body.get("password"):
        body["password"] = items[idx].get("password", "")
    if conn_type == "applications" and not body.get("jwt_secret"):
        body["jwt_secret"] = items[idx].get("jwt_secret", "")
    body["id"] = conn_id
    items[idx] = body
    _save_connections(conns)
    _sync_active_to_env(conns)
    return {"status": "updated", "id": conn_id,
            "note": "Restart the orchestrator to apply changes."}


@router.post("/connections/{conn_type}/{conn_id}/activate")
async def activate_connection(conn_type: str, conn_id: str):
    """Set a connection as the active one (deactivates others of same type)."""
    if conn_type not in ("llm_providers", "databases", "applications"):
        raise HTTPException(400, "Invalid conn_type")
    conns = _read_connections()
    items = conns[conn_type]
    found = False
    for c in items:
        if c["id"] == conn_id:
            c["is_active"] = True
            found = True
        else:
            c["is_active"] = False
    if not found:
        raise HTTPException(404, f"Connection '{conn_id}' not found")
    _save_connections(conns)
    _sync_active_to_env(conns)
    return {"status": "activated", "id": conn_id,
            "note": "Restart the orchestrator to apply changes."}


@router.delete("/connections/{conn_type}/{conn_id}")
async def delete_connection(conn_type: str, conn_id: str):
    """Delete a connection entry (cannot delete the last or the active one)."""
    if conn_type not in ("llm_providers", "databases", "applications"):
        raise HTTPException(400, "Invalid conn_type")
    conns = _read_connections()
    items = conns[conn_type]
    idx = next((i for i, c in enumerate(items) if c["id"] == conn_id), None)
    if idx is None:
        raise HTTPException(404, f"Connection '{conn_id}' not found")
    if items[idx].get("is_active"):
        raise HTTPException(400, "Cannot delete the active connection. Activate another first.")
    if len(items) <= 1:
        raise HTTPException(400, "Cannot delete the last connection.")
    items.pop(idx)
    _save_connections(conns)
    return {"status": "deleted", "id": conn_id}


# Legacy single-section PUT endpoints (backwards compatible)
@router.put("/connections/llm")
async def update_llm_connection(request: Request):
    """Update LLM connection settings in .env file."""
    body = await request.json()
    updates = {}
    if "base_url" in body:
        updates["ATL_LLM_BASE_URL"] = body["base_url"]
    if "api_key" in body:
        updates["ATL_LLM_API_KEY"] = body["api_key"]
    if "model" in body:
        updates["ATL_LLM_MODEL"] = body["model"]
    if "fast_model" in body:
        updates["ATL_LLM_FAST_MODEL"] = body["fast_model"]
    if "temperature" in body:
        updates["ATL_LLM_TEMPERATURE"] = str(body["temperature"])
    if "max_tokens" in body:
        updates["ATL_LLM_MAX_TOKENS"] = str(body["max_tokens"])
    if not updates:
        raise HTTPException(400, "No fields to update")
    _update_env_file(_ENV_PATH, updates)
    return {"status": "updated", "fields": list(updates.keys()),
            "note": "Restart the orchestrator to apply changes."}


@router.put("/connections/database")
async def update_db_connection(request: Request):
    """Update staging database settings in .env file."""
    body = await request.json()
    updates = {}
    if "url_override" in body:
        updates["ATL_STAGING_DB_URL_OVERRIDE"] = body["url_override"]
    else:
        if "host" in body:
            updates["ATL_STAGING_DB_HOST"] = body["host"]
        if "port" in body:
            updates["ATL_STAGING_DB_PORT"] = str(body["port"])
        if "name" in body:
            updates["ATL_STAGING_DB_NAME"] = body["name"]
        if "user" in body:
            updates["ATL_STAGING_DB_USER"] = body["user"]
        if "password" in body:
            updates["ATL_STAGING_DB_PASSWORD"] = body["password"]
    if not updates:
        raise HTTPException(400, "No fields to update")
    _update_env_file(_ENV_PATH, updates)
    return {"status": "updated", "fields": list(updates.keys()),
            "note": "Restart the orchestrator to apply changes."}


@router.put("/connections/auth")
async def update_auth_connection(request: Request):
    """Update authentication settings in .env file."""
    body = await request.json()
    updates = {}
    if "mode" in body:
        if body["mode"] not in ("jwt", "http", "hybrid"):
            raise HTTPException(400, "auth_mode must be jwt, http, or hybrid")
        updates["ATL_AUTH_MODE"] = body["mode"]
    if "jwt_secret" in body:
        updates["ATL_JWT_SECRET_OR_PUBLIC_KEY"] = body["jwt_secret"]
        updates["AI_JWT_SECRET"] = body["jwt_secret"]
    if "main_app_url" in body:
        updates["ATL_MAIN_APP_BASE_URL"] = body["main_app_url"]
    if not updates:
        raise HTTPException(400, "No fields to update")
    _update_env_file(_ENV_PATH, updates)
    return {"status": "updated", "fields": list(updates.keys()),
            "note": "Restart the orchestrator to apply changes."}


# ═════════════════════════════════════════════════════════
# 2. SCHEMA MAP EDITOR
# ═════════════════════════════════════════════════════════

@router.get("/schema")
async def get_schema():
    """Return the full schema map."""
    data = _read_json(_SCHEMA_MAP_PATH)
    tables = data.get("tables", {})
    summary = []
    for name, info in tables.items():
        cols = info.get("columns", {})
        summary.append({
            "name": name,
            "description": info.get("description", ""),
            "column_count": len(cols),
        })
    return {"database": data.get("database", ""), "dialect": data.get("dialect", ""),
            "table_count": len(tables), "tables": summary}


@router.get("/schema/{table_name}")
async def get_schema_table(table_name: str):
    """Return schema details for a single table."""
    data = _read_json(_SCHEMA_MAP_PATH)
    table = data.get("tables", {}).get(table_name)
    if not table:
        raise HTTPException(404, f"Table '{table_name}' not found in schema map")
    return {"name": table_name, **table}


@router.put("/schema/{table_name}")
async def update_schema_table(table_name: str, request: Request):
    """Update description and column descriptions for a table."""
    body = await request.json()
    data = _read_json(_SCHEMA_MAP_PATH)
    tables = data.get("tables", {})
    if table_name not in tables:
        raise HTTPException(404, f"Table '{table_name}' not found")

    if "description" in body:
        tables[table_name]["description"] = body["description"]

    if "columns" in body and isinstance(body["columns"], dict):
        existing_cols = tables[table_name].get("columns", {})
        for col_name, col_desc in body["columns"].items():
            if col_name in existing_cols:
                existing_cols[col_name] = col_desc

    _write_json(_SCHEMA_MAP_PATH, data)
    return {"status": "updated", "table": table_name}


@router.post("/schema/{table_name}/columns/{column_name}")
async def update_schema_column(table_name: str, column_name: str, request: Request):
    """Update a single column description."""
    body = await request.json()
    data = _read_json(_SCHEMA_MAP_PATH)
    table = data.get("tables", {}).get(table_name)
    if not table:
        raise HTTPException(404, f"Table '{table_name}' not found")
    cols = table.get("columns", {})
    if column_name not in cols:
        raise HTTPException(404, f"Column '{column_name}' not found in '{table_name}'")
    cols[column_name] = body.get("description", cols[column_name])
    _write_json(_SCHEMA_MAP_PATH, data)
    return {"status": "updated", "table": table_name, "column": column_name}


@router.post("/schema/{table_name}/ai-generate")
async def ai_generate_table_description(table_name: str):
    """Use LLM to auto-generate a table description and column descriptions."""
    from llm_client import generate, LLMClientError

    data = _read_json(_SCHEMA_MAP_PATH)
    table = data.get("tables", {}).get(table_name)
    if not table:
        raise HTTPException(404, f"Table '{table_name}' not found in schema map")

    columns = table.get("columns", {})
    col_lines = []
    for cname, cinfo in columns.items():
        if isinstance(cinfo, dict):
            col_lines.append(f"  - {cname} ({cinfo.get('type', 'unknown')})")
        else:
            col_lines.append(f"  - {cname}")

    prompt = (
        f"You are a database documentation expert for a garment/apparel ERP system.\n"
        f"Given the table name and its columns, write:\n"
        f"1. A concise table description (1-2 sentences, max 200 chars)\n"
        f"2. A short description for each column (max 80 chars each)\n\n"
        f"Table: {table_name}\n"
        f"Columns:\n" + "\n".join(col_lines) + "\n\n"
        f"Respond in valid JSON only, no markdown:\n"
        f'{{"description": "...", "columns": {{"col_name": "description", ...}}}}'
    )

    try:
        raw = await generate([{"role": "user", "content": prompt}])
        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        result = json.loads(text)
    except LLMClientError as e:
        raise HTTPException(502, f"LLM error: {e}")
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(502, f"LLM returned invalid JSON: {raw[:500]}")

    # Save the generated descriptions
    if "description" in result:
        data["tables"][table_name]["description"] = result["description"]
    if "columns" in result and isinstance(result["columns"], dict):
        existing_cols = data["tables"][table_name].get("columns", {})
        for col_name, col_desc in result["columns"].items():
            if col_name in existing_cols:
                if isinstance(existing_cols[col_name], dict):
                    existing_cols[col_name]["description"] = col_desc
                else:
                    existing_cols[col_name] = col_desc

    _write_json(_SCHEMA_MAP_PATH, data)
    return {
        "status": "generated",
        "table": table_name,
        "description": result.get("description", ""),
        "columns": result.get("columns", {}),
    }


@router.post("/schema/ai-generate-all")
async def ai_generate_all_descriptions():
    """Use LLM to auto-generate descriptions for all tables missing descriptions."""
    from llm_client import generate, LLMClientError

    data = _read_json(_SCHEMA_MAP_PATH)
    tables = data.get("tables", {})

    # Build a summary of ALL tables for context
    table_summaries = []
    for tname, tinfo in tables.items():
        columns = tinfo.get("columns", {})
        col_names = list(columns.keys())[:8]
        extra = f" +{len(columns) - 8} more" if len(columns) > 8 else ""
        table_summaries.append(f"  - {tname}: columns=[{', '.join(col_names)}{extra}]")

    prompt = (
        f"You are a database documentation expert for a garment/apparel ERP system (BGS).\n"
        f"This ERP handles: orders, styles, buyers, production (cutting/sewing/finishing),\n"
        f"inventory, finance, HR, logistics, and quality control.\n\n"
        f"Generate a short description (1-2 sentences, max 200 chars) for each table below.\n\n"
        f"Tables:\n" + "\n".join(table_summaries) + "\n\n"
        f"Respond in valid JSON only, no markdown:\n"
        f'{{"tables": {{"table_name": "description", ...}}}}'
    )

    try:
        raw = await generate([{"role": "user", "content": prompt}])
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        result = json.loads(text)
    except LLMClientError as e:
        raise HTTPException(502, f"LLM error: {e}")
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(502, f"LLM returned invalid JSON: {raw[:500]}")

    generated = result.get("tables", {})
    updated_count = 0
    for tname, desc in generated.items():
        if tname in tables and isinstance(desc, str):
            tables[tname]["description"] = desc
            updated_count += 1

    _write_json(_SCHEMA_MAP_PATH, data)
    return {
        "status": "generated",
        "updated": updated_count,
        "total": len(tables),
        "descriptions": generated,
    }


# ═════════════════════════════════════════════════════════
# 3. AI RBAC MATRIX
# ═════════════════════════════════════════════════════════

@router.get("/rbac")
async def get_rbac():
    """Return the full role configuration."""
    data = _read_json(_ROLE_CONFIG_PATH)
    schema = _read_json(_SCHEMA_MAP_PATH)
    all_tables = sorted(schema.get("tables", {}).keys())
    roles = {}
    for role_name, role_info in data.get("roles", {}).items():
        allowed = role_info.get("allowed_tables", [])
        is_all = "*" in allowed
        roles[role_name] = {
            "description": role_info.get("description", ""),
            "allowed_tables": all_tables if is_all else allowed,
            "all_access": is_all,
            "denied_columns": role_info.get("denied_columns", {}),
        }
    return {"all_tables": all_tables, "roles": roles}


@router.put("/rbac/{role_name}")
async def update_rbac_role(role_name: str, request: Request):
    """Update allowed tables and denied columns for a role."""
    body = await request.json()
    data = _read_json(_ROLE_CONFIG_PATH)
    roles = data.get("roles", {})
    if role_name not in roles:
        raise HTTPException(404, f"Role '{role_name}' not found")

    if "allowed_tables" in body:
        roles[role_name]["allowed_tables"] = body["allowed_tables"]
    if "denied_columns" in body:
        roles[role_name]["denied_columns"] = body["denied_columns"]
    if "description" in body:
        roles[role_name]["description"] = body["description"]
    if "all_access" in body:
        if body["all_access"]:
            roles[role_name]["allowed_tables"] = ["*"]
        # if turning off all_access, convert to explicit list
        elif "*" in roles[role_name].get("allowed_tables", []):
            schema = _read_json(_SCHEMA_MAP_PATH)
            roles[role_name]["allowed_tables"] = sorted(schema.get("tables", {}).keys())

    _write_json(_ROLE_CONFIG_PATH, data)
    return {"status": "updated", "role": role_name}


@router.post("/rbac/{role_name}/toggle-table")
async def toggle_rbac_table(role_name: str, request: Request):
    """Toggle a single table's access for a role."""
    body = await request.json()
    table_name = body.get("table")
    if not table_name:
        raise HTTPException(400, "table is required")

    data = _read_json(_ROLE_CONFIG_PATH)
    roles = data.get("roles", {})
    if role_name not in roles:
        raise HTTPException(404, f"Role '{role_name}' not found")

    allowed = roles[role_name].get("allowed_tables", [])
    was_wildcard = "*" in allowed

    # If wildcard, expand to explicit table list first
    if was_wildcard:
        schema = _read_json(_SCHEMA_MAP_PATH)
        allowed = sorted(schema.get("tables", {}).keys())

    if table_name in allowed:
        allowed.remove(table_name)
        action = "removed"
    else:
        allowed.append(table_name)
        action = "added"

    # If all tables are now selected, restore wildcard
    schema = _read_json(_SCHEMA_MAP_PATH)
    all_tables = sorted(schema.get("tables", {}).keys())
    if sorted(allowed) == all_tables:
        allowed = ["*"]

    roles[role_name]["allowed_tables"] = allowed
    _write_json(_ROLE_CONFIG_PATH, data)
    return {"status": action, "role": role_name, "table": table_name,
            "all_access": "*" in allowed}


# ═════════════════════════════════════════════════════════
# 4. INTENT & PATTERN MANAGER
# ═════════════════════════════════════════════════════════

# We store custom patterns in a JSON file alongside the built-in ones
_CUSTOM_PATTERNS_PATH = _BASE_DIR / "custom_intent_patterns.json"


def _load_custom_patterns() -> dict:
    if _CUSTOM_PATTERNS_PATH.exists():
        return _read_json(_CUSTOM_PATTERNS_PATH)
    return {"text_processing": [], "data_query": [], "multi_step_analysis": []}


def _save_custom_patterns(data: dict) -> None:
    _write_json(_CUSTOM_PATTERNS_PATH, data)


@router.get("/intents")
async def get_intents():
    """Return built-in and custom intent patterns."""
    from query_engine.intent_classifier import _TEXT_PATTERNS, _DATA_PATTERNS, _MULTI_STEP_PATTERNS

    builtin = {
        "text_processing": [
            {"pattern": p.pattern, "reason": r, "source": "built-in"}
            for p, r in _TEXT_PATTERNS
        ],
        "data_query": [
            {"pattern": p.pattern, "reason": r, "tables": t, "source": "built-in"}
            for p, r, t in _DATA_PATTERNS
        ],
        "multi_step_analysis": [
            {"pattern": p.pattern, "reason": r, "tables": t, "source": "built-in"}
            for p, r, t in _MULTI_STEP_PATTERNS
        ],
    }

    custom = _load_custom_patterns()

    # Merge custom into the response
    for intent_type in builtin:
        for cp in custom.get(intent_type, []):
            cp["source"] = "custom"
            builtin[intent_type].append(cp)

    return {
        "intents": builtin,
        "counts": {k: len(v) for k, v in builtin.items()},
    }


@router.post("/intents/{intent_type}")
async def add_intent_pattern(intent_type: str, request: Request):
    """Add a custom trigger phrase/pattern to an intent category."""
    if intent_type not in ("text_processing", "data_query", "multi_step_analysis"):
        raise HTTPException(400, "intent_type must be text_processing, data_query, or multi_step_analysis")

    body = await request.json()
    pattern = body.get("pattern", "").strip()
    reason = body.get("reason", "Custom pattern").strip()
    tables = body.get("tables", [])

    if not pattern:
        raise HTTPException(400, "pattern is required")

    # Validate the regex is compilable
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise HTTPException(400, f"Invalid regex pattern: {e}")

    custom = _load_custom_patterns()
    entry = {"pattern": pattern, "reason": reason}
    if intent_type in ("data_query", "multi_step_analysis"):
        entry["tables"] = tables
    custom[intent_type].append(entry)
    _save_custom_patterns(custom)

    return {"status": "added", "intent_type": intent_type, "pattern": pattern}


@router.delete("/intents/{intent_type}/{index}")
async def delete_intent_pattern(intent_type: str, index: int):
    """Delete a custom pattern by index."""
    if intent_type not in ("text_processing", "data_query", "multi_step_analysis"):
        raise HTTPException(400, "Invalid intent_type")

    custom = _load_custom_patterns()
    patterns = custom.get(intent_type, [])
    if index < 0 or index >= len(patterns):
        raise HTTPException(404, f"Pattern index {index} out of range")

    removed = patterns.pop(index)
    _save_custom_patterns(custom)
    return {"status": "deleted", "removed": removed}


@router.post("/intents/test")
async def test_intent_classification(request: Request):
    """Test how a question would be classified."""
    body = await request.json()
    question = body.get("question", "").strip()
    if not question:
        raise HTTPException(400, "question is required")

    from query_engine.intent_classifier import classify
    result = classify(question)
    return {
        "question": question,
        "intent": result.intent.value,
        "confidence": result.confidence,
        "reasoning": result.reasoning,
        "sub_tasks": result.sub_tasks,
        "suggested_tables": result.suggested_tables,
        "complexity_score": result.complexity_score,
    }


# ═════════════════════════════════════════════════════════
# 5. MEMORY & KNOWLEDGE BROWSER
# ═════════════════════════════════════════════════════════

@router.get("/memories")
async def list_memories(user_id: str = "", limit: int = 50, offset: int = 0):
    """List long-term memories, optionally filtered by user_id."""
    import aiosqlite
    db = await aiosqlite.connect(str(_CHAT_DB_PATH))
    db.row_factory = aiosqlite.Row
    try:
        if user_id:
            cursor = await db.execute(
                "SELECT * FROM user_memories WHERE user_id = ? ORDER BY importance DESC, created_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM user_memories ORDER BY importance DESC, created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = await cursor.fetchall()
        # Get total count
        if user_id:
            cnt = await db.execute("SELECT COUNT(*) FROM user_memories WHERE user_id = ?", (user_id,))
        else:
            cnt = await db.execute("SELECT COUNT(*) FROM user_memories")
        total = (await cnt.fetchone())[0]

        return {
            "total": total,
            "memories": [dict(r) for r in rows],
        }
    finally:
        await db.close()


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a specific long-term memory."""
    import aiosqlite
    db = await aiosqlite.connect(str(_CHAT_DB_PATH))
    try:
        cursor = await db.execute("DELETE FROM user_memories WHERE id = ?", (memory_id,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Memory not found")
        return {"status": "deleted", "id": memory_id}
    finally:
        await db.close()


@router.put("/memories/{memory_id}")
async def update_memory(memory_id: str, request: Request):
    """Update a memory's content/importance."""
    body = await request.json()
    import aiosqlite
    db = await aiosqlite.connect(str(_CHAT_DB_PATH))
    try:
        sets = []
        params = []
        if "content" in body:
            sets.append("content = ?")
            params.append(body["content"])
        if "importance" in body:
            sets.append("importance = ?")
            params.append(float(body["importance"]))
        if "category" in body:
            sets.append("category = ?")
            params.append(body["category"])
        if not sets:
            raise HTTPException(400, "No fields to update")
        params.append(memory_id)
        cursor = await db.execute(
            f"UPDATE user_memories SET {', '.join(sets)} WHERE id = ?", params
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Memory not found")
        return {"status": "updated", "id": memory_id}
    finally:
        await db.close()


@router.get("/episodic")
async def list_episodic_memories(user_id: str = "", limit: int = 50):
    """List episodic (reasoning chain) memories."""
    import aiosqlite
    db = await aiosqlite.connect(str(_CHAT_DB_PATH))
    db.row_factory = aiosqlite.Row
    try:
        if user_id:
            cursor = await db.execute(
                "SELECT * FROM episodic_memories WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM episodic_memories ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        results = []
        for r in rows:
            entry = dict(r)
            entry["execution_success"] = bool(entry.get("execution_success", 1))
            results.append(entry)
        return {"episodes": results}
    finally:
        await db.close()


@router.delete("/episodic/{episode_id}")
async def delete_episodic(episode_id: str):
    """Delete an episodic memory entry."""
    import aiosqlite
    db = await aiosqlite.connect(str(_CHAT_DB_PATH))
    try:
        cursor = await db.execute("DELETE FROM episodic_memories WHERE id = ?", (episode_id,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Episode not found")
        return {"status": "deleted", "id": episode_id}
    finally:
        await db.close()


# ═════════════════════════════════════════════════════════
# 6. PII & SECURITY CONTROLS
# ═════════════════════════════════════════════════════════

# PII settings stored in a JSON file for runtime toggling
_PII_CONFIG_PATH = _BASE_DIR / "pii_config.json"


def _load_pii_config() -> dict:
    if _PII_CONFIG_PATH.exists():
        return _read_json(_PII_CONFIG_PATH)
    # Default: all enabled
    return {
        "enabled": True,
        "types": {
            "EMAIL": True,
            "PHONE": True,
            "SSN": True,
            "CREDIT_CARD": True,
            "IP_ADDRESS": True,
            "DATE_OF_BIRTH": True,
            "NATIONAL_ID": True,
        },
        "action": "redact",
        "custom_blocked_topics": [],
    }


def _save_pii_config(data: dict) -> None:
    _write_json(_PII_CONFIG_PATH, data)


@router.get("/pii")
async def get_pii_config():
    """Return current PII detection configuration."""
    config = _load_pii_config()

    # Also return built-in blocked topics
    from query_engine.pii_detector import _BLOCKED_TOPIC_PATTERNS
    builtin_topics = [
        {"name": name, "pattern": pat.pattern, "source": "built-in"}
        for name, pat in _BLOCKED_TOPIC_PATTERNS
    ]

    config["builtin_blocked_topics"] = builtin_topics
    return config


@router.put("/pii")
async def update_pii_config(request: Request):
    """Update PII detection toggles."""
    body = await request.json()
    config = _load_pii_config()

    if "enabled" in body:
        config["enabled"] = bool(body["enabled"])
    if "action" in body:
        if body["action"] not in ("redact", "block", "warn"):
            raise HTTPException(400, "action must be redact, block, or warn")
        config["action"] = body["action"]
    if "types" in body and isinstance(body["types"], dict):
        for pii_type, enabled in body["types"].items():
            if pii_type in config["types"]:
                config["types"][pii_type] = bool(enabled)

    _save_pii_config(config)
    return {"status": "updated", "config": config}


@router.post("/pii/blocked-topics")
async def add_blocked_topic(request: Request):
    """Add a custom blocked topic."""
    body = await request.json()
    name = body.get("name", "").strip()
    pattern = body.get("pattern", "").strip()
    if not name or not pattern:
        raise HTTPException(400, "name and pattern are required")

    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise HTTPException(400, f"Invalid regex: {e}")

    config = _load_pii_config()
    config["custom_blocked_topics"].append({"name": name, "pattern": pattern})
    _save_pii_config(config)
    return {"status": "added", "name": name}


@router.delete("/pii/blocked-topics/{index}")
async def delete_blocked_topic(index: int):
    """Delete a custom blocked topic by index."""
    config = _load_pii_config()
    topics = config.get("custom_blocked_topics", [])
    if index < 0 or index >= len(topics):
        raise HTTPException(404, "Topic index out of range")
    removed = topics.pop(index)
    _save_pii_config(config)
    return {"status": "deleted", "removed": removed}


@router.post("/pii/test")
async def test_pii_detection(request: Request):
    """Test PII detection on sample text."""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(400, "text is required")

    from query_engine.pii_detector import detect_pii, detect_blocked_topics, redact_pii
    pii_matches = detect_pii(text)
    blocked = detect_blocked_topics(text)
    redacted = redact_pii(text, pii_matches)

    return {
        "original": text,
        "redacted": redacted,
        "pii_found": [{"type": m.pii_type, "value": m.value} for m in pii_matches],
        "blocked_topics": [{"name": t[0], "match": t[1]} for t in blocked],
    }


# ═════════════════════════════════════════════════════════
# 7. DATA FRESHNESS & SYNC MONITOR
# ═════════════════════════════════════════════════════════

@router.get("/freshness")
async def get_freshness():
    """Return data freshness + schema-map coverage report.

    Merges the sync-freshness data with a full table inventory so the
    front-end can show coverage cards and per-table details.
    Also checks the sync log so Force Sync updates the "last synced" time.
    """
    import asyncio
    from data_freshness import get_data_freshness
    from db import engine

    # 1. Base freshness report (status, last_updated, table_details …)
    freshness = await get_data_freshness()

    # 1b. Check sync log — if a recent force sync was done, use that timestamp
    #     when it's newer than the data-derived timestamp.
    sync_log = _read_sync_log()
    if sync_log:
        latest_sync = sync_log[0]  # most recent entry (list is newest-first)
        sync_ts_str = latest_sync.get("timestamp")
        if sync_ts_str:
            try:
                sync_ts = datetime.fromisoformat(sync_ts_str)
                if sync_ts.tzinfo is None:
                    sync_ts = sync_ts.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                data_ts = None
                if freshness.get("last_updated"):
                    data_ts = datetime.fromisoformat(freshness["last_updated"])
                    if data_ts.tzinfo is None:
                        data_ts = data_ts.replace(tzinfo=timezone.utc)

                # Use the more recent of data timestamp vs sync timestamp
                if data_ts is None or sync_ts > data_ts:
                    minutes_ago = int((now - sync_ts).total_seconds() / 60)
                    freshness["last_updated"] = sync_ts.isoformat()
                    freshness["minutes_ago"] = minutes_ago
                    # Recompute status and message
                    if minutes_ago < 45:
                        freshness["status"] = "fresh"
                    elif minutes_ago < 120:
                        freshness["status"] = "slightly_stale"
                    else:
                        freshness["status"] = "stale"
                    if minutes_ago < 1:
                        freshness["message"] = "Data last synced: just now"
                    elif minutes_ago < 60:
                        freshness["message"] = f"Data last synced: {minutes_ago} minute{'s' if minutes_ago != 1 else ''} ago"
                    elif minutes_ago < 1440:
                        hours = minutes_ago // 60
                        freshness["message"] = f"Data last synced: {hours} hour{'s' if hours != 1 else ''} ago"
                    else:
                        days = minutes_ago // 1440
                        freshness["message"] = f"Data last synced: {days} day{'s' if days != 1 else ''} ago"
            except (ValueError, TypeError):
                pass  # ignore malformed timestamps

    # 2. Schema map — which tables are mapped and with what descriptions
    try:
        schema = _read_json(_SCHEMA_MAP_PATH)
    except Exception:
        schema = {"tables": {}}
    mapped_tables_map = schema.get("tables", {})

    # 3. Introspect actual database tables
    db_tables: list[dict] = []
    try:
        async with asyncio.timeout(10):
            async with engine.connect() as conn:
                if settings.is_sqlite:
                    rows = (await conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table' "
                             "AND name NOT LIKE 'sqlite_%'")
                    )).fetchall()
                    table_names = sorted(r[0] for r in rows)

                    for tbl in table_names:
                        # Column count
                        cols_result = (await conn.execute(
                            text(f"PRAGMA table_info([{tbl}])")
                        )).fetchall()
                        db_col_count = len(cols_result)

                        # Row count (fast for SQLite)
                        row_count_result = (await conn.execute(
                            text(f"SELECT COUNT(*) FROM [{tbl}]")
                        )).fetchone()
                        row_count = row_count_result[0] if row_count_result else 0

                        # Schema map match
                        in_map = tbl in mapped_tables_map
                        mapped_cols = 0
                        description = ""
                        if in_map:
                            tbl_schema = mapped_tables_map[tbl]
                            description = tbl_schema.get("description", "")
                            mapped_cols = len(tbl_schema.get("columns", {}))

                        db_tables.append({
                            "table": tbl,
                            "db_columns": db_col_count,
                            "mapped_columns": mapped_cols,
                            "row_count": row_count,
                            "in_schema_map": in_map,
                            "description": description,
                        })
                else:
                    # PostgreSQL — information_schema
                    rows = (await conn.execute(
                        text("SELECT table_name FROM information_schema.tables "
                             "WHERE table_schema = 'public' ORDER BY table_name")
                    )).fetchall()
                    table_names = [r[0] for r in rows]

                    for tbl in table_names:
                        cols_result = (await conn.execute(
                            text("SELECT COUNT(*) FROM information_schema.columns "
                                 "WHERE table_schema='public' AND table_name=:t"),
                            {"t": tbl},
                        )).fetchone()
                        db_col_count = cols_result[0] if cols_result else 0

                        row_count_result = (await conn.execute(
                            text(f'SELECT COUNT(*) FROM "{tbl}"')
                        )).fetchone()
                        row_count = row_count_result[0] if row_count_result else 0

                        in_map = tbl in mapped_tables_map
                        mapped_cols = 0
                        description = ""
                        if in_map:
                            tbl_schema = mapped_tables_map[tbl]
                            description = tbl_schema.get("description", "")
                            mapped_cols = len(tbl_schema.get("columns", {}))

                        db_tables.append({
                            "table": tbl,
                            "db_columns": db_col_count,
                            "mapped_columns": mapped_cols,
                            "row_count": row_count,
                            "in_schema_map": in_map,
                            "description": description,
                        })
    except Exception as e:
        logger.warning("freshness_table_introspection_failed: %s", str(e))

    # 4. Compute summary counts
    total_db = len(db_tables)
    mapped_count = sum(1 for t in db_tables if t["in_schema_map"])
    unmapped_count = total_db - mapped_count

    # Sort: mapped first (by name), then unmapped (by name)
    db_tables.sort(key=lambda t: (not t["in_schema_map"], t["table"]))

    # 5. Merge into final response
    freshness["total_db_tables"] = total_db
    freshness["mapped_tables"] = mapped_count
    freshness["unmapped_tables"] = unmapped_count
    freshness["tables"] = db_tables

    return freshness


@router.get("/freshness/history")
async def get_freshness_history():
    """Return sync history (placeholder for future pg_dump tracking)."""
    return {
        "note": "Sync history tracking requires integration with your pg_dump/pg_restore pipeline.",
        "current_db_type": "SQLite" if settings.is_sqlite else "PostgreSQL",
        "staging_db_url": settings.staging_db_url[:40] + "...",
    }


# ── Sync Schedule Persistence ────────────────────────────
_SYNC_SCHEDULE_PATH = _BASE_DIR / "sync_schedule.json"
_SYNC_LOG_PATH = _BASE_DIR / "sync_log.json"

def _read_sync_schedule() -> dict:
    try:
        return _read_json(_SYNC_SCHEDULE_PATH)
    except Exception:
        return {
            "enabled": False,
            "mode": "interval",
            "interval_minutes": 30,
            "specific_time": "03:00",
            "days_of_week": [0, 1, 2, 3, 4],
            "created_at": None,
            "updated_at": None,
        }

def _write_sync_schedule(data: dict) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(_SYNC_SCHEDULE_PATH, data)

def _read_sync_log() -> list:
    try:
        data = _read_json(_SYNC_LOG_PATH)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _append_sync_log(entry: dict) -> None:
    log = _read_sync_log()
    log.insert(0, entry)
    log = log[:100]  # keep last 100 entries
    _write_json(_SYNC_LOG_PATH, log)


@router.get("/sync/schedule")
async def get_sync_schedule():
    """Return the current sync schedule configuration."""
    return _read_sync_schedule()


@router.put("/sync/schedule")
async def update_sync_schedule(request: Request):
    """Update sync schedule settings."""
    body = await request.json()
    schedule = _read_sync_schedule()

    if "enabled" in body:
        schedule["enabled"] = bool(body["enabled"])
    if "mode" in body and body["mode"] in ("interval", "daily", "weekly"):
        schedule["mode"] = body["mode"]
    if "interval_minutes" in body:
        val = int(body["interval_minutes"])
        if val in (5, 10, 15, 30, 60, 120, 360, 720, 1440):
            schedule["interval_minutes"] = val
    if "specific_time" in body:
        schedule["specific_time"] = str(body["specific_time"])[:5]
    if "days_of_week" in body and isinstance(body["days_of_week"], list):
        schedule["days_of_week"] = [int(d) for d in body["days_of_week"] if int(d) in range(7)]

    if not schedule.get("created_at"):
        schedule["created_at"] = datetime.now(timezone.utc).isoformat()

    _write_sync_schedule(schedule)
    return {"status": "updated", "schedule": schedule}


@router.post("/sync/force")
async def force_sync():
    """Trigger a force sync — re-introspect database and refresh schema map."""
    import asyncio
    from db import engine

    start = datetime.now(timezone.utc)
    synced_tables = 0
    errors = []

    try:
        schema = _read_json(_SCHEMA_MAP_PATH)
    except Exception:
        schema = {"database": "erp_bgs", "dialect": "sqlite", "tables": {}}

    mapped_tables = schema.get("tables", {})

    try:
        async with asyncio.timeout(30):
            async with engine.connect() as conn:
                if settings.is_sqlite:
                    rows = (await conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table' "
                             "AND name NOT LIKE 'sqlite_%'")
                    )).fetchall()
                    table_names = sorted(r[0] for r in rows)
                else:
                    rows = (await conn.execute(
                        text("SELECT table_name FROM information_schema.tables "
                             "WHERE table_schema = 'public' ORDER BY table_name")
                    )).fetchall()
                    table_names = [r[0] for r in rows]

                for tbl in table_names:
                    try:
                        if settings.is_sqlite:
                            cols_result = (await conn.execute(
                                text(f"PRAGMA table_info([{tbl}])")
                            )).fetchall()
                            col_names = [c[1] for c in cols_result]
                            col_types = {c[1]: c[2] for c in cols_result}
                        else:
                            cols_result = (await conn.execute(
                                text("SELECT column_name, data_type "
                                     "FROM information_schema.columns "
                                     "WHERE table_schema='public' AND table_name=:t "
                                     "ORDER BY ordinal_position"),
                                {"t": tbl},
                            )).fetchall()
                            col_names = [c[0] for c in cols_result]
                            col_types = {c[0]: c[1] for c in cols_result}

                        if tbl not in mapped_tables:
                            mapped_tables[tbl] = {
                                "description": "",
                                "columns": {},
                            }

                        existing_cols = mapped_tables[tbl].get("columns", {})
                        for col in col_names:
                            if col not in existing_cols:
                                existing_cols[col] = {
                                    "type": col_types.get(col, "TEXT"),
                                    "description": "",
                                }
                            elif "type" not in existing_cols[col]:
                                existing_cols[col]["type"] = col_types.get(col, "TEXT")
                        mapped_tables[tbl]["columns"] = existing_cols
                        synced_tables += 1
                    except Exception as e:
                        errors.append({"table": tbl, "error": str(e)})

        schema["tables"] = mapped_tables
        _write_json(_SCHEMA_MAP_PATH, schema)

    except Exception as e:
        errors.append({"table": "_global_", "error": str(e)})

    elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
    result = {
        "status": "completed" if not errors else "completed_with_errors",
        "synced_tables": synced_tables,
        "total_tables": len(table_names) if 'table_names' in dir() else 0,
        "errors": errors,
        "elapsed_ms": round(elapsed_ms, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    _append_sync_log({
        "type": "force_sync",
        "status": result["status"],
        "synced_tables": synced_tables,
        "errors_count": len(errors),
        "elapsed_ms": result["elapsed_ms"],
        "timestamp": result["timestamp"],
    })

    return result


@router.get("/sync/log")
async def get_sync_log():
    """Return recent sync history log."""
    return {"log": _read_sync_log()}


# ═════════════════════════════════════════════════════════
# 8. SYSTEM PROMPT & SKILLS
# ═════════════════════════════════════════════════════════

_SYSTEM_PROMPT_PATH = _BASE_DIR / "system_prompt.md"
_SKILLS_DIR = _BASE_DIR / "skills"


@router.get("/system-prompt")
async def get_system_prompt():
    """Return the current system prompt and installed skills."""
    prompt_text = ""
    if _SYSTEM_PROMPT_PATH.exists():
        prompt_text = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    skills = []
    if _SKILLS_DIR.exists():
        for f in sorted(_SKILLS_DIR.iterdir()):
            if f.is_file() and f.suffix in (".md", ".txt", ".json"):
                skills.append({
                    "name": f.stem,
                    "file": f.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(
                        f.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                })

    return {
        "prompt": prompt_text,
        "prompt_file": str(_SYSTEM_PROMPT_PATH),
        "skills": skills,
        "skill_count": len(skills),
    }


@router.put("/system-prompt")
async def update_system_prompt(request: Request):
    """Update the system prompt text."""
    body = await request.json()
    prompt_text = body.get("prompt", "")
    _SYSTEM_PROMPT_PATH.write_text(prompt_text, encoding="utf-8")
    return {"status": "updated", "length": len(prompt_text)}


@router.post("/skills")
async def add_skill(request: Request):
    """Add a new skill file."""
    body = await request.json()
    name = body.get("name", "").strip()
    content = body.get("content", "")
    if not name:
        raise HTTPException(400, "name is required")
    # Sanitize filename
    safe_name = re.sub(r"[^\w\-.]", "_", name)
    if not safe_name.endswith((".md", ".txt")):
        safe_name += ".md"
    _SKILLS_DIR.mkdir(exist_ok=True)
    skill_path = _SKILLS_DIR / safe_name
    skill_path.write_text(content, encoding="utf-8")
    return {"status": "created", "file": safe_name}


@router.delete("/skills/{skill_name}")
async def delete_skill(skill_name: str):
    """Delete a skill file."""
    safe_name = re.sub(r"[^\w\-.]", "_", skill_name)
    skill_path = _SKILLS_DIR / safe_name
    if not skill_path.exists() or not skill_path.is_file():
        for ext in (".md", ".txt"):
            alt = _SKILLS_DIR / (safe_name + ext)
            if alt.exists() and alt.is_file():
                skill_path = alt
                break
    if not skill_path.exists() or not skill_path.is_file():
        raise HTTPException(404, f"Skill '{skill_name}' not found")
    if not skill_path.resolve().is_relative_to(_SKILLS_DIR.resolve()):
        raise HTTPException(400, "Invalid skill path")
    skill_path.unlink()
    return {"status": "deleted", "file": skill_path.name}


@router.get("/skills/{skill_name}")
async def get_skill(skill_name: str):
    """Get the content of a skill file."""
    safe_name = re.sub(r"[^\w\-.]", "_", skill_name)
    skill_path = _SKILLS_DIR / safe_name
    if not skill_path.exists() or not skill_path.is_file():
        for ext in (".md", ".txt"):
            alt = _SKILLS_DIR / (safe_name + ext)
            if alt.exists() and alt.is_file():
                skill_path = alt
                break
    if not skill_path.exists() or not skill_path.is_file():
        raise HTTPException(404, f"Skill '{skill_name}' not found")
    if not skill_path.resolve().is_relative_to(_SKILLS_DIR.resolve()):
        raise HTTPException(400, "Invalid skill path")
    return {
        "name": skill_path.stem,
        "file": safe_name,
        "content": skill_path.read_text(encoding="utf-8"),
    }


# ═════════════════════════════════════════════════════════
# 9. ALL USER CHAT SESSIONS (Admin View)
# ═════════════════════════════════════════════════════════

@router.get("/sessions")
async def admin_list_all_sessions(
    user_id: str = "", limit: int = 100, offset: int = 0
):
    """Admin: list chat sessions across all users (or filter by user_id)."""
    import aiosqlite

    db = await aiosqlite.connect(str(_CHAT_DB_PATH))
    db.row_factory = aiosqlite.Row
    try:
        if user_id:
            cursor = await db.execute(
                "SELECT s.*, COUNT(m.id) as message_count "
                "FROM chat_sessions s LEFT JOIN chat_messages m ON s.id = m.session_id "
                "WHERE s.user_id = ? GROUP BY s.id "
                "ORDER BY s.updated_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            )
            cnt = await db.execute(
                "SELECT COUNT(*) FROM chat_sessions WHERE user_id = ?", (user_id,)
            )
        else:
            cursor = await db.execute(
                "SELECT s.*, COUNT(m.id) as message_count "
                "FROM chat_sessions s LEFT JOIN chat_messages m ON s.id = m.session_id "
                "GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            cnt = await db.execute("SELECT COUNT(*) FROM chat_sessions")

        rows = await cursor.fetchall()
        total = (await cnt.fetchone())[0]

        # Get distinct users
        users_cursor = await db.execute(
            "SELECT DISTINCT user_id FROM chat_sessions ORDER BY user_id"
        )
        users = [r[0] for r in await users_cursor.fetchall()]

        return {
            "total": total,
            "users": users,
            "sessions": [dict(r) for r in rows],
        }
    finally:
        await db.close()


@router.get("/sessions/{session_id}")
async def admin_get_session(session_id: str):
    """Admin: get a specific session with all messages."""
    import aiosqlite

    db = await aiosqlite.connect(str(_CHAT_DB_PATH))
    db.row_factory = aiosqlite.Row
    try:
        cursor = await db.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        )
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(404, "Session not found")

        msg_cursor = await db.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        messages = [dict(m) for m in await msg_cursor.fetchall()]
        return {"session": dict(session), "messages": messages}
    finally:
        await db.close()


@router.delete("/sessions/{session_id}")
async def admin_delete_session(session_id: str):
    """Admin: delete a session and all its messages."""
    import aiosqlite

    db = await aiosqlite.connect(str(_CHAT_DB_PATH))
    try:
        await db.execute(
            "DELETE FROM chat_messages WHERE session_id = ?", (session_id,)
        )
        cursor = await db.execute(
            "DELETE FROM chat_sessions WHERE id = ?", (session_id,)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Session not found")
        return {"status": "deleted", "id": session_id}
    finally:
        await db.close()


# ═════════════════════════════════════════════════════════
# 10. RAG DATABASE MANAGEMENT
# ═════════════════════════════════════════════════════════

@router.get("/rag")
async def get_rag_status():
    """Return RAG database status and statistics."""
    import aiosqlite

    stats = {"memories": 0, "episodes": 0, "sessions": 0, "messages": 0}
    db_size = 0

    if _CHAT_DB_PATH.exists():
        db_size = _CHAT_DB_PATH.stat().st_size

    try:
        db = await aiosqlite.connect(str(_CHAT_DB_PATH))
        try:
            for table, key in [
                ("user_memories", "memories"),
                ("episodic_memories", "episodes"),
                ("chat_sessions", "sessions"),
                ("chat_messages", "messages"),
            ]:
                try:
                    cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[key] = (await cursor.fetchone())[0]
                except Exception:
                    pass
        finally:
            await db.close()
    except Exception:
        pass

    return {
        "db_path": str(_CHAT_DB_PATH),
        "db_size_bytes": db_size,
        "db_size_mb": round(db_size / (1024 * 1024), 2),
        "stats": stats,
    }


@router.post("/rag/vacuum")
async def vacuum_rag_db():
    """Vacuum the RAG database to reclaim space."""
    import aiosqlite

    db = await aiosqlite.connect(str(_CHAT_DB_PATH))
    try:
        await db.execute("VACUUM")
        await db.commit()
        new_size = _CHAT_DB_PATH.stat().st_size
        return {
            "status": "vacuumed",
            "new_size_bytes": new_size,
            "new_size_mb": round(new_size / (1024 * 1024), 2),
        }
    finally:
        await db.close()


@router.post("/rag/clear/{table}")
async def clear_rag_table(table: str):
    """Clear all rows from a RAG table (memories, episodes, or messages)."""
    import aiosqlite

    allowed = {
        "memories": "user_memories",
        "episodes": "episodic_memories",
        "messages": "chat_messages",
    }
    real_table = allowed.get(table)
    if not real_table:
        raise HTTPException(400, f"table must be one of: {', '.join(allowed.keys())}")

    db = await aiosqlite.connect(str(_CHAT_DB_PATH))
    try:
        cursor = await db.execute(f"DELETE FROM {real_table}")
        await db.commit()
        return {"status": "cleared", "table": table, "deleted": cursor.rowcount}
    finally:
        await db.close()


# ═════════════════════════════════════════════════════════
# 11. APPLICATION LOGS
# ═════════════════════════════════════════════════════════

_LOG_PATH = _BASE_DIR / "orchestrator.log"


@router.get("/logs")
async def get_logs(lines: int = 200, level: str = ""):
    """Return recent application logs."""
    log_entries = []

    # Read from log file if it exists
    if _LOG_PATH.exists():
        try:
            raw = _LOG_PATH.read_text(encoding="utf-8", errors="replace")
            all_lines = raw.strip().splitlines()
            if level:
                level_upper = level.upper()
                all_lines = [l for l in all_lines if level_upper in l.upper()]
            log_entries = all_lines[-lines:]
        except Exception as e:
            log_entries = [f"Error reading log: {e}"]
    else:
        log_entries = ["No log file found. Configure logging to write to orchestrator.log"]

    # Also include sync log for operational history
    sync_log = _read_sync_log()[:20]

    return {
        "log_file": str(_LOG_PATH),
        "log_exists": _LOG_PATH.exists(),
        "entries": log_entries,
        "entry_count": len(log_entries),
        "sync_log": sync_log,
    }
