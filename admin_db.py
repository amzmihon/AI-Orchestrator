"""
Admin user database — independent SQLite database for orchestrator admin users.

Manages the `admin.db` file with its own user table, completely separate
from the ERP-BGS database.  Supports local admin accounts and linked
ERP-BGS admin accounts (auto-provisioned on first JWT login).
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

import aiosqlite

from config import settings

_DB_PATH = Path(__file__).parent / "admin.db"


# ── Password hashing (PBKDF2-SHA256, no extra deps) ─────

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}${h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, h_hex = stored.split("$", 1)
    except ValueError:
        return False
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return secrets.compare_digest(h.hex(), h_hex)


# ── Database lifecycle ───────────────────────────────────

async def init_admin_db() -> None:
    """Create admin tables and seed the default admin user on first run."""
    async with aiosqlite.connect(str(_DB_PATH)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT    UNIQUE NOT NULL,
                password_hash TEXT   NOT NULL,
                display_name TEXT    DEFAULT '',
                role         TEXT    NOT NULL DEFAULT 'admin',
                is_active    INTEGER DEFAULT 1,
                source       TEXT    DEFAULT 'local',
                erp_user_id  INTEGER,
                created_at   REAL    NOT NULL,
                last_login   REAL
            )
        """)
        await db.commit()

        cursor = await db.execute("SELECT COUNT(*) FROM admin_users")
        count = (await cursor.fetchone())[0]
        if count == 0:
            await db.execute(
                """INSERT INTO admin_users
                   (username, password_hash, display_name, role, is_active, source, created_at)
                   VALUES (?, ?, ?, ?, 1, 'local', ?)""",
                (
                    settings.admin_default_username,
                    _hash_password(settings.admin_default_password),
                    "System Admin",
                    "superadmin",
                    time.time(),
                ),
            )
            await db.commit()


# ── CRUD operations ──────────────────────────────────────

async def authenticate(username: str, password: str) -> dict | None:
    """Verify credentials and return user dict, or None on failure."""
    async with aiosqlite.connect(str(_DB_PATH)) as db:
        db.row_factory = sqlite3.Row
        cursor = await db.execute(
            "SELECT * FROM admin_users WHERE username = ? AND is_active = 1",
            (username,),
        )
        row = await cursor.fetchone()
        if not row or not _verify_password(password, row["password_hash"]):
            return None
        await db.execute(
            "UPDATE admin_users SET last_login = ? WHERE id = ?",
            (time.time(), row["id"]),
        )
        await db.commit()
        return _row_to_dict(row)


async def get_user_by_id(user_id: int) -> dict | None:
    async with aiosqlite.connect(str(_DB_PATH)) as db:
        db.row_factory = sqlite3.Row
        cursor = await db.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return _row_to_dict(row) if row else None


async def get_user_by_username(username: str) -> dict | None:
    async with aiosqlite.connect(str(_DB_PATH)) as db:
        db.row_factory = sqlite3.Row
        cursor = await db.execute(
            "SELECT * FROM admin_users WHERE username = ?", (username,),
        )
        row = await cursor.fetchone()
        return _row_to_dict(row) if row else None


async def list_users() -> list[dict]:
    async with aiosqlite.connect(str(_DB_PATH)) as db:
        db.row_factory = sqlite3.Row
        cursor = await db.execute(
            "SELECT * FROM admin_users ORDER BY created_at DESC",
        )
        return [_row_to_dict(r) for r in await cursor.fetchall()]


async def create_user(
    username: str,
    password: str,
    display_name: str = "",
    role: str = "admin",
    source: str = "local",
    erp_user_id: int | None = None,
) -> dict:
    async with aiosqlite.connect(str(_DB_PATH)) as db:
        cursor = await db.execute(
            """INSERT INTO admin_users
               (username, password_hash, display_name, role, is_active,
                source, erp_user_id, created_at)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                username,
                _hash_password(password),
                display_name,
                role,
                source,
                erp_user_id,
                time.time(),
            ),
        )
        await db.commit()
        return await get_user_by_id(cursor.lastrowid)


async def update_user(user_id: int, **fields) -> dict | None:
    _ALLOWED = {"display_name", "role", "is_active", "username"}
    updates = {k: v for k, v in fields.items() if k in _ALLOWED}
    if "password" in fields and fields["password"]:
        updates["password_hash"] = _hash_password(fields["password"])
    if not updates:
        return await get_user_by_id(user_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [user_id]
    async with aiosqlite.connect(str(_DB_PATH)) as db:
        await db.execute(
            f"UPDATE admin_users SET {set_clause} WHERE id = ?", values,
        )
        await db.commit()
    return await get_user_by_id(user_id)


async def delete_user(user_id: int) -> bool:
    async with aiosqlite.connect(str(_DB_PATH)) as db:
        cursor = await db.execute(
            "DELETE FROM admin_users WHERE id = ?", (user_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def find_or_create_erp_user(
    erp_user_id: int, username: str, role: str, display_name: str = "",
) -> dict:
    """Find existing linked ERP user or auto-create an admin entry."""
    async with aiosqlite.connect(str(_DB_PATH)) as db:
        db.row_factory = sqlite3.Row
        cursor = await db.execute(
            "SELECT * FROM admin_users WHERE source = 'erp-bgs' AND erp_user_id = ?",
            (erp_user_id,),
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE admin_users SET last_login = ? WHERE id = ?",
                (time.time(), row["id"]),
            )
            await db.commit()
            return _row_to_dict(row)

    return await create_user(
        username=f"erp_{username}",
        password=secrets.token_urlsafe(32),
        display_name=display_name or username,
        role="admin",
        source="erp-bgs",
        erp_user_id=erp_user_id,
    )


# ── Helpers ──────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "source": row["source"],
        "erp_user_id": row["erp_user_id"],
        "created_at": row["created_at"],
        "last_login": row["last_login"],
    }
