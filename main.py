"""
ATL-AI Orchestrator — FastAPI entry point.

Private AI-Powered Corporate Intelligence Ecosystem.
Converts natural-language questions into SQL, executes on a read-only
staging database, and returns professional summaries.
"""

import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import settings
from db import engine, dispose_engine
from routers import chat, health, sessions, admin, assist, admin_dashboard, admin_auth
from chat_history import init_db
from admin_db import init_admin_db
from auth.dependencies import get_admin_user
from middleware.rate_limiter import RateLimitMiddleware

STATIC_DIR = pathlib.Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    # Startup — initialize databases
    await init_db()
    await init_admin_db()
    yield
    # Shutdown — close DB pool
    await dispose_engine()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# ── CORS (allow the main office app frontend) ───────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten to Main App origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate Limiting ────────────────────────────────────────
app.add_middleware(RateLimitMiddleware)

# ── Routers ──────────────────────────────────────────────
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(assist.router, prefix="/api", tags=["writing assistant"])
app.include_router(sessions.router, prefix="/api", tags=["sessions & memory"])
app.include_router(admin.router, prefix="/api", tags=["admin & monitoring"])
app.include_router(admin_auth.router, prefix="/api", tags=["admin auth"])
app.include_router(
    admin_dashboard.router,
    prefix="/api",
    tags=["admin dashboard"],
    dependencies=[Depends(get_admin_user)],
)

# ── Static UI (AFTER routers so /api/* is matched first) ─
@app.get("/", include_in_schema=False)
async def root_ui():
    resp = FileResponse(STATIC_DIR / "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8101)),
        reload=settings.debug,
    )
