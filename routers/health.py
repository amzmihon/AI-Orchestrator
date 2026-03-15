"""
Health endpoint — reports component connectivity and data freshness.
"""

from fastapi import APIRouter

from config import settings
from db import check_connection as check_db
from llm_client import check_llm_reachable
from auth.token_verify import check_main_app_reachable
from data_freshness import get_data_freshness

router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Returns the status of all dependent services:
      - staging_db
      - llm_server
      - main_app_auth
    """
    db_ok = await check_db()
    llm_ok = await check_llm_reachable()
    auth_ok = await check_main_app_reachable()

    all_healthy = db_ok and llm_ok and auth_ok

    return {
        "status": "healthy" if all_healthy else "degraded",
        "components": {
            "staging_db": "connected" if db_ok else "unreachable",
            "llm_server": "connected" if llm_ok else "unreachable",
            "main_app_auth": "reachable" if auth_ok else "unreachable",
        },
        "version": settings.app_version,
    }


@router.get("/data-freshness")
async def data_freshness():
    """
    Returns the freshness status of the staging database.

    The staging DB syncs from the production system every ~30 minutes.
    This endpoint tells the frontend exactly how current the data is,
    so users know if a recent sale or hire hasn't appeared yet.

    Response:
      - status: "fresh" | "slightly_stale" | "stale" | "unknown"
      - last_updated: ISO timestamp of most recent data record
      - minutes_ago: integer minutes since latest data
      - message: human-readable string (e.g. "Data last synced: 12 minutes ago")
      - sync_interval_minutes: expected sync frequency (30)
      - table_details: per-table breakdown
    """
    return await get_data_freshness()
