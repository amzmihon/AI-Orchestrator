"""
LLM Client — sends prompts to the LiteLLM Proxy (OpenAI-compatible API)
and returns the generated text.

Supports per-department API keys so each department's usage is tracked
separately in LiteLLM's monitoring dashboard.
"""

from __future__ import annotations

import json

import httpx

from config import settings


class LLMClientError(Exception):
    """Raised when the LLM call fails."""


def _get_api_key(department: str | None = None) -> str:
    """
    Return the LLM API key for the given department.

    If a department-specific key exists in ATL_LLM_DEPARTMENT_KEYS,
    use it. Otherwise fall back to the default ATL_LLM_API_KEY.

    This allows LiteLLM to track token usage per department.
    """
    if department:
        try:
            dept_keys = json.loads(settings.llm_department_keys)
        except (json.JSONDecodeError, TypeError):
            dept_keys = {}

        # Try exact match, then case-insensitive
        key = dept_keys.get(department) or dept_keys.get(department.lower())
        if key:
            return key

    return settings.llm_api_key


async def generate(
    messages: list[dict[str, str]],
    department: str | None = None,
    model: str | None = None,
) -> str:
    """
    Send a chat completion request to the LiteLLM Proxy and return
    the assistant's response content.

    Args:
        messages: OpenAI-style messages list.
        department: Optional department name for per-department API key routing.
        model: Optional model override (defaults to settings.llm_model).

    Returns:
        The text content of the first choice.

    Raises:
        LLMClientError on timeout, connection, or API errors.
    """
    url = f"{settings.llm_base_url}/v1/chat/completions"

    payload = {
        "model": model or settings.llm_model,
        "messages": messages,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
    }

    api_key = _get_api_key(department)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Include department metadata for LiteLLM tracking (skip for non-LiteLLM APIs)
    if department and "localhost" in settings.llm_base_url:
        payload["metadata"] = {"department": department}

    try:
        async with httpx.AsyncClient(
            timeout=settings.llm_request_timeout
        ) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise LLMClientError(
            f"LLM request timed out after {settings.llm_request_timeout}s"
        ) from exc
    except httpx.ConnectError as exc:
        raise LLMClientError(
            f"Cannot connect to LLM server at {settings.llm_base_url}"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise LLMClientError(
            f"LLM API returned {exc.response.status_code}: {exc.response.text}"
        ) from exc

    data = resp.json()

    try:
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        # Reasoning models (e.g. GPT-OSS-20B) may put the answer
        # in reasoning_content when content is empty
        if not content.strip():
            content = message.get("reasoning_content") or message.get("reasoning") or ""
    except (KeyError, IndexError) as exc:
        raise LLMClientError(
            f"Unexpected LLM response structure: {data}"
        ) from exc

    return content.strip()


async def check_llm_reachable() -> bool:
    """Health-check helper — returns True if LiteLLM Proxy responds."""
    url = f"{settings.llm_base_url}/health/liveliness"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            return resp.status_code < 500
    except Exception:
        return False
