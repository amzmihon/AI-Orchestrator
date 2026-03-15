"""
Token verification — supports two authentication strategies:

1. **JWT Mode** (Silent Auth / Zero-Network)
   Decodes the JWT locally using the Main App's secret or public key.
   The user's identity, role, and department are embedded in the JWT claims.
   No network call needed — the fastest option.

2. **HTTP Mode** (API Callback)
   Calls the Main App's /verify-user endpoint to validate the token.
   Useful when the Main App doesn't issue JWTs or when you need
   real-time permission checks.

3. **Hybrid Mode** (default)
   Tries JWT first. If the token isn't a valid JWT, falls back to HTTP.

The mode is controlled by the ATL_AUTH_MODE env var ("jwt" | "http" | "hybrid").

Pro-Tip: In the Main App, when the user clicks the AI button, pass the
JWT in the Authorization header or as a URL parameter. The AI App validates
it locally — the user never sees a second login screen.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx
from jose import jwt, JWTError

from config import settings


# ── User model returned after verification ───────────────
@dataclass(frozen=True)
class VerifiedUser:
    user_id: int
    username: str
    role: str
    department: str
    permissions: list[str] = field(default_factory=list)


# ── Simple TTL cache ────────────────────────────────────
_cache: dict[str, tuple[VerifiedUser, float]] = {}


def _get_cached(token: str) -> VerifiedUser | None:
    entry = _cache.get(token)
    if entry is None:
        return None
    user, ts = entry
    if time.time() - ts > settings.token_cache_ttl_seconds:
        _cache.pop(token, None)
        return None
    return user


def _set_cached(token: str, user: VerifiedUser) -> None:
    _cache[token] = (user, time.time())


def clear_cache() -> None:
    """Utility to flush the token cache (useful for testing)."""
    _cache.clear()


# ═════════════════════════════════════════════════════════
# Strategy 1: JWT — local decode, zero network calls
# ═════════════════════════════════════════════════════════

class JWTVerificationError(Exception):
    """Raised when JWT decode / validation fails."""


def _decode_jwt(token: str) -> VerifiedUser:
    """
    Decode a JWT issued by the Main App.

    Expected JWT payload:
    {
      "sub": "42",             # or "user_id": 42
      "username": "john.doe",
      "role": "HR_Manager",
      "department": "Human Resources",
      "permissions": ["employees", "leave_requests"],
      "exp": 1740000000,       # expiration
      "iss": "main-app",       # issuer (optional)
      "aud": "atl-ai"          # audience (optional)
    }
    """
    secret = settings.jwt_secret_or_public_key
    if not secret:
        raise JWTVerificationError(
            "JWT auth enabled but ATL_JWT_SECRET_OR_PUBLIC_KEY is not set"
        )

    options: dict = {}
    kwargs: dict = {
        "algorithms": [settings.jwt_algorithm],
        "options": options,
    }

    # Optional audience / issuer checks
    if settings.jwt_audience:
        kwargs["audience"] = settings.jwt_audience
    else:
        options["verify_aud"] = False

    if settings.jwt_issuer:
        kwargs["issuer"] = settings.jwt_issuer

    try:
        payload = jwt.decode(token, secret, **kwargs)
    except JWTError as exc:
        raise JWTVerificationError(f"JWT decode failed: {exc}") from exc

    # Extract user info from claims
    user_id = payload.get("user_id") or payload.get("sub")
    if user_id is None:
        raise JWTVerificationError("JWT missing 'user_id' or 'sub' claim")

    try:
        user_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise JWTVerificationError(f"Invalid user_id in JWT: {user_id}") from exc

    username = payload.get("username", payload.get("name", f"user_{user_id}"))
    role = payload.get("role", "")
    department = payload.get("department", "")
    permissions = payload.get("permissions", [])

    if not role:
        raise JWTVerificationError("JWT missing 'role' claim")

    return VerifiedUser(
        user_id=user_id,
        username=username,
        role=role,
        department=department,
        permissions=permissions if isinstance(permissions, list) else [],
    )


# ═════════════════════════════════════════════════════════
# Strategy 2: HTTP — call Main App /verify-user
# ═════════════════════════════════════════════════════════

async def _verify_via_http(token: str) -> VerifiedUser:
    """
    Verify a Bearer token by calling the Main App's /verify-user API.

    Returns a VerifiedUser on success.
    Raises httpx.HTTPStatusError on 401/403.
    Raises ConnectionError if the Main App is unreachable.
    """
    url = f"{settings.main_app_base_url}{settings.main_app_verify_endpoint}"

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot reach Main App at {url}"
            ) from exc

    data = resp.json()

    return VerifiedUser(
        user_id=data["user_id"],
        username=data["username"],
        role=data["role"],
        department=data.get("department", ""),
        permissions=data.get("permissions", []),
    )


# ═════════════════════════════════════════════════════════
# Main entry point — picks strategy based on config
# ═════════════════════════════════════════════════════════

async def verify_token(token: str) -> VerifiedUser:
    """
    Verify a Bearer token using the configured auth strategy.

    Auth modes:
      - "jwt"    → Decode locally (no network). Fails if not a valid JWT.
      - "http"   → Call Main App /verify-user endpoint.
      - "hybrid" → Try JWT first, fall back to HTTP on failure.

    Results are cached for token_cache_ttl_seconds.
    """
    # Check cache first
    cached = _get_cached(token)
    if cached is not None:
        return cached

    mode = settings.auth_mode.lower()
    user: VerifiedUser | None = None

    if mode == "jwt":
        user = _decode_jwt(token)

    elif mode == "http":
        user = await _verify_via_http(token)

    elif mode == "hybrid":
        # Try JWT first (fast, no network), fall back to HTTP
        try:
            user = _decode_jwt(token)
        except (JWTVerificationError, Exception):
            user = await _verify_via_http(token)

    else:
        raise ValueError(f"Unknown auth_mode: '{mode}'. Use jwt, http, or hybrid.")

    _set_cached(token, user)
    return user


async def check_main_app_reachable() -> bool:
    """Health-check helper — returns True if Main App responds."""
    # In JWT-only mode, we don't need the Main App to be reachable
    if settings.auth_mode.lower() == "jwt":
        return True

    url = f"{settings.main_app_base_url}/health"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            return resp.status_code < 500
    except Exception:
        return False
