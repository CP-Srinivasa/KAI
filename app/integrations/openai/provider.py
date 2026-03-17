"""OpenAI analysis provider.

Implements BaseAnalysisProvider using the OpenAI SDK's structured output feature
(beta.chat.completions.parse). The LLM response is automatically parsed and
validated against the LLMAnalysisOutput Pydantic schema.

Provider name: "openai"
Default model: gpt-4o (configurable)
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.integrations.openai.prompts import SYSTEM_PROMPT_V1, format_user_prompt

_MAX_TEXT_CHARS = 6000   # ~1500 tokens — leaves room for prompt + response


class OpenAIAnalysisProvider(BaseAnalysisProvider):
    """Analyze documents using OpenAI structured outputs.

    Args:
        api_key:    OpenAI API key (required).
        model:      Model name, default "gpt-4o". Must support structured outputs.
        timeout:    HTTP timeout in seconds.
        max_tokens: Max response tokens (default 1024 is sufficient for structured output).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        timeout: int = 30,
        max_tokens: int = 1024,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str | None:
        return self._model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput:
        """Call OpenAI and return validated LLMAnalysisOutput.

        Uses beta.chat.completions.parse for automatic schema validation.
        Retries up to 3 times with exponential backoff on transient errors.

        Raises:
            openai.APIError:      on non-retryable API errors.
            pydantic.ValidationError: if model output fails schema validation after retries.
        """
        user_prompt = format_user_prompt(
            title=title,
            text=text[:_MAX_TEXT_CHARS],
            context=context,
        )
        response = await self._client.beta.chat.completions.parse(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_V1},
                {"role": "user", "content": user_prompt},
            ],
            response_format=LLMAnalysisOutput,
            max_tokens=self._max_tokens,
        )
        result: LLMAnalysisOutput | None = response.choices[0].message.parsed
        if result is None:
            raise ValueError("OpenAI returned null parsed output — possible refusal")
        return result

    @classmethod
    def from_settings(cls, settings: Any) -> OpenAIAnalysisProvider:
        """Construct from ProviderSettings (app.core.settings.ProviderSettings)."""
        return cls(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout=settings.openai_timeout,
        )
