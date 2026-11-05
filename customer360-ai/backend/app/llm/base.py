"""LLM provider abstraction.

The application never imports OpenAI/Gemini SDKs directly in services:
it talks to an `LLMProvider`. Switching providers requires a config change
only. Providers are never exposed to the frontend (no API keys in browser).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMResult:
    text: str


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Return a completion. Implementations must never leak API keys."""
        raise NotImplementedError