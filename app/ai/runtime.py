"""Productive async entry point for KAI callers.

Callers describe their existing direct operation and, optionally, how to parse
an OpenAI-compatible LiteLLM response. Mode, retry, circuit, budget and
authority remain inside ``app.ai``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Any, Final

import httpx

from app.ai.audit import Purpose, classify_error, correlation_scope
from app.ai.config import InferenceSettings
from app.ai.gateway import AsyncGatewayOutcome, execute_async
from app.ai.models import AttemptResult, AttemptTrace
from app.ai.modes import resolve_mode, unknown_route_keys
from app.ai.retry import RetryPolicy
from app.ai.routes import route_for
from app.core.logging import get_logger
from app.integrations.litellm.provider import LiteLLMConfig, call_litellm_async

logger = get_logger(__name__)


class LiteLLMCallError(RuntimeError):
    """A typed LiteLLM result was unavailable and no direct fallback existed."""


@dataclass(frozen=True)
class LiteLLMRequest[T]:
    """Transport input plus caller-owned response interpretation."""

    parser: Callable[[dict[str, Any]], T]
    payload: dict[str, Any] | None = None
    endpoint: str = "/v1/chat/completions"
    files: Any = None
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class RoutedValue[T]:
    value: T
    transport: str
    outcome: AsyncGatewayOutcome[T] | None = None


#: Hart abgeschaltete Rueckfallebene. Wird benutzt, wenn die Umgebung eine
#: unbrauchbare ``KAI_INFERENCE_*``-Variable enthaelt: ein Tippfehler in einer
#: Env-Variable darf den Direktpfad von Chat, Intent, STT und Consensus nicht
#: mitreissen. OFF ist der Rollback, nicht ein Fehlerzustand.
_HARD_OFF: Final = InferenceSettings.model_construct(
    enabled=False,
    mode_ceiling="off",
    route_modes={},
    route_aliases={},
    litellm_base_url="http://127.0.0.1:4000",
    litellm_api_key="",
    timeout_seconds=30.0,
    max_attempts=1,
    backoff_base_seconds=0.0,
    backoff_max_seconds=0.0,
    jitter_max_seconds=0.0,
)


@lru_cache(maxsize=1)
def environment_settings() -> InferenceSettings:
    """Die Umgebungsfassung — EINMAL gelesen, nicht pro Aufruf.

    ``BaseSettings()`` liest ``.env`` von der Platte. Das pro Chat-, Intent- und
    STT-Aufruf zu tun, waere blockierendes Datei-I/O im Event-Loop — genau die
    Klasse Fehler, gegen die Luecke B antritt. Zwischenspeicher statt Neubau;
    :func:`reset_environment_settings` macht ihn fuer Tests wieder auf.
    """
    try:
        return InferenceSettings()
    except Exception as exc:  # noqa: BLE001 - eine kaputte Env darf nicht werfen
        logger.error("ai_gateway_settings_invalid_falling_back_to_off", error=str(exc))
        return _HARD_OFF


def reset_environment_settings() -> None:
    """Zwischenspeicher leeren (Tests, Neustart nach Env-Wechsel)."""
    environment_settings.cache_clear()


def inference_settings(source: Any | None = None) -> InferenceSettings:
    """Resolve settings without making legacy caller test doubles grow fields."""
    if isinstance(source, InferenceSettings):
        return source
    candidate = getattr(source, "ai_gateway", None) if source is not None else None
    if isinstance(candidate, InferenceSettings):
        return candidate
    return environment_settings()


def _direct_trace(
    provider: str, model: str, latency_ms: float, exc: Exception | None
) -> AttemptTrace:
    return AttemptTrace(
        transport="direct",
        requested_model=model,
        latency_ms=latency_ms,
        actual_provider=provider if exc is None else "",
        actual_model=model if exc is None else "",
        error_class=classify_error(exc) if exc is not None else None,
        detail={"exception": type(exc).__name__} if exc is not None else {},
    )


async def invoke[T](
    *,
    purpose: Purpose,
    direct_call: Callable[[], Awaitable[T]],
    direct_provider: str,
    direct_model: str,
    litellm: LiteLLMRequest[T] | None,
    settings: InferenceSettings | Any | None = None,
    correlation_id: str | None = None,
    telemetry_path: Path | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    clock: Callable[[], float] = monotonic,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
    jitter: Callable[[], float] = lambda: 0.0,
) -> RoutedValue[T]:
    """Run one logical call; OFF is an exact direct-path fast return."""
    configured = inference_settings(settings)
    route = route_for(purpose)
    invalid_routes = unknown_route_keys(configured.route_modes)
    if invalid_routes:
        logger.warning("ai_gateway_unknown_route_keys", route_keys=invalid_routes)

    ceiling = configured.mode_ceiling if configured.enabled else "off"
    mode = resolve_mode(route, per_route=configured.route_modes, ceiling=ceiling)
    if purpose == "consensus" and mode == "primary":
        mode = "shadow"

    # This branch deliberately adds no network client, task or retry around the
    # legacy path. It is the hard rollback invariant, not merely a mode label.
    if mode == "off":
        with correlation_scope(correlation_id) as _:
            return RoutedValue(value=await direct_call(), transport="direct")

    with correlation_scope(correlation_id) as active_correlation:

        async def run_direct() -> AttemptResult[T]:
            started = clock()
            try:
                value = await direct_call()
            except Exception as exc:  # preserve and re-raise after policy selection
                return AttemptResult(
                    trace=_direct_trace(
                        direct_provider,
                        direct_model,
                        (clock() - started) * 1000.0,
                        exc,
                    ),
                    error=exc,
                )
            return AttemptResult(
                trace=_direct_trace(
                    direct_provider,
                    direct_model,
                    (clock() - started) * 1000.0,
                    None,
                ),
                value=value,
            )

        async def boundary_failure() -> AttemptResult[T]:
            exc = ValueError("LiteLLM base_url is outside the localhost boundary")
            return AttemptResult(
                trace=AttemptTrace(
                    transport="litellm",
                    requested_model=configured.route_aliases.get(route, route),
                    latency_ms=0.0,
                    error_class="schema",
                    detail={"exception": type(exc).__name__, "boundary": "non_local"},
                ),
                error=exc,
            )

        async def unavailable() -> AttemptResult[T]:
            exc = LiteLLMCallError("LiteLLM request is not defined for this caller")
            return AttemptResult(
                trace=AttemptTrace(
                    transport="litellm",
                    requested_model=configured.route_aliases.get(route, route),
                    latency_ms=0.0,
                    error_class="schema",
                    detail={"exception": type(exc).__name__},
                ),
                error=exc,
            )

        lite_config = LiteLLMConfig(
            base_url=configured.litellm_base_url,
            timeout_s=configured.timeout_seconds,
            api_key=configured.litellm_api_key,
        )
        async with client_factory(timeout=configured.timeout_seconds) as client:

            async def run_litellm() -> AttemptResult[T]:
                if litellm is None:
                    return await unavailable()
                if not lite_config.is_local:
                    return await boundary_failure()
                response = await call_litellm_async(
                    config=lite_config,
                    model=configured.route_aliases.get(route, route),
                    client=client,
                    monotonic=clock,
                    correlation_id=active_correlation,
                    endpoint=litellm.endpoint,
                    payload=litellm.payload,
                    files=litellm.files,
                    data=litellm.data,
                )
                if not response.trace.ok:
                    return AttemptResult(
                        trace=response.trace,
                        error=LiteLLMCallError(
                            f"LiteLLM attempt failed: {response.trace.error_class}"
                        ),
                    )
                try:
                    value = litellm.parser(response.body)
                except Exception as exc:
                    detail = {**response.trace.detail, "exception": type(exc).__name__}
                    return AttemptResult(
                        trace=replace(response.trace, error_class="schema", detail=detail),
                        error=exc,
                    )
                return AttemptResult(trace=response.trace, value=value)

            kwargs: dict[str, Any] = {}
            if sleeper is not None:
                kwargs["sleeper"] = sleeper
            outcome = await execute_async(
                purpose=purpose,
                alias=configured.route_aliases.get(route, route),
                direct_call=run_direct,
                litellm_call=run_litellm,
                per_route=configured.route_modes,
                ceiling=ceiling,
                retry_policy=RetryPolicy(
                    max_attempts=configured.max_attempts,
                    base_backoff_s=configured.backoff_base_seconds,
                    max_backoff_s=configured.backoff_max_seconds,
                    max_jitter_s=configured.jitter_max_seconds,
                ),
                jitter=jitter,
                clock=clock,
                correlation_id=active_correlation,
                telemetry_path=telemetry_path,
                **kwargs,
            )

    selected = outcome.authoritative_attempt
    if selected is None:
        raise LiteLLMCallError("AI control plane produced no authoritative result")
    if selected.error is not None:
        raise selected.error
    if selected.value is None:
        raise LiteLLMCallError("AI control plane produced an empty authoritative result")
    transport = "litellm" if selected in outcome.litellm_attempts else "direct"
    return RoutedValue(value=selected.value, transport=transport, outcome=outcome)


__all__ = [
    "LiteLLMCallError",
    "LiteLLMRequest",
    "RoutedValue",
    "environment_settings",
    "inference_settings",
    "invoke",
    "reset_environment_settings",
]
