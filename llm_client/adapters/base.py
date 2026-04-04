"""
base.py — Abstract base class for LLM provider adapters.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from config import LLMConfig

class BaseLLMAdapter(ABC):
    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Generate response from the LLM based on messages."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the LLM provider is healthy/reachable."""
        pass
