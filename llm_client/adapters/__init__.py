"""
__init__.py — LLM Adapters entry module.
"""
from .base import BaseLLMAdapter
from .openai_adapter import OpenAIAdapter

def get_adapter(config) -> BaseLLMAdapter:
    if config.provider_type == "openai":
        return OpenAIAdapter(config)
    # Default to OpenAI adapter for now
    return OpenAIAdapter(config)
