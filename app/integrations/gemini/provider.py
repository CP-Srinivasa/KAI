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
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.ai.audit import is_retryable_error
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
        # NEO-F-002 (2026-09-02): the timeout used to be stored and never used —
        # a hanging Gemini call blocked a thread-pool slot forever. Two layers now:
        # (1) HttpOptions.timeout (milliseconds) makes the SDK's own HTTP client
        #     give up, and
        # (2) asyncio.wait_for() in analyze() frees the awaiting task even if the
        #     SDK ignores (1).
        # Honest limitation: the to_thread worker itself is not cancellable. The
        # complete fix needs the async client; this is the halfway house that stops
        # the unbounded await.
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout) * 1000),
        )
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
        # NEO-F-006: without a filter tenacity retried 401/400/ValidationError too,
        # costing three attempts plus up to 15 s backoff for a hopeless call.
        retry=retry_if_exception(is_retryable_error),
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

        response = await asyncio.wait_for(
            asyncio.to_thread(
                self._client.models.generate_content,
                model=self._model,
                contents=user_prompt,
                config=config,
            ),
            timeout=self._timeout,
        )

        if not response.text:
            raise ValueError("Gemini returned empty structured output")

        # response.text is guaranteed to be a JSON string matching schema
        result = LLMAnalysisOutput.model_validate_json(response.text)
        # NEO-F-003: without these the DB audit trail skipped every Gemini success
        # and the token columns stayed at zero. Mirrors openai/provider.py:91-95.
        result.raw_prompt = user_prompt
        result.raw_response = response.text
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            # isinstance-gated: LLMAnalysisOutput is strict + validate_assignment,
            # so a non-int usage field must not blow up an otherwise valid answer.
            prompt_tokens = getattr(usage, "prompt_token_count", 0)
            completion_tokens = getattr(usage, "candidates_token_count", 0)
            if isinstance(prompt_tokens, int):
                result.prompt_tokens = prompt_tokens
            if isinstance(completion_tokens, int):
                result.completion_tokens = completion_tokens
        return result

    @classmethod
    def from_settings(cls, settings: Any) -> GeminiAnalysisProvider:
        """Construct from ProviderSettings (app.core.settings.ProviderSettings)."""
        return cls(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout=settings.gemini_timeout,
        )
