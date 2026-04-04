"""
llm_manager.py — Multi-LLM registration, health, and selection service
"""
import asyncio
import time
from typing import Dict, List, Optional
import httpx
from pydantic import BaseModel

from config import LLMConfig, settings

class LLMStatus(BaseModel):
    name: str
    healthy: bool = True
    last_heartbeat: float = 0.0
    latency_ms: Optional[float] = None
    fail_count: int = 0
    role: str = "standby"
    capabilities: List[str] = []

class LLMManager:
    def __init__(self, llms: Optional[List[LLMConfig]] = None):
        self.llms: Dict[str, LLMConfig] = {}
        self.status: Dict[str, LLMStatus] = {}
        self.load_llms(llms or settings.llms)

    def load_llms(self, llms: List[LLMConfig]):
        for llm in llms:
            self.llms[llm.name] = llm
            self.status[llm.name] = LLMStatus(
                name=llm.name,
                healthy=True,
                last_heartbeat=time.time(),
                role=llm.role,
                capabilities=llm.capabilities,
            )

    async def check_llm_health(self, name: str) -> bool:
        llm = self.llms.get(name)
        if not llm:
            return False
        url = f"{llm.base_url}/health/liveliness"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                start = time.time()
                resp = await client.get(url)
                latency = (time.time() - start) * 1000
                healthy = resp.status_code < 500
                self.status[name].healthy = healthy
                self.status[name].last_heartbeat = time.time()
                self.status[name].latency_ms = latency
                if healthy:
                    self.status[name].fail_count = 0
                else:
                    self.status[name].fail_count += 1
                return healthy
        except Exception:
            self.status[name].healthy = False
            self.status[name].fail_count += 1
            return False

    async def heartbeat_all(self):
        tasks = [self.check_llm_health(name) for name in self.llms]
        return await asyncio.gather(*tasks)

    def get_llm(self, name: str) -> Optional[LLMConfig]:
        return self.llms.get(name)

    def get_status(self, name: str) -> Optional[LLMStatus]:
        return self.status.get(name)

    def all_llms(self) -> List[LLMConfig]:
        return list(self.llms.values())

    def healthy_llms(self) -> List[LLMConfig]:
        return [llm for llm in self.llms.values() if self.status[llm.name].healthy]

    def primary_llm(self) -> Optional[LLMConfig]:
        # Try to find a healthy primary
        for llm in self.llms.values():
            if llm.role == "primary" and self.status[llm.name].healthy:
                return llm
        # If no healthy primary, promote first healthy standby/secondary
        for llm in self.llms.values():
            if llm.role in ("secondary", "standby") and self.status[llm.name].healthy:
                # Promote to primary
                llm.role = "primary"
                self.status[llm.name].role = "primary"
                return llm
        return None

    def register_llm(self, llm: LLMConfig):
        self.llms[llm.name] = llm
        self.status[llm.name] = LLMStatus(
            name=llm.name,
            healthy=True,
            last_heartbeat=time.time(),
            role=llm.role,
            capabilities=llm.capabilities,
        )

    def update_heartbeat(self, name: str, latency_ms: Optional[float] = None):
        if name in self.status:
            self.status[name].last_heartbeat = time.time()
            self.status[name].healthy = True
            self.status[name].latency_ms = latency_ms
            self.status[name].fail_count = 0

    def mark_failed(self, name: str):
        if name in self.status:
            self.status[name].healthy = False
            self.status[name].fail_count += 1

llm_manager = LLMManager()
