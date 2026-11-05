"""Concrete LLM providers: OpenAI, Gemini, and an honest MockProvider."""
from __future__ import annotations

import time

from app.core.config import settings
from app.core.logging_config import get_logger
from app.llm.base import LLMProvider, LLMResult

logger = get_logger()


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self) -> None:
        from openai import OpenAI

        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not configured.")
        self._client = OpenAI(api_key=settings.openai_api_key)

    def complete(self, system_prompt: str, user_prompt: str) -> LLMResult:
        response = self._client.chat.completions.create(
            model=settings.default_llm_model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return LLMResult(text=response.choices[0].message.content or "")


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        from google import genai

        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not configured.")
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def complete(self, system_prompt: str, user_prompt: str) -> LLMResult:
        response = self._client.models.generate_content(
            model=settings.default_llm_model,
            config={
                "temperature": settings.llm_temperature,
                "max_output_tokens": settings.llm_max_tokens,
                "system_instruction": system_prompt,
            },
            contents=user_prompt,
        )
        return LLMResult(text=response.text or "")


class MockProvider(LLMProvider):
    """Used for local development / demo mode.

    Never invents numbers: it reports exactly what the retrieved metrics
    show and explains that no LLM provider is configured.
    """

    name = "mock"

    def complete(self, system_prompt: str, user_prompt: str) -> LLMResult:
        logger.info("mock_llm_called", length=len(user_prompt))
        time.sleep(0.1)  # simulate latency deterministically
        return LLMResult(
            text=(
                "No LLM provider is configured (LLM_PROVIDER=mock). "
                "I can still show you data retrieved from your backend — "
                "see the attached metrics. Configure OPENAI_API_KEY or "
                "GEMINI_API_KEY to enable full natural-language answers."
            )
        )