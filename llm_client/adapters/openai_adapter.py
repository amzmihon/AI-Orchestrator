"""
openai_adapter.py — Adapter for OpenAI-compatible APIs (LiteLLM, Groq, vLLM).
"""
import httpx
import time
from typing import List, Dict, Any
from .base import BaseLLMAdapter
from config import settings

class OpenAIAdapter(BaseLLMAdapter):
    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        url = f"{self.config.base_url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": kwargs.get("model") or self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        
        # Merge extra kwargs
        department = kwargs.get("department")
        if department and "localhost" in self.config.base_url:
            payload["metadata"] = {"department": department}
            
        async with httpx.AsyncClient(timeout=self.config.request_timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            
        data = resp.json()
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        if not content.strip():
            content = message.get("reasoning_content") or message.get("reasoning") or ""
        return content.strip()

    async def health_check(self) -> bool:
        url = f"{self.config.base_url.rstrip('/')}/v1/models"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, headers=headers)
                return resp.status_code < 500
        except Exception:
            # Fallback to health liveliness if models endpoint fails
            try:
                hl_url = f"{self.config.base_url.rstrip('/')}/health/liveliness"
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(hl_url)
                    return resp.status_code < 500
            except Exception:
                return False
