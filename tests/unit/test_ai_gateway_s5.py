"""LiteLLM-v2 Sprint 5 control-plane acceptance tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ai.audit import llm_call_scope
from app.ai.config import InferenceSettings
from app.ai.runtime import LiteLLMRequest, invoke
from app.integrations.litellm.provider import LiteLLMConfig


def _settings(
    mode: str,
    *,
    purpose_route: str = "standard",
    max_attempts: int = 3,
) -> InferenceSettings:
    return InferenceSettings(
        enabled=True,
        mode_ceiling=mode,
        route_modes={purpose_route: mode},
        max_attempts=max_attempts,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        jitter_max_seconds=0.0,
    )


def _factory(handler: Callable[[httpx.Request], httpx.Response]):
    def build(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return build


def _chat_request() -> LiteLLMRequest[str]:
    def parse(body: dict[str, Any]) -> str:
        return str(body["choices"][0]["message"]["content"])

    return LiteLLMRequest(parser=parse, payload={"messages": []})


def _response(request: httpx.Request, *, status: int = 200, cost: str | None = "0.1"):
    headers = {
        "x-litellm-model-provider": "openai",
        "x-litellm-model": "gpt-4o-mini",
    }
    if cost is not None:
        headers["x-litellm-response-cost"] = cost
    return httpx.Response(
        status,
        json={
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": "lite"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
        headers=headers,
        request=request,
    )


async def _no_sleep(_: float) -> None:
    return None


def test_inference_settings_are_fail_safe_by_default() -> None:
    settings = InferenceSettings(_env_file=None)
    assert settings.enabled is False
    assert settings.mode_ceiling == "off"
    assert settings.litellm_base_url == "http://127.0.0.1:4000"
    assert settings.max_attempts == 3


def test_localhost_boundary_rejects_prefix_and_userinfo_tricks() -> None:
    assert LiteLLMConfig(base_url="http://127.0.0.1:4000").is_local
    assert LiteLLMConfig(base_url="http://[::1]:4000").is_local
    assert not LiteLLMConfig(base_url="http://localhost.evil.example:4000").is_local
    assert not LiteLLMConfig(base_url="http://localhost@evil.example:4000").is_local


async def test_off_calls_direct_and_never_constructs_litellm_client() -> None:
    seen: list[str] = []

    async def direct() -> str:
        seen.append("direct")
        return "direct"

    def forbidden_factory(**_: Any) -> httpx.AsyncClient:
        raise AssertionError("OFF must not construct a LiteLLM client")

    result = await invoke(
        purpose="analysis",
        direct_call=direct,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=InferenceSettings(enabled=False),
        client_factory=forbidden_factory,
    )
    assert result.value == "direct"
    assert result.transport == "direct"
    assert seen == ["direct"]


async def test_unknown_mode_and_global_primary_without_route_graduation_are_off() -> None:
    calls = 0

    async def direct() -> str:
        return "direct"

    def forbidden(**_: Any) -> httpx.AsyncClient:
        nonlocal calls
        calls += 1
        raise AssertionError

    for settings in (
        InferenceSettings(enabled=True, mode_ceiling="typo", route_modes={"standard": "primary"}),
        InferenceSettings(enabled=True, mode_ceiling="primary", route_modes={}),
    ):
        result = await invoke(
            purpose="analysis",
            direct_call=direct,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=_chat_request(),
            settings=settings,
            client_factory=forbidden,
        )
        assert result.transport == "direct"
    assert calls == 0


async def test_shadow_runs_both_but_direct_is_authoritative() -> None:
    direct_calls = 0
    lite_calls = 0

    async def direct() -> str:
        nonlocal direct_calls
        direct_calls += 1
        return "direct"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lite_calls
        lite_calls += 1
        return _response(request)

    result = await invoke(
        purpose="analysis",
        direct_call=direct,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("shadow"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "direct"
    assert result.transport == "direct"
    assert direct_calls == lite_calls == 1
    assert result.outcome is not None
    assert result.outcome.gateway.shadow is result.outcome.gateway.litellm
    assert not result.outcome.gateway.litellm.execution_authority


async def test_consensus_primary_is_clamped_to_shadow() -> None:
    async def direct() -> str:
        return "direct-consensus"

    result = await invoke(
        purpose="consensus",
        direct_call=direct,
        direct_provider="openai",
        direct_model="gpt-4o-mini",
        litellm=_chat_request(),
        settings=_settings("primary", purpose_route="reasoning"),
        client_factory=_factory(_response),
        sleeper=_no_sleep,
    )
    assert result.value == "direct-consensus"
    assert result.outcome is not None
    assert result.outcome.gateway.mode == "shadow"
    assert result.outcome.gateway.authoritative is result.outcome.gateway.direct


@pytest.mark.parametrize("status", [400, 401, 402, 403])
async def test_non_retryable_http_errors_are_not_retried(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(request, status=status)

    async def direct() -> str:
        return "fallback"

    result = await invoke(
        purpose="analysis",
        direct_call=direct,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "fallback"
    assert calls == 1


async def test_quota_marker_on_429_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            json={"error": {"type": "insufficient_quota", "message": "quota exhausted"}},
            request=request,
        )

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("fallback"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "fallback"
    assert calls == 1
    assert result.outcome is not None
    assert result.outcome.litellm_attempts[0].trace.error_class == "quota"


async def test_schema_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": []}, request=request)

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("fallback"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "fallback"
    assert calls == 1
    assert result.outcome is not None
    assert result.outcome.litellm_attempts[0].trace.error_class == "schema"


async def _value(value: str) -> str:
    return value


@pytest.mark.parametrize("failure", [408, 429, 500, 503])
async def test_retryable_http_failure_retries_then_succeeds(failure: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(request, status=failure if calls == 1 else 200)

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direct"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "lite"
    assert result.transport == "litellm"
    assert calls == 2
    assert result.outcome is not None
    assert len(result.outcome.litellm_attempts) == 2


@pytest.mark.parametrize("kind", ["timeout", "transport"])
async def test_retryable_network_failure_is_bounded(kind: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if kind == "timeout":
            raise httpx.ReadTimeout("late", request=request)
        raise httpx.ConnectError("down", request=request)

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direct-fallback"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary", max_attempts=3),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "direct-fallback"
    assert calls == 3
    assert result.outcome is not None
    assert len(result.outcome.litellm_attempts) == 3


async def test_unknown_retry_cost_makes_total_unknown_and_attempts_share_correlation(
    tmp_path: Path,
) -> None:
    calls = 0
    sink = tmp_path / "llm.jsonl"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(
            request,
            status=500 if calls == 1 else 200,
            cost=None if calls == 1 else "0.2",
        )

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direct"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        correlation_id="corr-s5",
        telemetry_path=sink,
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.outcome is not None
    assert result.outcome.gateway.litellm is not None
    assert result.outcome.gateway.litellm.total_cost_usd is None
    rows = [json.loads(line) for line in sink.read_text("utf-8").splitlines()]
    assert len(rows) == 2
    assert {row["correlation_id"] for row in rows} == {"corr-s5"}
    assert [row["attempt"] for row in rows] == [1, 2]
    assert rows[0]["cost_usd"] is None and rows[0]["cost_known"] is False
    assert rows[1]["actual_provider"] == "openai"
    assert rows[1]["actual_model"] == "gpt-4o-mini"
    assert rows[1]["identity_proven"] is True
    assert rows[1]["logical_route"] == "standard"
    assert rows[1]["execution_authority"] is True


async def test_direct_and_litellm_rows_share_correlation_in_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = tmp_path / "llm.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)

    async def direct() -> str:
        async with llm_call_scope(purpose="analysis", provider="openai", model="gpt-4o"):
            return "direct"

    await invoke(
        purpose="analysis",
        direct_call=direct,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("shadow"),
        correlation_id="corr-shared",
        telemetry_path=sink,
        client_factory=_factory(_response),
        sleeper=_no_sleep,
    )
    rows = [json.loads(line) for line in sink.read_text("utf-8").splitlines()]
    assert len(rows) == 2
    assert {row["correlation_id"] for row in rows} == {"corr-shared"}
    assert {row["transport"] for row in rows} == {"direct", "litellm"}


async def test_telemetry_writer_failure_does_not_break_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_lock(*_: Any, **__: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("app.observability.llm_telemetry.append_lock", fail_lock)
    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direct"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("shadow"),
        client_factory=_factory(_response),
        sleeper=_no_sleep,
    )
    assert result.value == "direct"


async def test_async_path_never_uses_sync_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("sync httpx.Client used in async caller")

    monkeypatch.setattr(httpx.Client, "post", forbidden)
    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direct"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("shadow"),
        client_factory=_factory(_response),
        sleeper=_no_sleep,
    )
    assert result.value == "direct"
