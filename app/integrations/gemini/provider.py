"""Google Gemini analysis provider.

Implements BaseAnalysisProvider using the google-genai SDK's structured output
(response_schema) feature.

Provider name: "gemini"
Default model: gemini-2.5-flash (configurable)
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.analysis.prompts import SYSTEM_PROMPT_V1, format_user_prompt

_MAX_TEXT_CHARS = 10000


class GeminiAnalysisProvider(BaseAnalysisProvider):
    """Analyze documents using Google Gemini structured outputs.

    Args:
        api_key:    Gemini API key (required).
        model:      Model name, default "gemini-2.5-flash".
        timeout:    HTTP timeout in seconds.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        timeout: int = 30,
    ) -> None:
        # genai.Client does not support a constructor-level timeout.
        # timeout is stored and passed as http_options if needed per-request in future.
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        return "gemini"

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
        """Call Gemini and return validated LLMAnalysisOutput.

        Uses response_schema config to guarantee the JSON matches the schema.
        Note: google-genai Client is wrapped in asyncio.to_thread because it blocks.
        """
        import asyncio
        user_prompt = format_user_prompt(
            title=title,
            text=text[:_MAX_TEXT_CHARS],
            context=context,
        )

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=LLMAnalysisOutput,
            system_instruction=SYSTEM_PROMPT_V1,
            temperature=0.1,
        )

        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=self._model,
            contents=user_prompt,
            config=config,
        )

        if not response.text:
             raise ValueError("Gemini returned empty structured output")

        # response.text is guaranteed to be a JSON string matching schema
        return LLMAnalysisOutput.model_validate_json(response.text)

    @classmethod
    def from_settings(cls, settings: Any) -> GeminiAnalysisProvider:
        """Construct from ProviderSettings (app.core.settings.ProviderSettings)."""
        return cls(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout=settings.gemini_timeout,
        )
