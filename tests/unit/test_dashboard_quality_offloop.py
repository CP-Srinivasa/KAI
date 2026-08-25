from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport

from app.api.main import app
from app.api.routers import dashboard as dashboard_mod
from app.api.routers.dashboard_quality_cache import SingleFlightCache

FIXED_NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        if tz is None:
            return FIXED_NOW.replace(tzinfo=None)
        return FIXED_NOW.astimezone(tz)


FIXED_REPORT: dict[str, Any] = {
    "generated_at": "2026-08-25T12:00:00+00:00",
    "signal_quality_validation": {
        "resolved_precision_pct": 66.67,
        "resolved_false_positive_rate_pct": 33.33,
        "active_precision_pct": 50.0,
        "priority_hit_correlation": None,
        "priority_tier_lift_pct": -5.0,
        "priority_tier_high_conviction_threshold": 10,
        "priority_tier_high_conviction_resolved": 2,
        "priority_tier_high_conviction_hit_rate_pct": 50.0,
        "priority_tier_high_conviction_ci_low_pct": 9.5,
        "priority_tier_high_conviction_ci_high_pct": 90.5,
        "priority_tier_standard_resolved": 4,
        "priority_tier_standard_hit_rate_pct": 55.0,
        "priority_tier_standard_ci_low_pct": 20.0,
        "priority_tier_standard_ci_high_pct": 80.0,
        "paper_real_price_cycle_count": 2,
        "directional_actionable_rate_pct": 12.5,
        "high_priority_hit_rate_pct": 50.0,
        "low_priority_hit_rate_pct": 25.0,
    },
    "alert_hit_rate_evidence": {
        "resolved_directional_documents": 6,
        "directional_alert_documents": 8,
        "alert_hits": 4,
        "alert_misses": 2,
        "active_resolved_directional_documents": 4,
        "active_alert_hits": 2,
        "active_alert_misses": 2,
        "legacy_resolved_documents": 2,
        "legacy_unknown_cutoff": "2026-05-01",
    },
    "paper_trading_evidence": {"loop_metrics": {"total_cycles": 3}},
    "hold_gate_evaluation": {
        "overall_status": "hold_remains_active",
        "blocking_reasons": ["resolved_directional_below_200"],
    },
    "forward_simulation": {
        "precision_pct": 50.0,
        "resolved": 2,
        "hits": 1,
        "miss": 1,
    },
    "per_source_active_precision": {"decrypt": {"n": 2, "precision_pct": 50.0}},
    "per_source_stability": {"decrypt": {"windows": 1}},
}

SOURCE_RELIABILITY: dict[str, Any] = {
    "status": "ok",
    "reliability_status": "ok",
    "generated_at": "2026-08-25T12:00:00+00:00",
    "window_days": 90,
    "thresholds": {"min_n_for_promote": 2},
    "quality_status": "ok",
    "health_warning": None,
    "trusted_count": 1,
    "source_count": 1,
    "active_sources_count": 1,
    "legacy_sources_count": 0,
    "unknown_sources_count": 0,
    "provisional_count": 0,
    "min_n": 2,
    "tier_counts": {"trusted": 1},
    "top_sources": [
        {
            "source_name": "decrypt",
            "hits": 2,
            "miss": 0,
            "n": 2,
            "point_estimate_pct": 100.0,
            "wilson_lower_95_pct": 34.2,
            "tier": "trusted",
            "priority_modifier": 1,
            "is_provisional": False,
            "sample_warning": None,
        }
    ],
    "unknown_bucket": None,
}


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _patch_artifact_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = {
        "_ARTIFACTS": tmp_path,
        "_ALERT_AUDIT": tmp_path / "alert_audit.jsonl",
        "_ALERT_OUTCOMES": tmp_path / "alert_outcomes.jsonl",
        "_TRADING_LOOP_AUDIT": tmp_path / "trading_loop_audit.jsonl",
        "_PAPER_EXECUTION_AUDIT": tmp_path / "paper_execution_audit.jsonl",
        "_BRIDGE_PENDING_ORDERS": tmp_path / "bridge_pending_orders.jsonl",
        "_ENTRY_WATCHER_AUDIT": tmp_path / "entry_watcher_audit.jsonl",
        "_AUDIT_V1_DISQUALIFIED_FLAG": tmp_path / "paper_execution_audit_v1_disqualified.flag",
        "_SOURCE_RELIABILITY_REPORT": tmp_path / "source_reliability.json",
        "_SHADOW_LEDGER": tmp_path / "shadow_candidate_ledger.jsonl",
    }
    for name, value in paths.items():
        monkeypatch.setattr(dashboard_mod, name, value)


