from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from app.core.settings import InferenceSettings
from app.inference.circuit import CircuitBreaker, CircuitState
from app.inference.errors import (
    InferenceBudgetExceededError,
    InferenceCircuitOpenError,
    InferenceExhaustedError,
)
from app.inference.models import InferenceRoute
from app.inference.router import InferenceRouter
from app.observability.llm_telemetry import llm_telemetry_summary


class _Payload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    answer: str


def _settings(tmp_path: Path, **overrides: object) -> InferenceSettings:
    values: dict[str, object] = {
        "enabled": True,
        "mode": "primary",
        "route_aliases": {
            "bulk": "bulk-a",
            "standard": "standard-a",
            "reasoning": "reasoning-a",
            "critical": "critical-a",
            "stt": "stt-a",
        },
        "route_fallbacks": {"standard": ["standard-b"]},
        "retries_per_model": 0,
        "max_attempts": 4,
        "backoff_base_seconds": 0.0,
        "backoff_max_seconds": 0.0,
        "telemetry_path": str(tmp_path / "telemetry.jsonl"),
    }
    values.update(overrides)
    return InferenceSettings(_env_file=None, **values)


def _response(
    request: httpx.Request,
    *,
    answer: object = "ok",
    model: str = "provider-model",
    provider: str = "gemini",
    cost: str | None = "0.001",
) -> httpx.Response:
    headers = {"x-litellm-response-cost": cost} if cost is not None else {}
    return httpx.Response(
        200,
        request=request,
        headers=headers,
        json={
            "model": model,
            "_hidden_params": {"custom_llm_provider": provider},
            "choices": [{"message": {"content": json.dumps({"answer": answer})}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "prompt_tokens_details": {"cached_tokens": 3},
            },
        },
    )


@pytest.mark.asyncio
async def test_primary_success_validates_schema_and_accounts_usage(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _response(request)

    router = InferenceRouter(_settings(tmp_path), transport=httpx.MockTransport(handler))
    result = await router.chat(
        messages=[{"role": "user", "content": "redacted by telemetry"}],
        route=InferenceRoute.STANDARD,
        response_model=_Payload,
    )

    assert result.parsed == _Payload(answer="ok")
    assert result.actual_provider == "gemini"
    assert result.actual_model == "provider-model"
    assert result.usage.total_tokens == 14
    assert result.usage.cached_tokens == 3
    assert result.estimated_cost_usd == 0.001
    row = json.loads((tmp_path / "telemetry.jsonl").read_text("utf-8").splitlines()[-1])
    assert row["logical_route"] == "standard"
    assert row["actual_provider"] == "gemini"
    assert "redacted by telemetry" not in json.dumps(row)


@pytest.mark.asyncio
async def test_primary_failure_falls_back_once(tmp_path: Path) -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "standard-a":
            return httpx.Response(500, request=request)
        return _response(request, provider="openai", model="gpt-test")

    router = InferenceRouter(_settings(tmp_path), transport=httpx.MockTransport(handler))
    result = await router.chat(
        messages=[{"role": "user", "content": "x"}],
        response_model=_Payload,
    )
    assert calls == ["standard-a", "standard-b"]
    assert result.fallback_count == 1
    assert result.actual_provider == "openai"


@pytest.mark.asyncio
async def test_429_retries_same_model_with_bounded_backoff(tmp_path: Path) -> None:
    calls = 0
    sleeps: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, request=request)
        return _response(request)

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    settings = _settings(tmp_path, retries_per_model=1, route_fallbacks={})
    router = InferenceRouter(
        settings,
        transport=httpx.MockTransport(handler),
        sleeper=sleeper,
        randomizer=lambda: 0.0,
    )
    result = await router.chat(messages=[{"role": "user", "content": "x"}])
    assert calls == 2
    assert result.retry_count == 1
    assert sleeps == [0.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 404, 422])
