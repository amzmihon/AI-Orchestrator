"""
Admin & Monitoring Router — merged from the standalone Admin Console.

Provides unified management endpoints for:
  • LiteLLM Proxy  — model listing, API key CRUD
  • Ollama Engine  — model lifecycle (list, pull, delete, show)
  • Main App       — user management proxy (list, create, update, toggle, reset-password, delete)
  • System         — health checks, config, LLM test
"""

import os
import httpx
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import settings

router = APIRouter()

# ── Service URLs (resolved from config / env) ──────────
LITELLM_URL = settings.llm_base_url.rstrip("/")
LITELLM_KEY = os.getenv("LITELLM_MASTER_KEY") or os.getenv("ATL_LLM_API_KEY", settings.llm_api_key)
MAIN_APP_URL = settings.main_app_base_url.rstrip("/")
OLLAMA_URL = os.getenv("ATL_OLLAMA_URL", "http://localhost:11434").rstrip("/")

TIMEOUT = httpx.Timeout(15.0)


# ═══════════════════════════════════════════════════════════
# Health Proxies
# ═══════════════════════════════════════════════════════════

@router.get("/admin/services")
async def all_services_status():
    """Check all services and return combined status."""
    results = {}
    checks = {
        "litellm": (f"{LITELLM_URL}/health", {"Authorization": f"Bearer {LITELLM_KEY}"}),
        "main_app": (f"{MAIN_APP_URL}/api/health", {}),
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for name, (url, headers) in checks.items():
            try:
                r = await client.get(url, headers=headers)
                results[name] = {"healthy": r.status_code == 200, "status_code": r.status_code}
            except Exception as e:
                results[name] = {"healthy": False, "error": str(e)}
        # Ollama
        try:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            results["ollama"] = {"healthy": r.status_code == 200}
        except Exception as e:
            results["ollama"] = {"healthy": False, "error": str(e)}
    return results


@router.get("/admin/litellm-health")
async def litellm_health():
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{LITELLM_URL}/health", headers={"Authorization": f"Bearer {LITELLM_KEY}"})
            return {"healthy": r.status_code == 200, "status_code": r.status_code}
    except Exception as e:
        return {"healthy": False, "error": str(e)}


@router.get("/admin/ollama-health")
async def ollama_health():
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            if r.status_code == 200:
                data = r.json()
                return {"healthy": True, "model_count": len(data.get("models", []))}
            return {"healthy": False, "status_code": r.status_code}
    except Exception as e:
        return {"healthy": False, "error": str(e)}


@router.get("/admin/mainapp-health")
async def mainapp_health():
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{MAIN_APP_URL}/api/health")
            data = r.json() if r.status_code == 200 else {}
            return {"healthy": r.status_code == 200, "data": data}
    except Exception as e:
        return {"healthy": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# System Info
# ═══════════════════════════════════════════════════════════

@router.get("/admin/config")
async def system_config():
    """Return current system configuration (non-sensitive)."""
    return {
        "litellm_url": LITELLM_URL,
        "main_app_url": MAIN_APP_URL,
        "ollama_url": OLLAMA_URL,
        "staging_db_host": settings.staging_db_host,
        "staging_db_port": settings.staging_db_port,
        "llm_model": settings.llm_model,
        "auth_mode": settings.auth_mode,
    }


# ═══════════════════════════════════════════════════════════
# LiteLLM — Models & Key Management
# ═══════════════════════════════════════════════════════════

@router.get("/admin/litellm/models")
async def litellm_models():
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{LITELLM_URL}/v1/models", headers={"Authorization": f"Bearer {LITELLM_KEY}"})
            if r.status_code == 200:
                return r.json()
            return {"error": f"Status {r.status_code}", "detail": r.text}
    except Exception as e:
        return {"error": str(e)}


@router.post("/admin/litellm/keys/generate")
async def litellm_key_generate(request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{LITELLM_URL}/key/generate",
                json=body,
                headers={"Authorization": f"Bearer {LITELLM_KEY}", "Content-Type": "application/json"},
            )
            return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/admin/litellm/keys/list")
async def litellm_key_list():
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(
                f"{LITELLM_URL}/key/list",
                headers={"Authorization": f"Bearer {LITELLM_KEY}"},
            )
            if r.status_code == 200:
                return r.json()
            return {"error": f"Status {r.status_code}", "detail": r.text}
    except Exception as e:
        return {"error": str(e)}


@router.post("/admin/litellm/keys/delete")
async def litellm_key_delete(request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{LITELLM_URL}/key/delete",
                json=body,
                headers={"Authorization": f"Bearer {LITELLM_KEY}", "Content-Type": "application/json"},
            )
            return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/litellm/keys/info")
async def litellm_key_info(request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{LITELLM_URL}/key/info",
                json=body,
                headers={"Authorization": f"Bearer {LITELLM_KEY}", "Content-Type": "application/json"},
            )
            return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ═══════════════════════════════════════════════════════════
# Ollama — Model Management
# ═══════════════════════════════════════════════════════════

@router.get("/admin/ollama/models")
async def ollama_models():
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            if r.status_code == 200:
                return r.json()
            return {"error": f"Status {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/admin/ollama/running")
async def ollama_running():
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{OLLAMA_URL}/api/ps")
            if r.status_code == 200:
                return r.json()
            return {"error": f"Status {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/admin/ollama/pull")
async def ollama_pull(request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            r = await client.post(f"{OLLAMA_URL}/api/pull", json={"name": body.get("name"), "stream": False})
            return JSONResponse(status_code=r.status_code, content=r.json() if r.status_code == 200 else {"error": r.text})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/ollama/delete")
async def ollama_delete(request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.delete(f"{OLLAMA_URL}/api/delete", json={"name": body.get("name")})
            return JSONResponse(status_code=r.status_code, content={"status": "deleted"} if r.status_code == 200 else {"error": r.text})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/ollama/show")
async def ollama_show(request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{OLLAMA_URL}/api/show", json={"name": body.get("name")})
            if r.status_code == 200:
                return r.json()
            return {"error": r.text}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# Main App — User Management (proxy with admin auto-login)
# ═══════════════════════════════════════════════════════════

async def _admin_token(client: httpx.AsyncClient) -> str | None:
    """Get a fresh admin JWT from Main App."""
    r = await client.post(
        f"{MAIN_APP_URL}/api/auth/login",
        json={"email": "ahmed.rashid@atlcorp.com", "password": "password123"},
    )
    if r.status_code == 200:
        return r.json().get("access_token")
    return None


@router.get("/admin/users")
async def mainapp_users():
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            token = await _admin_token(client)
            if not token:
                return {"error": "Could not authenticate with Main App"}
            r = await client.get(f"{MAIN_APP_URL}/api/users", headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                return r.json()
            return {"error": f"Status {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/admin/users")
async def mainapp_create_user(request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            token = await _admin_token(client)
            if not token:
                return JSONResponse(status_code=401, content={"error": "Admin auth failed"})
            r = await client.post(
                f"{MAIN_APP_URL}/api/users", json=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.put("/admin/users/{user_id}")
async def mainapp_update_user(user_id: int, request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            token = await _admin_token(client)
            if not token:
                return JSONResponse(status_code=401, content={"error": "Admin auth failed"})
            r = await client.put(
                f"{MAIN_APP_URL}/api/users/{user_id}", json=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/users/{user_id}/toggle")
async def mainapp_toggle_user(user_id: int):
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            token = await _admin_token(client)
            if not token:
                return JSONResponse(status_code=401, content={"error": "Admin auth failed"})
            r = await client.post(
                f"{MAIN_APP_URL}/api/users/{user_id}/toggle-active",
                headers={"Authorization": f"Bearer {token}"},
            )
            return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/users/{user_id}/reset-password")
async def mainapp_reset_password(user_id: int, request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            token = await _admin_token(client)
            if not token:
                return JSONResponse(status_code=401, content={"error": "Admin auth failed"})
            r = await client.post(
                f"{MAIN_APP_URL}/api/users/{user_id}/reset-password", json=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.delete("/admin/users/{user_id}")
async def mainapp_delete_user(user_id: int):
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            token = await _admin_token(client)
            if not token:
                return JSONResponse(status_code=401, content={"error": "Admin auth failed"})
            r = await client.delete(
                f"{MAIN_APP_URL}/api/users/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ═══════════════════════════════════════════════════════════
# LLM Test
# ═══════════════════════════════════════════════════════════

@router.post("/admin/llm/test")
async def llm_test(request: Request):
    """Send a test prompt to LiteLLM and return the response."""
    body = await request.json()
    prompt = body.get("prompt", "Say hello in one sentence.")
    model = body.get("model", settings.llm_model)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            r = await client.post(
                f"{LITELLM_URL}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 256,
                },
                headers={"Authorization": f"Bearer {LITELLM_KEY}", "Content-Type": "application/json"},
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "response": data.get("choices", [{}])[0].get("message", {}).get("content", ""),
                    "model": data.get("model", model),
                    "usage": data.get("usage", {}),
                }
            return {"error": f"LLM returned status {r.status_code}", "detail": r.text}
    except Exception as e:
        return {"error": str(e)}
