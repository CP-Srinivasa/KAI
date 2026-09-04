"""Analysis adapter that puts the existing provider behind ``app.ai``."""

from __future__ import annotations

from typing import Any

from app.ai.config import InferenceSettings
from app.ai.runtime import LiteLLMRequest, invoke
from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.analysis.prompts import SYSTEM_PROMPT_V1, format_user_prompt

_MAX_TEXT_CHARS = 6000


class ControlPlaneAnalysisProvider(BaseAnalysisProvider):
    """Preserve the direct provider while adding governed shadow/primary transport."""

    def __init__(
        self,
        direct: BaseAnalysisProvider,
        settings: InferenceSettings,
        *,
        force_off: bool = False,
    ) -> None:
        self._direct = direct
        self._settings = settings.model_copy(update={"enabled": False}) if force_off else settings

    @property
    def provider_name(self) -> str:
        return self._direct.provider_name

    @property
    def model(self) -> str | None:
        return self._direct.model

    def __getattr__(self, name: str) -> Any:
        """Preserve ensemble/runtime metadata used by the existing pipeline."""
        return getattr(self._direct, name)

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

        def parse(body: dict[str, Any]) -> LLMAnalysisOutput:
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError("LiteLLM analysis response has no choices")
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            raw = message.get("content") if isinstance(message, dict) else None
            if not isinstance(raw, str) or not raw:
                raise ValueError("LiteLLM analysis response has no JSON content")
            output = LLMAnalysisOutput.model_validate_json(raw)
            output.raw_prompt = user_prompt
            output.raw_response = raw
            usage = body.get("usage")
            if isinstance(usage, dict):
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")
                if isinstance(prompt_tokens, int):
                    output.prompt_tokens = prompt_tokens
                if isinstance(completion_tokens, int):
                    output.completion_tokens = completion_tokens
            return output

        routed = await invoke(
            purpose="analysis",
            direct_call=lambda: self._direct.analyze(title=title, text=text, context=context),
            direct_provider=self._direct.provider_name,
            direct_model=self._direct.model or "",
            litellm=LiteLLMRequest(
                parser=parse,
                payload={
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT_V1},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 1024,
                },
            ),
            settings=self._settings,
        )
        output = routed.value
        if routed.transport == "litellm" and routed.outcome is not None:
            selected = routed.outcome.authoritative_attempt
            if selected is not None and selected.trace.identity_proven:
                output.provider_used = selected.trace.actual_provider
        return output


__all__ = ["ControlPlaneAnalysisProvider"]