async def test_unknown_model_falls_back_without_retry(tmp_path: Path, status: int) -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        if len(calls) == 1:
            return httpx.Response(status, request=request)
        return _response(request)

    router = InferenceRouter(_settings(tmp_path), transport=httpx.MockTransport(handler))
    result = await router.chat(messages=[{"role": "user", "content": "x"}])
    assert result.fallback_count == 1
    assert calls == ["standard-a", "standard-b"]


@pytest.mark.asyncio
async def test_auth_error_does_not_retry_or_fallback(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, request=request)

    router = InferenceRouter(_settings(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(InferenceExhaustedError):
        await router.chat(messages=[{"role": "user", "content": "x"}])
    assert calls == 1


@pytest.mark.asyncio
async def test_timeout_falls_back(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return _response(request)

    router = InferenceRouter(_settings(tmp_path), transport=httpx.MockTransport(handler))
    result = await router.chat(messages=[{"role": "user", "content": "x"}])
    assert result.fallback_count == 1


@pytest.mark.asyncio
async def test_malformed_gateway_json_falls_back(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, request=request, content=b"not-json")
        return _response(request)

    router = InferenceRouter(_settings(tmp_path), transport=httpx.MockTransport(handler))
    result = await router.chat(messages=[{"role": "user", "content": "x"}])
    assert result.fallback_count == 1


@pytest.mark.asyncio
async def test_schema_violation_falls_back(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _response(request, answer=123)
        return _response(request, answer="valid")

    router = InferenceRouter(_settings(tmp_path), transport=httpx.MockTransport(handler))
    result = await router.chat(messages=[{"role": "user", "content": "x"}], response_model=_Payload)
    assert result.parsed == _Payload(answer="valid")
    assert result.fallback_count == 1


@pytest.mark.asyncio
async def test_all_models_fail_with_hard_attempt_cap(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    settings = _settings(tmp_path, retries_per_model=2, max_attempts=3)
    router = InferenceRouter(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(InferenceExhaustedError):
        await router.chat(messages=[{"role": "user", "content": "x"}])
    assert calls == 3


@pytest.mark.asyncio
async def test_budget_hard_limit_blocks_without_network(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(request)

    settings = _settings(tmp_path, daily_hard_limit_usd=0.01)
    router = InferenceRouter(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(InferenceBudgetExceededError, match="daily_hard_limit"):
        await router.chat(
            messages=[{"role": "user", "content": "x"}],
            estimated_request_cost_usd=0.02,
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_budget_soft_limit_audits_but_does_not_block(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _response(request)

    settings = _settings(tmp_path, daily_soft_limit_usd=0.01)
    router = InferenceRouter(settings, transport=httpx.MockTransport(handler))
    result = await router.chat(
        messages=[{"role": "user", "content": "x"}],
        estimated_request_cost_usd=0.02,
    )
    assert result.actual_provider == "gemini"
    row = json.loads((tmp_path / "telemetry.jsonl").read_text("utf-8").splitlines()[-1])
    assert row["fallback_reason"] == "budget_soft_limit"


@pytest.mark.asyncio
async def test_unknown_cost_is_null_not_zero(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, cost=None)

    router = InferenceRouter(_settings(tmp_path), transport=httpx.MockTransport(handler))
    result = await router.chat(messages=[{"role": "user", "content": "x"}])
    assert result.estimated_cost_usd is None
    summary = llm_telemetry_summary(path=tmp_path / "telemetry.jsonl")
    assert summary["estimated_cost_usd"] is None
    assert summary["unknown_cost_calls"] == 1


def test_circuit_transitions_closed_open_half_open_closed() -> None:
    now = 100.0

    def clock() -> float:
        return now

    circuit = CircuitBreaker(failure_threshold=2, recovery_seconds=10.0, clock=clock)
    assert circuit.before_call("p") is CircuitState.CLOSED
    assert circuit.record_failure("p") is CircuitState.CLOSED
    circuit.before_call("p")
    assert circuit.record_failure("p") is CircuitState.OPEN
    with pytest.raises(InferenceCircuitOpenError):
        circuit.before_call("p")
    now = 111.0
    assert circuit.before_call("p") is CircuitState.HALF_OPEN
    assert circuit.record_success("p") is CircuitState.CLOSED
