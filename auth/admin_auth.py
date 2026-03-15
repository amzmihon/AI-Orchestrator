"""
Admin authentication — JWT creation and verification for orchestrator admin.

Two token types are accepted:
  1. **Orchestrator admin JWT** — issued by this service after local login
     (iss=atl-ai-orchestrator, aud=atl-ai-admin)
  2. **ERP-BGS JWT** — issued by the Django app for users whose role is in
     the ``erp_admin_roles`` config list (default: admin, owner, gm)
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from jose import jwt, JWTError

from config import settings


@dataclass(frozen=True)
class AdminUser:
    """Authenticated orchestrator admin."""
    id: int
    username: str
    role: str            # superadmin | admin | viewer
    source: str          # local | erp-bgs
    display_name: str = ""
    erp_user_id: int | None = None


# ── Orchestrator-issued tokens ───────────────────────────

def create_admin_token(user: dict) -> str:
    """Issue a JWT for a locally authenticated admin."""
    payload = {
        "sub": str(user["id"]),
        "admin_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "source": user["source"],
        "display_name": user.get("display_name", ""),
        "iss": "atl-ai-orchestrator",
        "aud": "atl-ai-admin",
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.admin_jwt_ttl,
    }
    return jwt.encode(payload, settings.admin_jwt_secret, algorithm="HS256")


def decode_admin_token(token: str) -> AdminUser:
    """Decode an orchestrator-issued admin JWT."""
    try:
        payload = jwt.decode(
            token,
            settings.admin_jwt_secret,
            algorithms=["HS256"],
            audience="atl-ai-admin",
            issuer="atl-ai-orchestrator",
        )
    except JWTError as exc:
        raise ValueError(f"Invalid admin token: {exc}") from exc

    return AdminUser(
        id=int(payload.get("admin_id") or payload.get("sub", 0)),
        username=payload.get("username", ""),
        role=payload.get("role", "admin"),
        source=payload.get("source", "local"),
        display_name=payload.get("display_name", ""),
    )


# ── ERP-BGS tokens (admin-role verification) ────────────

def decode_erp_admin_token(token: str) -> AdminUser:
    """
    Decode an ERP-BGS JWT and verify the user holds an admin-level role.
    Raises ValueError if the token is invalid or role insufficient.
    """
    secret = settings.jwt_secret_or_public_key
    if not secret:
        raise ValueError("ERP JWT secret not configured")

    kwargs: dict = {"algorithms": [settings.jwt_algorithm]}

    if settings.jwt_audience:
        kwargs["audience"] = settings.jwt_audience
    else:
        kwargs["options"] = {"verify_aud": False}

    if settings.jwt_issuer:
        kwargs["issuer"] = settings.jwt_issuer

    try:
        payload = jwt.decode(token, secret, **kwargs)
    except JWTError as exc:
        raise ValueError(f"Invalid ERP token: {exc}") from exc

    role = payload.get("role", "")
    allowed = [r.strip() for r in settings.erp_admin_roles.split(",")]
    if role not in allowed:
        raise ValueError(
            f"ERP role '{role}' does not have orchestrator admin access"
        )

    user_id = payload.get("user_id") or payload.get("sub")
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        raise ValueError("Invalid user_id in ERP JWT")

    return AdminUser(
        id=0,
        username=payload.get("username", f"erp_user_{user_id}"),
        role="admin",
        source="erp-bgs",
        display_name=payload.get("username", ""),
        erp_user_id=user_id,
    )
