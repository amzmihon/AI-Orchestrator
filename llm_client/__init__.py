"""
LLM Client — sends prompts to the LLM backend (LiteLLM, Groq, Ollama, etc.)
and returns the generated text.
"""

from __future__ import annotations
import json
import httpx
from config import settings
from llm_manager import llm_manager
from llm_client.adapters import get_adapter

class LLMClientError(Exception):
    """Raised when the LLM call fails."""

def _get_api_key(department: str | None = None) -> str:
    # Kept for compatibility. Use LLMConfig and Adapters going forward.
    if department:
        try:
            dept_keys = json.loads(settings.llm_department_keys)
        except (json.JSONDecodeError, TypeError):
            dept_keys = {}
        key = dept_keys.get(department) or dept_keys.get(department.lower())
        if key:
            return key
    return settings.llm_api_key

async def generate(
    messages: list[dict[str, str]],
    department: str | None = None,
    model: str | None = None,
    llm_name: str | None = None,
) -> str:
    """
    Send a chat completion request via the designated LLM adapter.
    """
    llm = None
    if llm_name:
        llm = llm_manager.get_llm(llm_name)
    if not llm:
        llm = llm_manager.primary_llm()
    
    if not llm:
        raise LLMClientError("No usable LLM configuration found.")

    adapter = get_adapter(llm)
    
    try:
        return await adapter.generate(messages, department=department, model=model)
    except Exception as exc:
        raise LLMClientError(f"LLM API generation failed: {exc}") from exc

async def check_llm_reachable() -> bool:
    """Health-check helper — returns True if primary LLM responds."""
    llm = llm_manager.primary_llm()
    if not llm:
        return False
    adapter = get_adapter(llm)
    try:
        return await adapter.health_check()
    except Exception:
        return False