def _write_quality_artifacts(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "symbol": "BTC/USDT",
                "timestamp_utc": "2026-08-25T10:00:00+00:00",
            },
            {
                "event_type": "order_filled",
                "symbol": "ETH/USDT",
                "timestamp_utc": "2026-08-25T11:00:00+00:00",
            },
            {
                "schema_version": "v2",
                "event_type": "position_closed",
                "symbol": "BTC/USDT",
                "trade_pnl_usd": 12.5,
                "timestamp_utc": "2026-08-25T10:30:00+00:00",
            },
            {
                "schema_version": "v2",
                "event_type": "position_partial_closed",
                "symbol": "ETH/USDT",
                "trade_pnl_usd": -2.5,
                "timestamp_utc": "2026-08-25T11:30:00+00:00",
            },
        ],
    )
    _write_jsonl(
        tmp_path / "alert_audit.jsonl",
        [
            {
                "document_id": "doc-aaa-111",
                "sentiment_label": "bullish",
                "priority": 10,
                "affected_assets": ["BTC/USDT"],
                "dispatched_at": "2026-08-25T09:00:00+00:00",
                "is_digest": False,
            },
            {
                "document_id": "doc-bbb-222",
                "sentiment_label": "bearish",
                "priority": 7,
                "affected_assets": ["ETH/USDT"],
                "dispatched_at": "2026-08-25T10:00:00+00:00",
                "is_digest": False,
            },
        ],
    )
    _write_jsonl(
        tmp_path / "alert_outcomes.jsonl",
        [
            {"document_id": "doc-aaa-111", "outcome": "hit"},
            {"document_id": "doc-bbb-222", "outcome": "miss"},
        ],
    )
    _write_jsonl(
        tmp_path / "trading_loop_audit.jsonl",
        [{"status": "completed"}, {"status": "no_signal"}],
    )
    for name in (
        "bridge_pending_orders.jsonl",
        "entry_watcher_audit.jsonl",
        "shadow_candidate_ledger.jsonl",
    ):
        (tmp_path / name).write_text("", encoding="utf-8")


async def _fixed_hold_report() -> dict[str, Any]:
    return FIXED_REPORT


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.value = start

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _reset_quality_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    clock: Callable[[], float] = time.monotonic,
    now_utc: Callable[[], datetime] = lambda: FIXED_NOW,
) -> SingleFlightCache:
    cache = SingleFlightCache(
        refresh=dashboard_mod._refresh_quality,
        ttl_s=dashboard_mod._QUALITY_CACHE_TTL_S,
        clock=clock,
        now_utc=now_utc,
    )
    monkeypatch.setattr(dashboard_mod, "_quality_cache_sf", cache)
    return cache


@pytest.fixture(autouse=True)
def reset_quality_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_quality_state(monkeypatch)
    yield


@pytest.fixture()
def fixed_quality_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    _patch_artifact_paths(monkeypatch, tmp_path)
    _write_quality_artifacts(tmp_path)
    monkeypatch.setattr(dashboard_mod, "datetime", FrozenDateTime)
    monkeypatch.setattr(dashboard_mod, "_live_hold_report", _fixed_hold_report)
    monkeypatch.setattr(
        dashboard_mod,
        "_load_source_reliability_summary",
        lambda: SOURCE_RELIABILITY,
    )
    monkeypatch.setattr(
        dashboard_mod,
        "_entry_runtime_block",
        lambda: {"entry_mode": "paper", "entry_mode_label": "paper (voll)"},
    )
    monkeypatch.setattr(
        dashboard_mod,
        "_shadow_attribution_24h",
        lambda: {
            "real_candidates_24h": 1,
            "probe_candidates_24h": 0,
            "unknown_candidates_24h": 0,
            "by_source_24h": {"technical_screener": 1},
        },
    )
    return tmp_path


def test_quality_endpoint_payload_is_golden_except_cache(
    fixed_quality_environment: Path,
) -> None:
    expected = dashboard_mod._build_quality_payload(FIXED_REPORT)

    with TestClient(app) as client:
        response = client.get("/dashboard/api/quality")

    assert response.status_code == 200
    payload = response.json()
    cache = payload.pop("cache")
    assert payload == expected
    assert cache["generated_at_utc"] == FIXED_NOW.isoformat()
    assert cache["stale"] is False
    assert cache["ttl_s"] == dashboard_mod._QUALITY_CACHE_TTL_S
    assert cache["age_s"] >= 0.0
    assert cache["compute_ms"] >= 0.0


