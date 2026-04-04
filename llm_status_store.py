"""
llm_status_store.py — Persistent tracking of LLM status and metrics.
"""
import time
import json
import asyncio
from typing import Dict, List, Any, Optional
import aiosqlite
import pathlib

DB_PATH = pathlib.Path(__file__).parent / "llm_status.db"

async def init_status_db():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS llm_metrics (
                name TEXT PRIMARY KEY,
                total_requests INTEGER DEFAULT 0,
                total_tokens_in INTEGER DEFAULT 0,
                total_tokens_out INTEGER DEFAULT 0,
                total_cost REAL DEFAULT 0.0,
                error_count INTEGER DEFAULT 0,
                last_error TEXT,
                uptime_percent REAL DEFAULT 100.0,
                last_updated REAL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS llm_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                timestamp REAL,
                latency_ms REAL,
                healthy BOOLEAN
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_llm_history_name_time ON llm_history(name, timestamp)')
        await db.commit()

class LLMStatusStore:
    def __init__(self):
        self.db_path = str(DB_PATH)
        
    async def record_metrics(self, name: str, requests: int = 0, tokens_in: int = 0, tokens_out: int = 0, cost: float = 0.0, error: Optional[str] = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO llm_metrics (name, total_requests, total_tokens_in, total_tokens_out, total_cost, error_count, last_error, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    total_requests = total_requests + excluded.total_requests,
                    total_tokens_in = total_tokens_in + excluded.total_tokens_in,
                    total_tokens_out = total_tokens_out + excluded.total_tokens_out,
                    total_cost = total_cost + excluded.total_cost,
                    error_count = error_count + excluded.error_count,
                    last_error = COALESCE(excluded.last_error, last_error),
                    last_updated = excluded.last_updated
            ''', (
                name, requests, tokens_in, tokens_out, cost, 
                1 if error else 0, error, time.time()
            ))
            await db.commit()

    async def record_history(self, name: str, latency_ms: float, healthy: bool):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO llm_history (name, timestamp, latency_ms, healthy)
                VALUES (?, ?, ?, ?)
            ''', (name, time.time(), latency_ms, healthy))
            await db.commit()

    async def get_all_stats(self) -> Dict[str, dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM llm_metrics")
            rows = await cursor.fetchall()
            return {r["name"]: dict(r) for r in rows}

    async def get_llm_stats(self, name: str) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM llm_metrics WHERE name = ?", (name,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_llm_history(self, name: str, limit: int = 100) -> List[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('''
                SELECT * FROM llm_history 
                WHERE name = ? 
                ORDER BY timestamp DESC LIMIT ?
            ''', (name, limit))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

llm_status_store = LLMStatusStore()
