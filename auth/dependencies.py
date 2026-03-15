"""
FastAPI dependencies for authentication.

Two dependency functions:
  - ``get_current_user``  — for chat / session routes (any verified ERP user)
  - ``get_admin_user``    — for admin dashboard routes (orchestrator admin OR
    ERP-BGS user with an admin-level role)
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.token_verify import VerifiedUser, verify_token
from auth.admin_auth import AdminUser, decode_admin_token, decode_erp_admin_token

_bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> VerifiedUser:
    """
    FastAPI dependency — inject into any route that needs authentication.

    Usage:
        @router.post("/chat")
        async def chat(user: VerifiedUser = Depends(get_current_user)):
            ...
    """
    token = credentials.credentials

    try:
        user = await verify_token(token)
    except ConnectionError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Main application auth service is unreachable",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token verification failed",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_admin_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> AdminUser:
    """
    FastAPI dependency for admin dashboard routes.

    Accepts either:
      1. Orchestrator-issued admin JWT  (from local ``/admin/auth/login``)
      2. ERP-BGS JWT whose ``role`` claim is in ``erp_admin_roles``
    """
    token = credentials.credentials

    # 1. Try orchestrator admin token
    try:
        return decode_admin_token(token)
    except (ValueError, Exception):
        pass

    # 2. Try ERP-BGS admin token
    try:
        return decode_erp_admin_token(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        )