@pytest.mark.asyncio
async def test_quality_endpoint_offloads_blocking_payload_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_quality_state(monkeypatch)
    monkeypatch.setattr(dashboard_mod, "_live_hold_report", _fixed_hold_report)

    def slow_builder(report: dict[str, Any]) -> dict[str, Any]:
        assert report == FIXED_REPORT
        time.sleep(1.5)
        return {"marker": "slow"}

    monkeypatch.setattr(dashboard_mod, "_build_quality_payload", slow_builder)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started_at = time.perf_counter()
        quality_task = asyncio.create_task(client.get("/dashboard/api/quality"))
        await asyncio.sleep(0.05)
        health_response = await client.get("/health")
        elapsed = time.perf_counter() - started_at
        quality_response = await quality_task

    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert elapsed < 0.2
    assert quality_response.status_code == 200
    assert quality_response.json()["marker"] == "slow"


@pytest.mark.asyncio
async def test_quality_endpoint_singleflight_empty_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_quality_state(monkeypatch)
    monkeypatch.setattr(dashboard_mod, "_live_hold_report", _fixed_hold_report)
    call_count = 0
    call_lock = threading.Lock()

    def counted_builder(report: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        assert report == FIXED_REPORT
        with call_lock:
            call_count += 1
        time.sleep(0.2)
        return {"marker": "singleflight"}

    monkeypatch.setattr(dashboard_mod, "_build_quality_payload", counted_builder)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(*(client.get("/dashboard/api/quality") for _ in range(5)))

    assert [response.status_code for response in responses] == [200] * 5
    assert call_count == 1
    payloads = [response.json() for response in responses]
    assert {payload["marker"] for payload in payloads} == {"singleflight"}
    assert all(payload["cache"]["stale"] is False for payload in payloads)


@pytest.mark.asyncio
async def test_quality_endpoint_serves_stale_while_revalidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    _reset_quality_state(monkeypatch, clock=clock.monotonic)
    monkeypatch.setattr(dashboard_mod, "_live_hold_report", _fixed_hold_report)

    monkeypatch.setattr(dashboard_mod, "_build_quality_payload", lambda _report: {"marker": "old"})

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        seeded_response = await client.get("/dashboard/api/quality")

    assert seeded_response.status_code == 200
    assert seeded_response.json()["marker"] == "old"
    clock.advance(dashboard_mod._QUALITY_CACHE_TTL_S + 5.0)

    builder_started = threading.Event()

    def slow_builder(report: dict[str, Any]) -> dict[str, Any]:
        assert report == FIXED_REPORT
        builder_started.set()
        time.sleep(0.4)
        return {"marker": "new"}

    monkeypatch.setattr(dashboard_mod, "_build_quality_payload", slow_builder)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first_task = asyncio.create_task(client.get("/dashboard/api/quality"))
        assert await asyncio.to_thread(builder_started.wait, 1.0)

        stale_started = time.perf_counter()
        stale_response = await client.get("/dashboard/api/quality")
        stale_elapsed = time.perf_counter() - stale_started

        first_response = await first_task
        fresh_response = await client.get("/dashboard/api/quality")

    assert stale_response.status_code == 200
    stale_payload = stale_response.json()
    assert stale_elapsed < 0.2
    assert stale_payload["marker"] == "old"
    assert stale_payload["cache"]["stale"] is True
    assert stale_payload["cache"]["age_s"] > dashboard_mod._QUALITY_CACHE_TTL_S

    assert first_response.status_code == 200
    assert first_response.json()["marker"] == "new"
    assert first_response.json()["cache"]["stale"] is False

    assert fresh_response.status_code == 200
    assert fresh_response.json()["marker"] == "new"
    assert fresh_response.json()["cache"]["stale"] is False


def test_quality_endpoint_build_error_does_not_poison_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_quality_state(monkeypatch)
    monkeypatch.setattr(dashboard_mod, "_live_hold_report", _fixed_hold_report)

    def broken_builder(report: dict[str, Any]) -> dict[str, Any]:
        assert report == FIXED_REPORT
        raise RuntimeError("quality build failed")

    monkeypatch.setattr(dashboard_mod, "_build_quality_payload", broken_builder)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/dashboard/api/quality")

    assert response.status_code == 500

    monkeypatch.setattr(dashboard_mod, "_build_quality_payload", lambda _report: {"marker": "ok"})
    with TestClient(app, raise_server_exceptions=False) as client:
        retry_response = client.get("/dashboard/api/quality")

    assert retry_response.status_code == 200
    assert retry_response.json()["marker"] == "ok"
