"""TaskRouter — the single entry point of the layer (ADR 0015 §2).

Chain per call: settings gate -> provider dispatch -> schema validation ->
audit append. Every stage fails closed into an ``LLMResult(ok=False, ...)``;
no exception escapes, no provider is ever substituted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.intelligence.audit import DEFAULT_AUDIT_PATH, append_audit_record, build_audit_record
from app.intelligence.core import (
    FALLBACK_DISABLED,
    FALLBACK_SCHEMA_VIOLATION,
    FALLBACK_UNAVAILABLE,
    LLMProvider,
    LLMRequest,
    LLMResult,
)
from app.intelligence.providers import (
    ClaudeProvider,
    MockProvider,
    NoOpProvider,
    OllamaProvider,
)
from app.intelligence.settings import LlmSettings, get_llm_settings

# Shared result envelope for all shadow task types. Deliberately strict:
# additionalProperties=false so injected extra fields never leave the layer.
SHADOW_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "evidence", "confidence"],
    "additionalProperties": False,
}

TASK_TYPES = ("daily_review_summary", "anomaly_explain", "doc_qa")


def _validate_shadow_payload(data: dict[str, Any]) -> bool:
    import jsonschema

    try:
        jsonschema.validate(data, SHADOW_RESULT_SCHEMA)
    except jsonschema.ValidationError:
        return False
    return True


def build_provider(settings: LlmSettings) -> LLMProvider:
    if settings.provider == "mock":
        return MockProvider()
    if settings.provider == "ollama":
        return OllamaProvider(base_url=settings.ollama_base_url, model=settings.model)
    if settings.provider == "claude":
        from app.core.settings import get_settings

        providers = get_settings().providers
        return ClaudeProvider(
            api_key=providers.anthropic_api_key,
            model=settings.model or providers.anthropic_model,
        )
    return NoOpProvider()


class TaskRouter:
    def __init__(
        self,
        settings: LlmSettings | None = None,
        provider: LLMProvider | None = None,
        audit_path: Path = DEFAULT_AUDIT_PATH,
    ) -> None:
        self._settings = settings or get_llm_settings()
        self._provider = provider or build_provider(self._settings)
        self._audit_path = audit_path

    @property
    def settings(self) -> LlmSettings:
        return self._settings

    def run(
        self,
        task_type: str,
        prompt: str,
        input_refs: tuple[str, ...] = (),
        *,
        redaction_count: int = 0,
    ) -> LLMResult:
        request = LLMRequest(
            task_type=task_type,
            prompt=prompt,
            schema=SHADOW_RESULT_SCHEMA,
            input_refs=input_refs,
            max_tokens=self._settings.max_tokens,
            timeout_s=self._settings.timeout_s,
        )
        if task_type not in TASK_TYPES:
            result = LLMResult(
                ok=False,
                data=None,
                provider=self._provider.name,
                model="",
                latency_ms=0.0,
                fallback_reason=FALLBACK_UNAVAILABLE,
            )
        elif not self._settings.enabled or self._settings.mode != "shadow":
            result = LLMResult(
                ok=False,
                data=None,
                provider="noop",
                model="",
                latency_ms=0.0,
                fallback_reason=FALLBACK_DISABLED,
            )
        else:
            result = self._provider.complete(request)
            if result.ok and (result.data is None or not _validate_shadow_payload(result.data)):
                result = LLMResult(
                    ok=False,
                    data=None,
                    provider=result.provider,
                    model=result.model,
                    latency_ms=result.latency_ms,
                    fallback_reason=FALLBACK_SCHEMA_VIOLATION,
                )
        append_audit_record(
            build_audit_record(request, result, redaction_count=redaction_count),
            path=self._audit_path,
        )
        return result
