"""Bounded OpenAI-compatible client for the local LiteLLM gateway."""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable, Mapping
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.settings import InferenceSettings
from app.inference.budget import BudgetGovernor
from app.inference.circuit import CircuitBreaker, CircuitState
from app.inference.errors import (
    GatewayAttemptError,
    InferenceBudgetExceededError,
    InferenceCircuitOpenError,
    InferenceConfigurationError,
    InferenceExhaustedError,
)
from app.inference.models import AttemptTrace, InferenceResult, InferenceRoute, InferenceUsage
from app.observability.llm_telemetry import record_llm_call

ParsedT = TypeVar("ParsedT", bound=BaseModel)


def _positive_int(value: object) -> int:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class InferenceRouter:
    """KAI-side route, retry, fallback, circuit, schema and budget boundary."""

    def __init__(
        self,
        settings: InferenceSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        randomizer: Callable[[], float] = random.random,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._sleeper = sleeper
        self._randomizer = randomizer
        self._clock = clock
        self._circuit = CircuitBreaker(
            failure_threshold=settings.circuit_failure_threshold,
            recovery_seconds=settings.circuit_recovery_seconds,
            clock=clock,
        )
        self._budget = BudgetGovernor(settings)
        self._telemetry_path = Path(settings.telemetry_path)

    @property
    def configured_routes(self) -> dict[str, str]:
        return dict(sorted(self.settings.route_aliases.items()))

    @property
    def circuit_snapshot(self) -> dict[str, dict[str, object]]:
        return self._circuit.snapshot()

    def models_for_route(self, route: InferenceRoute) -> list[str]:
        primary = self.settings.route_aliases.get(route.value, "").strip()
        if not primary:
            raise InferenceConfigurationError(f"no model alias configured for route {route.value}")
        result: list[str] = []
        for alias in [primary, *self.settings.route_fallbacks.get(route.value, [])]:
            normalized = alias.strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    async def chat(
        self,
        *,
        messages: list[dict[str, str]],
        route: InferenceRoute = InferenceRoute.STANDARD,
        response_model: type[ParsedT] | None = None,
        role: str = "primary",
        request_id: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.1,
        estimated_request_cost_usd: float | None = None,
    ) -> InferenceResult[ParsedT]:
        correlation_id = request_id or str(uuid.uuid4())
        aliases = self.models_for_route(route)
        budget = self._budget.evaluate(
            route=route.value,
            estimated_request_cost_usd=estimated_request_cost_usd,
        )
        if not budget.allowed:
            record_llm_call(
                provider="litellm",
                model=aliases[0],
                ok=False,
                latency_ms=0.0,
                role=role,
                logical_route=route.value,
                requested_model_alias=aliases[0],
                fallback_reason=budget.reason,
                error_type="InferenceBudgetExceeded",
                request_id=correlation_id,
                schema_validation="not_attempted",
                path=self._telemetry_path,
            )
            raise InferenceBudgetExceededError(budget.reason or "inference budget refused request")

        attempt_number = retry_count = fallback_count = 0
        traces: list[AttemptTrace] = []
        reasons: list[str] = []
        abort_all = False
        final_error_type: str | None = None
        final_reason: str | None = "budget_soft_limit" if budget.soft_limit_exceeded else None

        for alias_index, alias in enumerate(aliases):
            if attempt_number >= self.settings.max_attempts:
                break
            retries_for_alias = 0
            while retries_for_alias <= self.settings.retries_per_model:
                if attempt_number >= self.settings.max_attempts:
                    break
                attempt_number += 1
                circuit_key = f"{route.value}:{alias}"
                try:
                    self._circuit.before_call(circuit_key)
                except InferenceCircuitOpenError as exc:
                    fallback_count += int(alias_index < len(aliases) - 1)
                    reason = "circuit_open"
                    reasons.append(f"{alias}:{reason}")
                    traces.append(
                        AttemptTrace(
                            attempt_number=attempt_number,
                            requested_model_alias=alias,
                            latency_ms=0.0,
                            success=False,
                            fallback_reason=reason,
                            error_type=type(exc).__name__,
                            circuit_state=CircuitState.OPEN.value,
                        )
                    )
                    self._record_attempt(
                        route=route,
                        alias=alias,
                        role=role,
                        request_id=correlation_id,
                        attempt_number=attempt_number,
                        latency_ms=0.0,
                        ok=False,
                        circuit_state=CircuitState.OPEN.value,
                        error_type=type(exc).__name__,
                        fallback_reason=reason,
                        fallback_to=self._next_alias(aliases, alias_index),
                    )
                    final_error_type = type(exc).__name__
                    final_reason = reason
                    break

                started = self._clock()
                try:
                    result = await self._attempt_chat(
                        messages=messages,
                        alias=alias,
                        route=route,
                        response_model=response_model,
                        request_id=correlation_id,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                except GatewayAttemptError as exc:
                    latency_ms = max(0.0, (self._clock() - started) * 1000.0)
                    after_state = self._circuit.record_failure(circuit_key)
                    reasons.append(f"{alias}:{exc.reason}")
                    final_error_type = exc.error_type
                    final_reason = exc.reason
                    will_retry = (
                        exc.retryable
                        and retries_for_alias < self.settings.retries_per_model
                        and attempt_number < self.settings.max_attempts
                    )
                    fallback_to = None
                    if not will_retry and exc.fallback_allowed:
                        fallback_to = self._next_alias(aliases, alias_index)
                        fallback_count += int(fallback_to is not None)
                    traces.append(
                        AttemptTrace(
                            attempt_number=attempt_number,
                            requested_model_alias=alias,
                            latency_ms=latency_ms,
                            success=False,
                            fallback_reason=exc.reason,
                            error_type=exc.error_type,
                            circuit_state=after_state.value,
                        )
                    )
                    self._record_attempt(
                        route=route,
                        alias=alias,
                        role=role,
                        request_id=correlation_id,
                        attempt_number=attempt_number,
                        latency_ms=latency_ms,
                        ok=False,
                        circuit_state=after_state.value,
                        error_type=exc.error_type,
                        fallback_reason=exc.reason,
                        fallback_to=fallback_to,
                    )
                    if will_retry:
                        retry_count += 1
                        retries_for_alias += 1
                        await self._sleeper(self._backoff_seconds(retries_for_alias))
                        continue
                    if not exc.fallback_allowed:
                        abort_all = True
                    break

                latency_ms = max(0.0, (self._clock() - started) * 1000.0)
                self._circuit.record_success(circuit_key)
                successful_trace = AttemptTrace(
                    attempt_number=attempt_number,
                    requested_model_alias=alias,
                    actual_provider=result.actual_provider,
                    actual_model=result.actual_model,
                    latency_ms=latency_ms,
                    success=True,
                    circuit_state=CircuitState.CLOSED.value,
                )
                traces.append(successful_trace)
                complete = InferenceResult[ParsedT](
                    request_id=correlation_id,
                    route=route,
                    requested_model_alias=alias,
                    actual_provider=result.actual_provider,
                    actual_model=result.actual_model,
                    content=result.content,
                    parsed=result.parsed,
                    usage=result.usage,
                    estimated_cost_usd=result.estimated_cost_usd,
                    latency_ms=latency_ms,
                    retry_count=retry_count,
                    fallback_count=fallback_count,
                    schema_validation=result.schema_validation,
                    attempts=tuple(traces),
                )
                self._record_final(complete, role=role, fallback_reason=final_reason)
                return complete

            if abort_all:
                break

        record_llm_call(
            provider="litellm",
            model=aliases[0] if aliases else "unknown",
            ok=False,
            latency_ms=sum(trace.latency_ms for trace in traces),
            role=role,
            logical_route=route.value,
            requested_model_alias=aliases[0] if aliases else None,
            retry_count=retry_count,
            fallback_count=fallback_count,
            fallback_reason=final_reason,
            circuit_state=traces[-1].circuit_state if traces else None,
            request_id=correlation_id,
            schema_validation="failed" if traces else "not_attempted",
            error_type=final_error_type or "InferenceExhausted",
            path=self._telemetry_path,
        )
        raise InferenceExhaustedError("all inference attempts failed", reasons=reasons)

    async def _attempt_chat(
        self,
        *,
        messages: list[dict[str, str]],
        alias: str,
        route: InferenceRoute,
        response_model: type[ParsedT] | None,
        request_id: str,
        max_tokens: int,
        temperature: float,
    ) -> InferenceResult[ParsedT]:
        payload: dict[str, Any] = {
            "model": alias,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "user": request_id,
        }
        if response_model is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": response_model.model_json_schema(),
                },
            }
        headers = {"Content-Type": "application/json", "X-Request-ID": request_id}
        if self.settings.gateway_api_key:
            headers["Authorization"] = f"Bearer {self.settings.gateway_api_key}"
        endpoint = f"{self.settings.gateway_url.rstrip('/')}/chat/completions"
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise GatewayAttemptError(
                "gateway timeout",
                reason="timeout",
                retryable=True,
                error_type=type(exc).__name__,
            ) from exc
        except httpx.TransportError as exc:
            raise GatewayAttemptError(
                "gateway unavailable",
                reason="provider_unavailable",
                retryable=True,
                error_type=type(exc).__name__,
            ) from exc

        if response.status_code >= 400:
            raise self._http_error(response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise GatewayAttemptError(
                "gateway returned malformed JSON",
                reason="malformed_response",
                retryable=False,
                error_type=type(exc).__name__,
            ) from exc
        if not isinstance(data, dict):
            raise GatewayAttemptError(
                "gateway payload is not an object",
                reason="malformed_response",
                retryable=False,
            )
        content = self._extract_content(data)
        parsed: ParsedT | None = None
        schema_validation = "not_requested"
        if response_model is not None:
            try:
                parsed = response_model.model_validate_json(content, strict=True)
            except (ValidationError, ValueError) as exc:
                raise GatewayAttemptError(
                    "gateway response violated KAI schema",
                    reason="schema_violation",
                    retryable=False,
                    error_type=type(exc).__name__,
                ) from exc
            schema_validation = "passed"

        usage = self._usage(data.get("usage"))
        actual_provider, actual_model = self._actual_identity(data, response.headers)
        estimated_cost = self._response_cost(
            data=data,
            headers=response.headers,
            alias=alias,
            provider=actual_provider,
            model=actual_model,
            usage=usage,
        )
        return InferenceResult[ParsedT](
            request_id=request_id,
            route=route,
            requested_model_alias=alias,
            actual_provider=actual_provider,
            actual_model=actual_model,
            content=content,
            parsed=parsed,
            usage=usage,
            estimated_cost_usd=estimated_cost,
            latency_ms=0.0,
            retry_count=0,
            fallback_count=0,
            schema_validation=schema_validation,
        )

    @staticmethod
    def _http_error(status_code: int) -> GatewayAttemptError:
        if status_code in {401, 403}:
            return GatewayAttemptError(
                f"gateway authentication failed ({status_code})",
                reason="authentication_error",
                retryable=False,
                fallback_allowed=False,
                error_type=f"HTTP{status_code}",
            )
        if status_code == 429:
            return GatewayAttemptError(
                "provider rate limited",
                reason="http_429",
                retryable=True,
                error_type="HTTP429",
            )
        if status_code >= 500:
            return GatewayAttemptError(
                "provider server error",
                reason="http_5xx",
                retryable=True,
                error_type=f"HTTP{status_code}",
            )
        if status_code in {400, 404, 422}:
            return GatewayAttemptError(
                "model alias unsupported",
                reason="unsupported_model",
                retryable=False,
                error_type=f"HTTP{status_code}",
            )
        return GatewayAttemptError(
            f"gateway request failed ({status_code})",
            reason="http_error",
            retryable=False,
            error_type=f"HTTP{status_code}",
        )

    @staticmethod
    def _extract_content(data: Mapping[str, object]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise GatewayAttemptError(
                "gateway response has no choices",
                reason="malformed_response",
                retryable=False,
            )
        message = choices[0].get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise GatewayAttemptError(
                "gateway response has no text content",
                reason="malformed_response",
                retryable=False,
            )
        content = str(message["content"]).strip()
        if not content:
            raise GatewayAttemptError(
                "gateway returned empty content",
                reason="malformed_response",
                retryable=False,
            )
        return content

    @staticmethod
    def _usage(raw: object) -> InferenceUsage:
        usage = raw if isinstance(raw, Mapping) else {}
        prompt = _positive_int(usage.get("prompt_tokens"))
        completion = _positive_int(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details")
        cached = None
        if isinstance(details, Mapping) and details.get("cached_tokens") is not None:
            cached = _positive_int(details.get("cached_tokens"))
        return InferenceUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=_positive_int(usage.get("total_tokens")) or prompt + completion,
            cached_tokens=cached,
        )

    @staticmethod
    def _actual_identity(
        data: Mapping[str, object], headers: Mapping[str, str]
    ) -> tuple[str | None, str | None]:
        hidden = data.get("_hidden_params")
        hidden_map = hidden if isinstance(hidden, Mapping) else {}
        provider_raw = (
            hidden_map.get("custom_llm_provider")
            or data.get("provider")
            or headers.get("x-litellm-custom-llm-provider")
        )
        model_raw = (
            hidden_map.get("model_id") or data.get("model") or headers.get("x-litellm-model-id")
        )
        provider = str(provider_raw).strip() if provider_raw else None
        model = str(model_raw).strip() if model_raw else None
        return provider or None, model or None

    def _response_cost(
        self,
        *,
        data: Mapping[str, object],
        headers: Mapping[str, str],
        alias: str,
        provider: str | None,
        model: str | None,
        usage: InferenceUsage,
    ) -> float | None:
        hidden = data.get("_hidden_params")
        hidden_map = hidden if isinstance(hidden, Mapping) else {}
        raw = headers.get("x-litellm-response-cost") or hidden_map.get("response_cost")
        if raw is not None:
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                pass
        keys = [f"{provider}/{model}" if provider and model else "", model or "", alias]
        for key in keys:
            price = self.settings.model_prices_usd.get(key)
            if not price:
                continue
            input_price = price.get("input_per_million")
            output_price = price.get("output_per_million")
            if input_price is None or output_price is None:
                return None
            return (
                usage.prompt_tokens * float(input_price)
                + usage.completion_tokens * float(output_price)
            ) / 1_000_000.0
        return None

    def _record_attempt(
        self,
        *,
        route: InferenceRoute,
        alias: str,
        role: str,
        request_id: str,
        attempt_number: int,
        latency_ms: float,
        ok: bool,
        circuit_state: str,
        error_type: str | None,
        fallback_reason: str | None,
        fallback_to: str | None,
    ) -> None:
        record_llm_call(
            provider="litellm",
            model=alias,
            ok=ok,
            latency_ms=latency_ms,
            role=role,
            logical_route=route.value,
            requested_model_alias=alias,
            fallback_reason=fallback_reason,
            fallback_from=alias if fallback_to else None,
            fallback_to=fallback_to,
            attempt_number=attempt_number,
            circuit_state=circuit_state,
            request_id=request_id,
            schema_validation="failed" if not ok else "passed",
            error_type=error_type,
            event_scope="attempt",
            path=self._telemetry_path,
        )

    def _record_final(
        self,
        result: InferenceResult[ParsedT],
        *,
        role: str,
        fallback_reason: str | None,
    ) -> None:
        record_llm_call(
            provider=result.actual_provider or "unknown",
            model=result.actual_model or result.requested_model_alias,
            ok=True,
            latency_ms=result.latency_ms,
            role=role,
            logical_route=result.route.value,
            requested_model_alias=result.requested_model_alias,
            actual_provider=result.actual_provider,
            actual_model=result.actual_model,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            cached_tokens=result.usage.cached_tokens,
            estimated_cost=result.estimated_cost_usd,
            retry_count=result.retry_count,
            fallback_count=result.fallback_count,
            fallback_reason=fallback_reason,
            circuit_state=CircuitState.CLOSED.value,
            request_id=result.request_id,
            schema_validation=result.schema_validation,
            path=self._telemetry_path,
        )

    def _backoff_seconds(self, retry_number: int) -> float:
        base = self.settings.backoff_base_seconds * (2 ** max(0, retry_number - 1))
        capped = min(self.settings.backoff_max_seconds, base)
        return float(capped * (0.5 + self._randomizer() * 0.5))

    @staticmethod
    def _next_alias(aliases: list[str], alias_index: int) -> str | None:
        next_index = alias_index + 1
        return aliases[next_index] if next_index < len(aliases) else None

    async def gateway_reachable(self) -> bool:
        headers: dict[str, str] = {}
        if self.settings.gateway_api_key:
            headers["Authorization"] = f"Bearer {self.settings.gateway_api_key}"
        base = self.settings.gateway_url.removesuffix("/v1").rstrip("/")
        for path in ("/health/liveliness", "/health"):
            try:
                async with httpx.AsyncClient(
                    timeout=min(5.0, self.settings.timeout_seconds),
                    transport=self._transport,
                ) as client:
                    response = await client.get(f"{base}{path}", headers=headers)
                if response.status_code < 500:
                    return response.status_code < 400
            except httpx.HTTPError:
                continue
        return False


@lru_cache(maxsize=1)
def get_inference_router() -> InferenceRouter:
    """Process-wide router so circuit state is shared and operator-visible."""
    from app.core.settings import get_settings

    return InferenceRouter(get_settings().inference)
