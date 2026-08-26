from __future__ import annotations

import asyncio
import logging
import threading
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.api.main import app
from app.api.routers import dashboard as dashboard_mod
from app.api.routers.dashboard_quality_cache import SingleFlightCache

FIXED_HOLD_REPORT: dict[str, Any] = {
    "generated_at": "2026-08-26T12:00:00+00:00",
    "signal_quality_validation": {"resolved_precision_pct": 50.0},
    "alert_hit_rate_evidence": {"resolved_directional_documents": 2},
    "paper_trading_evidence": {"loop_metrics": {"total_cycles": 3}},
    "hold_gate_evaluation": {"overall_status": "hold_remains_active"},
}


def _reset_hold_state(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = SingleFlightCache(
        refresh=dashboard_mod._refresh_hold_report,
        ttl_s=dashboard_mod._HOLD_CACHE_TTL_S,
        include_cache_metadata=False,
    )
    monkeypatch.setattr(dashboard_mod, "_hold_cache", {"at": 0.0, "report": None})
    monkeypatch.setattr(dashboard_mod, "_hold_cache_sf", cache)
    monkeypatch.setattr(dashboard_mod, "_source_map_cache", {"at": 0.0, "map": None})


async def _fixed_source_by_doc() -> dict[str, str]:
    return {"doc-1": "decrypt"}


def _no_validate(*_args: Any, **_kwargs: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_live_hold_report_returns_golden_payload_and_uses_ttl_cache(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _reset_hold_state(monkeypatch)
    caplog.set_level(logging.INFO, logger=dashboard_mod.logger.name)
    monkeypatch.setattr(dashboard_mod, "_load_source_by_doc", _fixed_source_by_doc)
    monkeypatch.setattr(dashboard_mod, "_validate_dashboard_stream", _no_validate)
    call_count = 0

    def fixed_builder(**kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        assert kwargs["source_by_doc"] == {"doc-1": "decrypt"}
        return FIXED_HOLD_REPORT

    monkeypatch.setattr(dashboard_mod, "build_hold_metrics_report", fixed_builder)

    first = await dashboard_mod._live_hold_report()
    second = await dashboard_mod._live_hold_report()

    assert first == FIXED_HOLD_REPORT
    assert second == FIXED_HOLD_REPORT
    assert "cache" not in first
    assert call_count == 1
    assert "dashboard_hold_report_computed compute_ms=" in caplog.text


@pytest.mark.asyncio
async def test_live_hold_report_offloads_blocking_builder_allows_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_hold_state(monkeypatch)
    monkeypatch.setattr(dashboard_mod, "_load_source_by_doc", _fixed_source_by_doc)
    monkeypatch.setattr(dashboard_mod, "_validate_dashboard_stream", _no_validate)

    def slow_builder(**_kwargs: Any) -> dict[str, Any]:
        time.sleep(1.5)
        return {"marker": "slow"}

    monkeypatch.setattr(dashboard_mod, "build_hold_metrics_report", slow_builder)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started_at = time.perf_counter()
        hold_task = asyncio.create_task(dashboard_mod._live_hold_report())
        await asyncio.sleep(0.05)
        health_response = await client.get("/health")
        elapsed = time.perf_counter() - started_at
        hold_response = await hold_task

    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert elapsed < 0.2
    assert hold_response == {"marker": "slow"}


@pytest.mark.asyncio
async def test_live_hold_report_singleflight_empty_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_hold_state(monkeypatch)
    monkeypatch.setattr(dashboard_mod, "_load_source_by_doc", _fixed_source_by_doc)
    monkeypatch.setattr(dashboard_mod, "_validate_dashboard_stream", _no_validate)
    call_count = 0
    call_lock = threading.Lock()

    def counted_builder(**_kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        with call_lock:
            call_count += 1
        time.sleep(0.2)
        return {"marker": "singleflight"}

    monkeypatch.setattr(dashboard_mod, "build_hold_metrics_report", counted_builder)

    responses = await asyncio.gather(*(dashboard_mod._live_hold_report() for _ in range(5)))

    assert call_count == 1
    assert responses == [{"marker": "singleflight"}] * 5


@pytest.mark.asyncio
async def test_live_hold_report_builder_error_propagates_and_does_not_poison_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_hold_state(monkeypatch)
    monkeypatch.setattr(dashboard_mod, "_load_source_by_doc", _fixed_source_by_doc)
    monkeypatch.setattr(dashboard_mod, "_validate_dashboard_stream", _no_validate)
    mode = "broken"

    def builder(**_kwargs: Any) -> dict[str, Any]:
        if mode == "broken":
            raise RuntimeError("hold build failed")
        return {"marker": "ok"}

    monkeypatch.setattr(dashboard_mod, "build_hold_metrics_report", builder)

    with pytest.raises(RuntimeError, match="hold build failed"):
        await dashboard_mod._live_hold_report()

    mode = "ok"
    retry = await dashboard_mod._live_hold_report()

    assert retry == {"marker": "ok"}


@pytest.mark.asyncio
async def test_load_source_by_doc_offloads_blocking_audit_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_mod, "_source_map_cache", {"at": 0.0, "map": None})

    def slow_validate(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        time.sleep(1.5)
        return SimpleNamespace(rows=[])

    monkeypatch.setattr(dashboard_mod, "_validate_dashboard_stream", slow_validate)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started_at = time.perf_counter()
        source_task = asyncio.create_task(dashboard_mod._load_source_by_doc())
        await asyncio.sleep(0.05)
        health_response = await client.get("/health")
        elapsed = time.perf_counter() - started_at
        source_map = await source_task

    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert elapsed < 0.2
    assert source_map == {}
