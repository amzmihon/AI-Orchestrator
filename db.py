"""
Async database engine and session factory for the Staging DB.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy import text

from config import settings

# ── Engine (lazy singleton) ──────────────────────────────
_engine_kwargs: dict = {
    "echo": settings.debug,
}

if not settings.is_sqlite:
    _engine_kwargs.update({
        "pool_size": 10,
        "max_overflow": 5,
        "pool_pre_ping": True,
        "pool_timeout": 5,
        "connect_args": {"timeout": 3},
    })

engine: AsyncEngine = create_async_engine(
    settings.staging_db_url,
    **_engine_kwargs,
)


async def dispose_engine() -> None:
    """Gracefully close the connection pool."""
    await engine.dispose()


async def check_connection() -> bool:
    """Return True if the staging DB is reachable."""
    import asyncio
    try:
        async with asyncio.timeout(3):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
