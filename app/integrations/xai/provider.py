"""xAI (Grok) analysis provider — Phase I: Ausweichmöglichkeit / Fallback only.

Implements BaseAnalysisProvider against the xAI API (OpenAI-compatible surface at
https://api.x.ai/v1). Activated only when ProviderSettings.xai_fallback_enabled
is true; otherwise the skeleton is imported but never instantiated.

xAI does not currently support OpenAI's beta.chat.completions.parse helper, so
we request a JSON object and validate with LLMAnalysisOutput.model_validate_json.

Provider name: "grok"
Default model: grok-4
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.ai.audit import is_retryable_error
from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.analysis.prompts import SYSTEM_PROMPT_V1, format_user_prompt

_XAI_BASE_URL = "https://api.x.ai/v1"
_MAX_TEXT_CHARS = 6000


class GrokAnalysisProvider(BaseAnalysisProvider):
    """Analyze documents using xAI Grok via OpenAI-compatible REST surface.

    Intended as a fallback when primary providers (OpenAI, Gemini) fail or hit
    quota/outage limits. Not wired into the primary ensemble chain in Phase I.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "grok-4",
        timeout: int = 30,
        max_tokens: int = 1024,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=_XAI_BASE_URL,
            timeout=timeout,
        )
        self._model = model
        self._max_tokens = max_tokens

    @property
    def provider_name(self) -> str:
        return "grok"

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
        user_prompt = format_user_prompt(
            title=title,
            text=text[:_MAX_TEXT_CHARS],
            context=context,
        )
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_V1},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=self._max_tokens,
        )
        raw = response.choices[0].message.content
        if not raw:
            raise ValueError("Grok returned empty content — possible refusal")
        result = LLMAnalysisOutput.model_validate_json(raw)
        # NEO-F-003: without these the DB audit trail skipped every Grok success
        # and the token columns stayed at zero. Mirrors openai/provider.py:91-95.
        result.raw_prompt = user_prompt
        result.raw_response = raw
        usage = getattr(response, "usage", None)
        if usage is not None:
            # isinstance-gated: LLMAnalysisOutput is strict + validate_assignment,
            # so a non-int usage field must not blow up an otherwise valid answer.
            prompt_tokens = getattr(usage, "prompt_tokens", 0)
            completion_tokens = getattr(usage, "completion_tokens", 0)
            if isinstance(prompt_tokens, int):
                result.prompt_tokens = prompt_tokens
            if isinstance(completion_tokens, int):
                result.completion_tokens = completion_tokens
        return result

    @classmethod
    def from_settings(cls, settings: Any) -> GrokAnalysisProvider:
        """Construct from ProviderSettings. Caller must check xai_fallback_enabled."""
        return cls(
            api_key=settings.xai_api_key,
            model=settings.xai_model,
            timeout=settings.xai_timeout,
        )
