"""Analysis-provider adapters for off/shadow/primary migration modes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic
from typing import Any

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.analysis.prompts import SYSTEM_PROMPT_V1, format_user_prompt
from app.core.settings import InferenceSettings
from app.inference.models import InferenceRoute
from app.inference.router import InferenceRouter, get_inference_router
from app.inference.shadow import (
    record_analysis_shadow_comparison,
    record_analysis_shadow_failure,
)
from app.observability.llm_telemetry import record_llm_call

_MAX_TEXT_CHARS = 6000


class GatewayAnalysisProvider(BaseAnalysisProvider):
    """Strict KAI-side analysis validation over the LiteLLM proxy."""

    records_telemetry = True

    def __init__(
        self,
        router: InferenceRouter,
        *,
        route: InferenceRoute = InferenceRoute.STANDARD,
        role: str = "primary",
    ) -> None:
        self._router = router
        self._route = route
        self._role = role
        self._last_model: str | None = None

    @property
    def provider_name(self) -> str:
        return "litellm"

    @property
    def model(self) -> str | None:
        return self._last_model or self._router.settings.route_aliases.get(self._route.value)

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
        user_prompt += "\n\nReturn only one JSON object matching the requested schema."
        result = await self._router.chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_V1},
                {"role": "user", "content": user_prompt},
            ],
            route=self._route,
            response_model=LLMAnalysisOutput,
            role=self._role,
            max_tokens=1024,
            temperature=0.1,
        )
        if result.parsed is None:
            raise ValueError("gateway returned no validated LLMAnalysisOutput")
        output = result.parsed
        output.provider_used = result.actual_provider or "litellm"
        output.model_used = result.actual_model or result.requested_model_alias
        output.logical_route = self._route.value
        output.prompt_tokens = result.usage.prompt_tokens
        output.completion_tokens = result.usage.completion_tokens
        output.latency_ms = result.latency_ms
        output.estimated_cost_usd = result.estimated_cost_usd
        output.raw_prompt = user_prompt
        output.raw_response = result.content
        self._last_model = output.model_used
        return output


class InferenceModeAnalysisProvider(BaseAnalysisProvider):
    """Mode boundary that preserves the legacy provider as authority or fallback."""

    records_telemetry = True

    def __init__(
        self,
        *,
        mode: str,
        gateway: GatewayAnalysisProvider,
        legacy: BaseAnalysisProvider | None,
        settings: InferenceSettings,
    ) -> None:
        if mode not in {"shadow", "primary"}:
            raise ValueError("InferenceModeAnalysisProvider requires shadow or primary mode")
        if mode == "shadow" and legacy is None:
            raise ValueError("shadow mode requires an authoritative legacy provider")
        self._mode = mode
        self._gateway = gateway
        self._legacy = legacy
        self._settings = settings

    @property
    def provider_name(self) -> str:
        if self._mode == "shadow" and self._legacy is not None:
            return self._legacy.provider_name
        return "litellm"

    @property
    def model(self) -> str | None:
        if self._mode == "shadow" and self._legacy is not None:
            return self._legacy.model
        return self._gateway.model

    @property
    def provider_chain(self) -> list[str]:
        result = ["litellm"]
        if self._legacy is not None:
            result.append(self._legacy.provider_name)
        return result

    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput:
        if self._mode == "shadow":
            assert self._legacy is not None
            candidate_task = asyncio.create_task(self._gateway.analyze(title, text, context))
            try:
                current = await self._call_legacy(title, text, context, role="primary")
            except Exception:
                # Always retrieve the shadow outcome so a legacy failure cannot
                # leave an unobserved background-task exception behind.
                await asyncio.gather(candidate_task, return_exceptions=True)
                raise
            try:
                candidate = await candidate_task
            except Exception as exc:  # noqa: BLE001 -- shadow can never replace current
                record_analysis_shadow_failure(
                    title=title,
                    text=text,
                    current=current,
                    error_type=type(exc).__name__,
                    path=Path(self._settings.shadow_comparison_path),
                )
                return current
            record_analysis_shadow_comparison(
                title=title,
                text=text,
                current=current,
                candidate=candidate,
                path=Path(self._settings.shadow_comparison_path),
            )
            return current

        try:
            return await self._gateway.analyze(title, text, context)
        except Exception:
            if self._legacy is None:
                raise
            return await self._call_legacy(
                title,
                text,
                context,
                role="primary_fallback",
                fallback_reason="gateway_exhausted",
            )

    async def _call_legacy(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None,
        *,
        role: str,
        fallback_reason: str | None = None,
    ) -> LLMAnalysisOutput:
        assert self._legacy is not None
        started = monotonic()
        try:
            output = await self._legacy.analyze(title, text, context)
        except Exception as exc:
            record_llm_call(
                provider=self._legacy.provider_name,
                model=self._legacy.model or "unknown",
                ok=False,
                latency_ms=(monotonic() - started) * 1000.0,
                role=role,
                logical_route=InferenceRoute.STANDARD.value,
                requested_model_alias=self._legacy.model,
                fallback_reason=fallback_reason,
                error_type=type(exc).__name__,
                schema_validation="failed",
                path=Path(self._settings.telemetry_path),
            )
            raise
        latency_ms = (monotonic() - started) * 1000.0
        output.provider_used = output.provider_used or self._legacy.provider_name
        output.model_used = output.model_used or self._legacy.model
        output.logical_route = output.logical_route or InferenceRoute.STANDARD.value
        output.latency_ms = latency_ms
        record_llm_call(
            provider=output.provider_used,
            model=output.model_used or "unknown",
            ok=True,
            latency_ms=latency_ms,
            role=role,
            logical_route=output.logical_route,
            requested_model_alias=self._legacy.model,
            actual_provider=output.provider_used,
            actual_model=output.model_used,
            prompt_tokens=output.prompt_tokens,
            completion_tokens=output.completion_tokens,
            estimated_cost=output.estimated_cost_usd,
            fallback_count=int(fallback_reason is not None),
            fallback_reason=fallback_reason,
            schema_validation="passed",
            path=Path(self._settings.telemetry_path),
        )
        return output


def wrap_analysis_provider(
    legacy: BaseAnalysisProvider | None,
    settings: InferenceSettings,
    *,
    route: InferenceRoute = InferenceRoute.STANDARD,
) -> BaseAnalysisProvider | None:
    """Apply the default-off migration mode to one operational caller."""
    mode = settings.effective_mode
    if mode == "off":
        return legacy
    if mode == "shadow" and legacy is None:
        return None
    router = get_inference_router()
    gateway = GatewayAnalysisProvider(
        router,
        route=route,
        role="shadow" if mode == "shadow" else "primary",
    )
    return InferenceModeAnalysisProvider(
        mode=mode,
        gateway=gateway,
        legacy=legacy,
        settings=settings,
    )
