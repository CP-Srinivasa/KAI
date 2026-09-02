from __future__ import annotations

from pathlib import Path

import pytest

from app.core.settings import AppSettings, InferenceSettings
from app.inference.status import build_inference_status


class _Router:
    configured_routes = {"standard": "kai-standard"}
    circuit_snapshot = {"standard:kai-standard": {"state": "closed"}}

    async def gateway_reachable(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_disabled_status_is_read_only_and_does_not_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    router = _Router()

    async def unexpected_probe() -> bool:
        raise AssertionError("disabled mode must not probe the gateway")

    router.gateway_reachable = unexpected_probe  # type: ignore[method-assign]
    monkeypatch.setattr("app.inference.status.get_inference_router", lambda: router)
    settings = AppSettings(_env_file=None)
    settings.inference = InferenceSettings(
        enabled=False,
        mode="primary",
        telemetry_path=str(tmp_path / "none.jsonl"),
        _env_file=None,
    )
    status = await build_inference_status(settings)
    assert status["mode"] == "off"
    assert status["gateway_reachable"] is None
    assert status["execution_authority"] is False
    assert "gateway_api_key" not in str(status)


@pytest.mark.asyncio
async def test_enabled_status_reports_gateway_and_honest_unknown_cost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("app.inference.status.get_inference_router", lambda: _Router())
    settings = AppSettings(_env_file=None)
    settings.inference = InferenceSettings(
        enabled=True,
        mode="shadow",
        telemetry_path=str(tmp_path / "none.jsonl"),
        _env_file=None,
    )
    status = await build_inference_status(settings)
    assert status["mode"] == "shadow"
    assert status["gateway_reachable"] is True
    assert status["telemetry_24h"]["estimated_cost_usd"] is None
