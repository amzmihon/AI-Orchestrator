"""
Admin Authentication Router — login, user management for orchestrator admin.

Public endpoint:
  POST /admin/auth/login   — authenticate with username & password

Protected endpoints (admin JWT required):
  GET    /admin/auth/me           — current admin user info
  GET    /admin/auth/users        — list all admin users (superadmin)
  POST   /admin/auth/users        — create admin user   (superadmin)
  PUT    /admin/auth/users/{id}   — update admin user   (superadmin)
  DELETE /admin/auth/users/{id}   — delete admin user   (superadmin)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.admin_auth import AdminUser, create_admin_token
from auth.dependencies import get_admin_user
import admin_db

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])


# ── Request models ───────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "admin"


class UserUpdateRequest(BaseModel):
    username: str | None = None
    password: str | None = None
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


# ── Public ───────────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest):
    """Authenticate with orchestrator admin credentials."""
    user = await admin_db.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_admin_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
            "source": user["source"],
        },
    }


# ── Protected (any admin) ───────────────────────────────

@router.get("/me")
async def me(admin: AdminUser = Depends(get_admin_user)):
    """Return current admin user info."""
    return {
        "id": admin.id,
        "username": admin.username,
        "role": admin.role,
        "source": admin.source,
        "display_name": admin.display_name,
        "erp_user_id": admin.erp_user_id,
    }


# ── Protected (superadmin / ERP admin only) ──────────────

def _require_superadmin(admin: AdminUser):
    if admin.role != "superadmin" and admin.source != "erp-bgs":
        raise HTTPException(status_code=403, detail="Superadmin access required")


@router.get("/users")
async def list_admin_users(admin: AdminUser = Depends(get_admin_user)):
    """List all orchestrator admin users."""
    _require_superadmin(admin)
    users = await admin_db.list_users()
    for u in users:
        u.pop("password_hash", None)
    return {"users": users}


@router.post("/users")
async def create_admin_user_endpoint(
    body: UserCreateRequest,
    admin: AdminUser = Depends(get_admin_user),
):
    """Create a new orchestrator admin user."""
    _require_superadmin(admin)
    existing = await admin_db.get_user_by_username(body.username)
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")
    user = await admin_db.create_user(
        username=body.username,
        password=body.password,
        display_name=body.display_name,
        role=body.role,
    )
    user.pop("password_hash", None)
    return {"user": user, "message": "Admin user created"}


@router.put("/users/{user_id}")
async def update_admin_user_endpoint(
    user_id: int,
    body: UserUpdateRequest,
    admin: AdminUser = Depends(get_admin_user),
):
    """Update an orchestrator admin user."""
    _require_superadmin(admin)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    user = await admin_db.update_user(user_id, **fields)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.pop("password_hash", None)
    return {"user": user, "message": "Admin user updated"}


@router.delete("/users/{user_id}")
async def delete_admin_user_endpoint(
    user_id: int,
    admin: AdminUser = Depends(get_admin_user),
):
    """Delete an orchestrator admin user."""
    _require_superadmin(admin)
    deleted = await admin_db.delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "Admin user deleted"}
