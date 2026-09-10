"""Kimi is a free-text research adviser, never a KAI decision authority."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.ai.budget import BUDGET_EXEMPT_ROUTES
from app.ai.config import InferenceSettings
from app.ai.modes import has_execution_authority, resolve_mode
from app.ai.research import (
    KIMI_RESEARCH_MAX_TOKENS,
    ResearchUnavailableError,
    research_advisory,
)


def _settings() -> InferenceSettings:
    return InferenceSettings(
        enabled=True,
        mode_ceiling="advisory",
        route_modes={"research": "advisory"},
        litellm_api_key="test-master-key",
        max_attempts=1,
    )


def _client_factory(
    handler: httpx.MockTransport,
) -> Callable[..., httpx.AsyncClient]:
    def build(*, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=handler, timeout=timeout)

    return build


@pytest.mark.asyncio
async def test_research_preserves_markdown_and_carries_measured_metadata(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            headers={
                "x-litellm-model-name": "moonshot/kimi-k2.6",
                "x-litellm-response-cost": "0.00123",
                "x-litellm-attempted-retries": "0",
            },
            json={
                "model": "kai-kimi-research",
                "choices": [
                    {
                        "message": {"content": "## Gegenargument\n\n```json\n{bad}\n```"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 34,
                    "completion_tokens_details": {"reasoning_tokens": 21},
                },
            },
        )

    telemetry = tmp_path / "llm.jsonl"
    result = await research_advisory(
        "Prüfe die Gegenthese.",
        settings=_settings(),
        correlation_id="kimi-research-test",
        telemetry_path=telemetry,
        client_factory=_client_factory(httpx.MockTransport(handle)),
    )

    assert captured["model"] == "kai-kimi-research"
    assert captured["max_tokens"] == KIMI_RESEARCH_MAX_TOKENS == 4096
    assert "response_format" not in captured
    assert result.content == "## Gegenargument\n\n```json\n{bad}\n```"
    assert result.route == "research"
    assert result.requested_model_alias == "kai-kimi-research"
    assert result.provider == "moonshot"
    assert result.model == "moonshot/kimi-k2.6"
    assert result.http_status == 200
    assert result.cost_usd == pytest.approx(0.00123)
    assert result.input_tokens == 12
    assert result.output_tokens == 34
    assert result.reasoning_tokens == 21
    assert result.finish_reason == "stop"
    assert result.truncated is False
    assert result.retries == 0
    assert result.fallbacks == 0
    assert result.correlation_id == "kimi-research-test"
    assert not result.execution_authority
    assert not result.analysis_result_authority
    assert not result.alert_authority

    row = json.loads(telemetry.read_text(encoding="utf-8").splitlines()[0])
    assert row["role"] == "advisory"
    assert row["logical_route"] == "research"
    assert row["execution_authority"] is False
    assert row["finish_reason"] == "stop"
    assert row["truncated"] is False
    assert row["correlation_id"] == "kimi-research-test"
    assert row["use_case"] == "research"


@pytest.mark.asyncio
async def test_missing_credential_fails_closed_without_advisory_output() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer test-master-key"
        return httpx.Response(401, json={"error": "missing moonshot credential"})

    with pytest.raises(ResearchUnavailableError, match="unavailable"):
        await research_advisory(
            "Research",
            settings=_settings(),
            client_factory=_client_factory(httpx.MockTransport(handle)),
        )


@pytest.mark.asyncio
async def test_truncated_research_fails_closed() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-litellm-model-name": "moonshot/kimi-k2.6"},
            json={"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]},
        )

    with pytest.raises(ResearchUnavailableError, match="unavailable"):
        await research_advisory(
            "Research",
            settings=_settings(),
            client_factory=_client_factory(httpx.MockTransport(handle)),
        )


def test_advisory_is_confined_to_research_and_never_executes() -> None:
    assert (
        resolve_mode("research", per_route={"research": "primary"}, ceiling="primary") == "advisory"
    )
    assert (
        resolve_mode("standard", per_route={"standard": "advisory"}, ceiling="advisory") == "shadow"
    )
    assert not has_execution_authority("advisory")


def test_research_timeout_is_bounded_without_changing_other_routes() -> None:
    settings = InferenceSettings()
    assert settings.route_timeout_seconds["research"] == 180.0
    assert settings.timeout_seconds == 30.0
    with pytest.raises(ValueError):
        InferenceSettings(route_timeout_seconds={"research": 301.0})


def test_research_uses_normal_budget_policy_and_defaults_to_off() -> None:
    settings = InferenceSettings()
    assert "research" not in BUDGET_EXEMPT_ROUTES
    assert (
        resolve_mode("research", per_route=settings.route_modes, ceiling=settings.mode_ceiling)
        == "off"
    )


def test_litellm_route_is_fixed_and_contains_no_secret() -> None:
    config = (Path(__file__).resolve().parents[2] / "config" / "litellm.yaml").read_text(
        encoding="utf-8"
    )
    route = config.split("model_name: kai-kimi-research", 1)[1].split("litellm_settings:", 1)[0]
    assert "model: moonshot/kimi-k2.6" in route
    assert "api_key" not in route.lower()
