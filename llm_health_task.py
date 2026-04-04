"""
llm_health_task.py — Periodic background health check for all LLMs
"""
import asyncio
from llm_manager import llm_manager

async def llm_health_background_task(interval_sec: int = 30):
    while True:
        await llm_manager.heartbeat_all()
        await asyncio.sleep(interval_sec)
