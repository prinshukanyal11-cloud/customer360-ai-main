"""Provider selection based on configuration."""
from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.core.logging_config import get_logger
from app.llm.base import LLMProvider

logger = get_logger()


@lru_cache
def get_provider() -> LLMProvider:
    from app.llm.providers import GeminiProvider, MockProvider, OpenAIProvider

    mapping = {
        "openai": OpenAIProvider,
        "gemini": GeminiProvider,
        "mock": MockProvider,
    }
    provider_class = mapping.get(settings.llm_provider.lower())
    if provider_class is None:
        logger.warning("unknown_llm_provider", provider=settings.llm_provider, falling_back="mock")
        provider_class = MockProvider
    try:
        return provider_class()
    except ValueError as exc:  # missing key
        logger.warning("llm_provider_init_failed", error=str(exc), falling_back="mock")
        return MockProvider()