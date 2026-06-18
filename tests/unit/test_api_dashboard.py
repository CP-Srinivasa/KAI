"""Tests for the operator dashboard (app.api.routers.dashboard).

Covers:
- GET /dashboard/api/quality returns structured JSON built live from audit files
- Auth middleware exempts all /dashboard/* paths
- JSONL loading helper edge cases
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import dashboard as dashboard_mod
from app.api.routers.dashboard import _load_jsonl, router
from app.core.settings import (
    AlertSettings,
    AppSettings,
    OperatorSettings,
    ProviderSettings,
    TradingViewSettings,
)
from app.security.auth import setup_auth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(*, api_key: str = "") -> FastAPI:
    """Minimal app with dashboard router and optional auth."""
    app = FastAPI()
    app.include_router(router)
    if api_key:
        setup_auth(app, api_key=api_key, env="production")
    return app


@contextmanager
def _patch_artifacts(
    d: Path,
) -> Generator[None, None, None]:
    """Patch all artifact path constants to point at tmp dir."""
    with (
        patch.object(dashboard_mod, "_ARTIFACTS", d),
        patch.object(
            dashboard_mod,
            "_ALERT_AUDIT",
            d / "alert_audit.jsonl",
        ),
        patch.object(
            dashboard_mod,
            "_ALERT_OUTCOMES",
            d / "alert_outcomes.jsonl",
        ),
        patch.object(
            dashboard_mod,
            "_TRADING_LOOP_AUDIT",
            d / "trading_loop_audit.jsonl",
        ),
        patch.object(
            dashboard_mod,
            "_PAPER_EXECUTION_AUDIT",
            d / "paper_execution_audit.jsonl",
        ),
        patch.object(
            dashboard_mod,
            "_AUDIT_V1_DISQUALIFIED_FLAG",
            d / "paper_execution_audit_v1_disqualified.flag",
        ),
        patch.object(
            dashboard_mod,
            "_BRIDGE_PENDING_ORDERS",
            d / "bridge_pending_orders.jsonl",
        ),
        patch.object(
            dashboard_mod,
            "_ENTRY_WATCHER_AUDIT",
            d / "entry_watcher_audit.jsonl",
        ),
        patch.object(
            dashboard_mod,
            "_SOURCE_RELIABILITY_REPORT",
            d / "source_reliability.json",
        ),
        patch.dict(dashboard_mod._hold_cache, {"report": None, "at": 0.0}),
    ):
        yield


@pytest.fixture()
def artifacts_dir(tmp_path: Path) -> Path:
    """Temp artifacts directory with sample data.

    The dashboard builds the hold-metrics report live from these audit JSONLs
    via build_hold_metrics_report — there is no pre-computed snapshot file.
    """
    (tmp_path / "alert_audit.jsonl").write_text(
        json.dumps(
            {
                "document_id": "abc12345-dead-beef",
                "sentiment_label": "bullish",
                "priority": 9,
                "affected_assets": ["BTC/USDT"],
                "dispatched_at": "2026-04-14T10:00:00",
                "is_digest": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    (tmp_path / "alert_outcomes.jsonl").write_text(
        json.dumps(
            {
                "document_id": "abc12345-dead-beef",
                "outcome": "hit",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    (tmp_path / "trading_loop_audit.jsonl").write_text(
        json.dumps({"status": "no_signal"})
        + "\n"
        + json.dumps({"status": "no_signal"})
        + "\n"
        + json.dumps({"status": "completed"})
        + "\n",
        encoding="utf-8",
    )

    (tmp_path / "paper_execution_audit.jsonl").write_text(
        json.dumps({"event_type": "order_filled", "side": "buy"})
        + "\n"
        + json.dumps({"event_type": "cycle_start"})
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# GET /dashboard/api/quality -- JSON API
#
# Note: The /dashboard HTML shell is served by the React SPA mount in
# app/api/main.py (web/dist/). This router only exposes the JSON API.
# ---------------------------------------------------------------------------


def test_quality_api_returns_metrics(
    artifacts_dir: Path,
) -> None:
    app = _make_app()
    with _patch_artifacts(artifacts_dir):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/quality")

    assert r.status_code == 200
    data = r.json()
    # Live build aggregates from audit JSONLs (not a snapshot file).
    # Fixture is sparse (1 alert older than recency window) → no resolved precision.
    assert data["precision_pct"] is None
    assert data["paper_fills"] == 1
    assert data["paper_cycles"] == 3
    assert data["signal_execution"]["total_correlations"] == 0
    assert data["loop_status_counts"]["no_signal"] == 2
    assert data["loop_status_counts"]["completed"] == 1
    # Sparse fixture → gate stays active with documented blocking reasons.
    assert data["gate_status"] == "hold_remains_active"
    assert "resolved_directional_below_200" in data["blocking_reasons"]
    assert data["dashboard_truth_contract_version"] == 2
    assert data["reentry"]["status"] == "expired"
    assert data["reentry"]["target_date"] == "2026-05-16"
    assert data["metric_contract"]["paper_fills_with_pnl"]["scope"] in {
        "lifetime",
        "cutoff_since",
    }
    assert data["metric_contract"]["paper_fills_with_pnl"]["quality_status"] == "historical_only"
    assert data["metric_contract"]["paper_fills_recent_24h"]["scope"] == "rolling_24h"
    # Truth-Layer v2 (Issue #170 Part A): registry serves the scalar metrics from
    # ONE source; the frontend is never permitted to recompute them, and the
    # contract value reconciles against the SSOT within tolerance.
    registry = data["metric_registry"]
    assert registry["paper_fills_with_pnl"]["status"] == "ok"
    assert registry["paper_fills_recent_24h"]["value"] == float(
        data["metric_contract"]["paper_fills_recent_24h"]["value"]
    )
    # an unsourced risk scalar is served degraded (value withheld), never faked
    assert registry["var_usd"]["status"] == "degraded"
    assert registry["var_usd"]["value"] is None
    # every reconciliation entry is within tolerance (contract == SSOT)
    assert data["metric_registry_reconciliation"]
    assert all(r["within_tolerance"] for r in data["metric_registry_reconciliation"])


def test_quality_api_includes_alerts_with_outcomes(
    artifacts_dir: Path,
) -> None:
    app = _make_app()
    with _patch_artifacts(artifacts_dir):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/quality")

    alerts = r.json()["recent_alerts"]
    assert len(alerts) == 1
    assert alerts[0]["doc_id"] == "abc12345-dea"
    assert alerts[0]["sentiment"] == "bullish"
    assert alerts[0]["outcome"] == "hit"


def test_quality_api_includes_source_reliability_summary(
    artifacts_dir: Path,
) -> None:
    (artifacts_dir / "source_reliability.json").write_text(
        json.dumps(
            {
                "report_type": "source_reliability",
                "generated_at": "2026-05-24T09:00:00+00:00",
                "window_days": 90,
                "thresholds": {"min_n_for_demote": 20},
                "scores": {
                    "decrypt": {
                        "source_name": "decrypt",
                        "hits": 18,
                        "miss": 7,
                        "n": 25,
                        "point_estimate": 0.72,
                        "wilson_lower_95": 0.52,
                        "tier": "neutral",
                        "priority_modifier": 0,
                    },
                    "unknown": {
                        "source_name": "unknown",
                        "hits": 4,
                        "miss": 16,
                        "n": 20,
                        "point_estimate": 0.2,
                        "wilson_lower_95": 0.08,
                        "tier": "low",
                        "priority_modifier": -2,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    app = _make_app()
    with _patch_artifacts(artifacts_dir):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/quality")

    rel = r.json()["source_reliability"]
    assert rel["status"] == "ok"
    assert rel["source_count"] == 2
    assert rel["tier_counts"] == {"neutral": 1, "low": 1}
    assert rel["top_sources"][0]["source_name"] == "decrypt"
    assert rel["top_sources"][0]["point_estimate_pct"] == 72.0
    assert rel["unknown_bucket"]["tier"] == "low"
    assert rel["trusted_count"] == 0
    assert rel["quality_status"] in {"critical", "stale"}
    assert rel["health_warning"]


def test_quality_api_includes_position_partial_closed_in_pnl(tmp_path: Path) -> None:
    """Forensik 2026-05-25: position_partial_closed muss in paper_realized_pnl_usd
    enthalten sein. Vorher hat dashboard.py:233 nur position_closed eingerechnet,
    was zu Untererfassung führte (Codex-Beleg: Pi $759 vs Audit-Replay $2486)."""
    (tmp_path / "alert_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "alert_outcomes.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "trading_loop_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "paper_execution_audit.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"event_type": "order_filled", "side": "buy", "symbol": "BTC/USDT"},
                {
                    "schema_version": "v2",
                    "event_type": "position_partial_closed",
                    "symbol": "BTC/USDT",
                    "trade_pnl_usd": 500.0,
                },
                {
                    "schema_version": "v2",
                    "event_type": "position_partial_closed",
                    "symbol": "BTC/USDT",
                    "trade_pnl_usd": 300.0,
                },
                {
                    "schema_version": "v2",
                    "event_type": "position_closed",
                    "symbol": "BTC/USDT",
                    "trade_pnl_usd": 200.0,
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    app = _make_app()
    with _patch_artifacts(tmp_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/quality")

    assert r.status_code == 200
    data = r.json()
    # All three close events must be counted: 500 + 300 + 200 = 1000
    assert data["paper_realized_pnl_usd"] == 1000.0
    assert data["paper_positions_closed"] == 1
    assert data["paper_positions_partial_closed"] == 2
    # Backwards-compat: paper_fills_with_pnl = closes + partials
    assert data["paper_fills_with_pnl"] == 3
    assert data["paper_evidence"]["closed_total"] == 3
    assert data["paper_evidence"]["scope"] == "lifetime"
    assert data["paper_evidence"]["warning"]


def test_quality_api_separates_historical_paper_fills_from_rolling_24h(
    tmp_path: Path,
) -> None:
    old_ts = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    recent_ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    (tmp_path / "alert_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "alert_outcomes.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "trading_loop_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "paper_execution_audit.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"event_type": "order_filled", "timestamp_utc": old_ts},
                {
                    "schema_version": "v2",
                    "event_type": "position_closed",
                    "timestamp_utc": old_ts,
                    "trade_pnl_usd": 25.0,
                },
                {"event_type": "order_filled", "timestamp_utc": recent_ts},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    old_mtime = (datetime.now(UTC) - timedelta(hours=26)).timestamp()
    os.utime(tmp_path / "paper_execution_audit.jsonl", (old_mtime, old_mtime))

    app = _make_app()
    with _patch_artifacts(tmp_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/quality")

    assert r.status_code == 200
    data = r.json()
    assert data["paper_fills"] == 2
    assert data["paper_fills_with_pnl"] == 1
    assert data["paper_evidence"]["fills_recent_24h"] == 1
    assert data["paper_evidence"]["closed_recent_24h"] == 0
    assert data["paper_evidence"]["stale_status"] == "stale"
    assert data["metric_contract"]["paper_fills_with_pnl"]["scope"] == "lifetime"
    assert data["metric_contract"]["paper_fills_recent_24h"]["scope"] == "rolling_24h"


def test_priority_gate_endpoint_exposes_reject_semantics(tmp_path: Path) -> None:
    recent = datetime.now(UTC).isoformat()
    (tmp_path / "alert_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "alert_outcomes.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "paper_execution_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "trading_loop_audit.jsonl").write_text(
        "\n".join(
            json.dumps({"started_at": recent, "status": status})
            for status in ["priority_rejected", "priority_rejected", "completed"]
        )
        + "\n",
        encoding="utf-8",
    )

    app = _make_app()
    with _patch_artifacts(tmp_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/priority-gate")

    assert r.status_code == 200
    data = r.json()
    assert data["window_hours"] == 24
    assert data["rejected_total"] == 2
    assert data["rejected_pct"] == pytest.approx(66.67)
    assert data["filled_total"] == 1
    assert data["top_reject_reason"] == "below_priority_threshold"
    assert data["priority_quality"]["current_quality_verdict"] in {
        "insufficient_data",
        "priority_unproven",
        "priority_underperforming",
        "priority_validated",
    }


def test_priority_gate_marks_negative_priority_lift_as_underperforming(tmp_path: Path) -> None:
    recent = datetime.now(UTC).isoformat()
    (tmp_path / "alert_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "alert_outcomes.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "paper_execution_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "trading_loop_audit.jsonl").write_text(
        json.dumps({"started_at": recent, "status": "priority_rejected"}) + "\n",
        encoding="utf-8",
    )

    async def fake_hold_report() -> dict[str, object]:
        return {
            "signal_quality_validation": {
                "priority_tier_lift_pct": -12.5,
                "priority_tier_high_conviction_resolved": 8,
                "priority_tier_standard_resolved": 8,
            }
        }

    app = _make_app()
    with (
        _patch_artifacts(tmp_path),
        patch.object(
            dashboard_mod,
            "_live_hold_report",
            fake_hold_report,
        ),
    ):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/priority-gate")

    assert r.status_code == 200
    priority_quality = r.json()["priority_quality"]
    assert priority_quality["high_priority_lift_pct"] == -12.5
    assert priority_quality["current_quality_verdict"] == "priority_underperforming"
    assert priority_quality["warning"]


def test_regime_endpoint_marks_read_only_and_exposes_snapshot_age(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regime_dir = tmp_path / "artifacts" / "regime_state"
    regime_dir.mkdir(parents=True)
    snapshot = {
        "asset": "BTC",
        "timestamp": datetime.now(UTC)
        .replace(minute=0, second=0, microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "regime": "chop_quiet",
        "vol_class": "vol_low",
        "confidence": 1.0,
        "adx": 20.0,
        "plus_di": 12.0,
        "minus_di": 10.0,
        "rv_24h": 0.01,
        "atr_zscore": 0.1,
    }
    (regime_dir / "btc_regime.jsonl").write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    app = _make_app()
    with TestClient(app) as client:
        r = client.get("/dashboard/api/regime")

    assert r.status_code == 200
    data = r.json()
    assert data["is_read_only"] is True
    assert data["is_decision_relevant"] is False
    assert data["semantic_status"] == "read_only"
    assert data["warning"]
    assert data["by_asset"]["BTC"]["regime"] == "chop_quiet"
    assert data["by_asset_metadata"]["BTC"]["quality_status"] == "read_only"
    assert data["by_asset_metadata"]["BTC"]["snapshot_age_hours"] is not None


# ---------------------------------------------------------------------------
# Truth-layer finalization sprint (2026-06-04): config re-entry, small-n
# deemphasis, loop heartbeat, regime data-vs-response freshness, contract
# consistency. These assert SEMANTICS, not mere field presence.
# ---------------------------------------------------------------------------


def test_reentry_status_config_and_failsafe_semantics() -> None:
    # Future date → active, real positive delta.
    future = dashboard_mod._reentry_status(target_date="2099-12-31")
    assert future["status"] == "active"
    assert future["days_delta"] > 0
    assert future["target_source"] == "explicit"
    # Past date → expired, NOT clamped to 0/today.
    past = dashboard_mod._reentry_status(target_date="2020-01-01")
    assert past["status"] == "expired"
    assert past["days_delta"] < 0
    # Empty/invalid → fail-safe requires_re_evaluation, no crash, no invented target.
    empty = dashboard_mod._reentry_status(target_date="")
    assert empty["status"] == "requires_re_evaluation"
    assert empty["days_delta"] is None
    # Default (from settings) → historical 2026-05-16 default is in the past → expired.
    default = dashboard_mod._reentry_status()
    assert default["target_date"] == "2026-05-16"
    assert default["status"] == "expired"
    assert default["target_source"] in {"config", "default_historical"}


def test_source_reliability_flags_small_n_as_provisional(artifacts_dir: Path) -> None:
    (artifacts_dir / "source_reliability.json").write_text(
        json.dumps(
            {
                "report_type": "source_reliability",
                "generated_at": datetime.now(UTC).isoformat(),
                "window_days": 90,
                "thresholds": {"min_n": 50},
                "scores": {
                    "btc_echo": {
                        "source_name": "btc_echo",
                        "hits": 1,
                        "miss": 0,
                        "n": 1,
                        "point_estimate": 1.0,
                        "wilson_lower_95": 0.05,
                        "tier": "watch",
                        "priority_modifier": 0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    app = _make_app()
    with _patch_artifacts(artifacts_dir):
        with TestClient(app) as client:
            rel = client.get("/dashboard/api/quality").json()["source_reliability"]
    # 100% at n=1 must NOT read as trusted, and must be flagged provisional.
    assert rel["trusted_count"] == 0
    assert rel["min_n"] == 50
    assert rel["provisional_count"] == 1
    src = rel["top_sources"][0]
    assert src["source_name"] == "btc_echo"
    assert src["is_provisional"] is True
    assert src["sample_warning"]


def test_priority_gate_heartbeat_unknown_when_no_cycles(tmp_path: Path) -> None:
    (tmp_path / "alert_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "alert_outcomes.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "paper_execution_audit.jsonl").write_text("", encoding="utf-8")
    # Empty loop audit → zero cycles → loop liveness NOT verified.
    (tmp_path / "trading_loop_audit.jsonl").write_text("", encoding="utf-8")

    app = _make_app()
    with _patch_artifacts(tmp_path):
        with TestClient(app) as client:
            data = client.get("/dashboard/api/priority-gate").json()
    assert data["heartbeat_status"] == "unknown"
    assert data["heartbeat_warning"]
    # 0 filled must not be presented as healthy.
    assert data["filled_total"] == 0


def test_priority_gate_heartbeat_active_blocking_when_cycles_present(tmp_path: Path) -> None:
    recent = datetime.now(UTC).isoformat()
    (tmp_path / "alert_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "alert_outcomes.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "paper_execution_audit.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "trading_loop_audit.jsonl").write_text(
        "\n".join(
            json.dumps({"started_at": recent, "status": s})
            for s in ["priority_rejected", "priority_rejected"]
        )
        + "\n",
        encoding="utf-8",
    )
    app = _make_app()
    with _patch_artifacts(tmp_path):
        with TestClient(app) as client:
            data = client.get("/dashboard/api/priority-gate").json()
    # Cycles present + fresh audit → loop liveness IS verified (not unknown/stale).
    assert data["heartbeat_status"] in {"active", "active_blocking"}
    assert data["heartbeat_warning"] is None
    assert data["loop_audit_present"] is True


def test_regime_separates_response_from_data_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regime_dir = tmp_path / "artifacts" / "regime_state"
    regime_dir.mkdir(parents=True)
    snapshot = {
        "asset": "BTC",
        "timestamp": datetime.now(UTC)
        .replace(minute=0, second=0, microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "regime": "chop_quiet",
        "vol_class": "vol_low",
        "confidence": 1.0,
        "adx": 20.0,
        "plus_di": 12.0,
        "minus_di": 10.0,
        "rv_24h": 0.01,
        "atr_zscore": 0.1,
    }
    (regime_dir / "btc_regime.jsonl").write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    app = _make_app()
    with TestClient(app) as client:
        data = client.get("/dashboard/api/regime").json()
    # Response freshness must be distinct from data freshness.
    assert "response_generated_at" in data
    assert data["data_freshness_status"] in {"ok", "warning", "stale", "unverified", "no_data"}
    assert data["data_freshness_status"] == "ok"  # snapshot is current
    assert data["is_decision_relevant"] is False


def test_metric_contract_does_not_contradict_parallel_truth_fields(artifacts_dir: Path) -> None:
    (artifacts_dir / "source_reliability.json").write_text(
        json.dumps(
            {
                "report_type": "source_reliability",
                "generated_at": datetime.now(UTC).isoformat(),
                "window_days": 90,
                "thresholds": {"min_n": 50},
                "scores": {
                    "unknown": {
                        "source_name": "unknown",
                        "hits": 4,
                        "miss": 16,
                        "n": 20,
                        "point_estimate": 0.2,
                        "wilson_lower_95": 0.08,
                        "tier": "low",
                        "priority_modifier": -2,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    app = _make_app()
    with _patch_artifacts(artifacts_dir):
        with TestClient(app) as client:
            data = client.get("/dashboard/api/quality").json()
    contract = data["metric_contract"]
    # Contract must mirror, not contradict, the parallel truth fields.
    assert contract["paper_fills_with_pnl"]["quality_status"] == "historical_only"
    assert contract["paper_fills_recent_24h"]["scope"] == "rolling_24h"
    assert contract["market_regime"]["is_read_only"] is True
    assert contract["market_regime"]["is_decision_relevant"] is False
    assert (
        contract["source_reliability"]["quality_status"]
        == data["source_reliability"]["quality_status"]
    )


# ---------------------------------------------------------------------------
# Auth exemption: /dashboard/* paths pass without bearer
# ---------------------------------------------------------------------------


def test_dashboard_api_exempt_from_auth(
    artifacts_dir: Path,
) -> None:
    app = _make_app(api_key="secret-key")
    with _patch_artifacts(artifacts_dir):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/quality")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# _load_jsonl helper
# ---------------------------------------------------------------------------


def test_load_jsonl_empty_for_missing_file() -> None:
    assert _load_jsonl(Path("/does/not/exist.jsonl")) == []


def test_load_jsonl_skips_invalid_lines(tmp_path: Path) -> None:
    f = tmp_path / "test.jsonl"
    f.write_text(
        '{"a":1}\nnot-json\n{"b":2}\n',
        encoding="utf-8",
    )
    rows = _load_jsonl(f)
    assert len(rows) == 2
    assert rows[0] == {"a": 1}
    assert rows[1] == {"b": 2}


def test_load_jsonl_tail(tmp_path: Path) -> None:
    f = tmp_path / "test.jsonl"
    lines = [json.dumps({"i": i}) for i in range(10)]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = _load_jsonl(f, tail=3)
    assert len(rows) == 3
    assert rows[0]["i"] == 7


# ---------------------------------------------------------------------------
# Route inventory in main app
# ---------------------------------------------------------------------------


def test_dashboard_routes_in_main_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify React-SPA mount registers when web/dist exists.

    The mount in app.api.main is conditional on `web/dist` being present
    (created by `npm run build`). CI runners do not build the SPA, so we
    stage a dummy web/dist to exercise the conditional mount.
    """
    (tmp_path / "web" / "dist").mkdir(parents=True)
    (tmp_path / "web" / "dist" / "index.html").write_text("<html></html>")
    monkeypatch.chdir(tmp_path)

    from app.api.main import create_app

    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/dashboard" in paths
    assert "/dashboard/api/quality" in paths


def test_lightning_endpoint_disabled_by_default() -> None:
    """Default-off: /dashboard/api/lightning meldet `disabled` ohne Netzwerk-Call."""
    client = TestClient(_make_app())
    resp = client.get("/dashboard/api/lightning")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "disabled"
    assert body["reachable"] is False
    assert "generated_at" in body
    assert "num_active_channels" in body
    # L1: chain-Wahrheit ist mit drin und ebenfalls default-off.
    assert body["chain"]["state"] == "disabled"


def test_lightning_endpoint_chain_truth_overrides_height(monkeypatch) -> None:
    """L1: Block-Höhe/Sync kommen aus der eigenen bitcoind, auch wenn lnd-getinfo leer ist."""
    from app.chain.adapter import ChainStatus
    from app.lightning.adapter import LightningNodeStatus

    async def _fake_cached_node():  # lnd erreichbar (aus Cache), getinfo-Details fehlen
        return (
            LightningNodeStatus(
                state="ok",
                reachable=True,
                server_state="SERVER_ACTIVE",
                info_available=False,
                block_height=0,
                synced_to_chain=False,
            ),
            5.0,
        )

    async def _fake_cached():  # Hintergrund-Cache liefert die Chain-Wahrheit
        return (
            ChainStatus(
                state="ok",
                reachable=True,
                chain="main",
                blocks=953902,
                headers=953902,
                synced=True,
                fee_sat_vb=2.0,
                mempool_tx=5,
            ),
            12.0,
        )

    monkeypatch.setattr("app.lightning.cache.get_cached_node_status", _fake_cached_node)
    monkeypatch.setattr("app.chain.cache.get_cached_chain_status", _fake_cached)

    body = TestClient(_make_app()).get("/dashboard/api/lightning").json()
    assert body["state"] == "ok" and body["reachable"] is True
    assert body["block_height"] == 953902  # aus bitcoind, nicht aus lnd-getinfo
    assert body["synced_to_chain"] is True
    assert body["node_age_seconds"] == 5.0
    assert body["chain"]["state"] == "ok" and body["chain"]["blocks"] == 953902
    assert body["chain_age_seconds"] == 12.0


def test_chain_endpoint_disabled_by_default() -> None:
    """Default-off: /dashboard/api/chain meldet `disabled` ohne Netzwerk-Call."""
    resp = TestClient(_make_app()).get("/dashboard/api/chain")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "disabled"
    assert body["reachable"] is False
    assert "generated_at" in body
    assert "blocks" in body and "mempool_tx" in body


def test_integrity_endpoint_disabled_by_default() -> None:
    """Default-off: /dashboard/api/integrity meldet `disabled` ohne FS-Touch."""
    resp = TestClient(_make_app()).get("/dashboard/api/integrity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "disabled"
    assert body["enabled"] is False
    assert "generated_at" in body
    assert "last_digest" in body and "proof_available" in body


def test_integrity_endpoint_ok(monkeypatch) -> None:
    """Vorhandener Anchor-Record → ok mit Digest + Proof-Status."""
    from app.integrity.status import IntegrityStatus

    def _fake_status(cfg=None):
        return IntegrityStatus(
            state="ok",
            enabled=True,
            stamper="opentimestamps",
            anchor_count=3,
            last_digest="abc123",
            last_anchored_at="2026-06-17T00:00:00+00:00",
            proof_available=True,
        )

    monkeypatch.setattr("app.integrity.get_integrity_status", _fake_status)
    body = TestClient(_make_app()).get("/dashboard/api/integrity").json()
    assert body["state"] == "ok" and body["enabled"] is True
    assert body["last_digest"] == "abc123" and body["proof_available"] is True
    assert body["anchor_count"] == 3


def test_chain_endpoint_ok_when_reachable(monkeypatch) -> None:
    """Erreichbare bitcoind → ok mit Tip-Höhe/Sync/Fee/Mempool aus der Node."""
    from app.chain.adapter import ChainStatus

    async def _fake_cached():
        return (
            ChainStatus(
                state="ok",
                reachable=True,
                chain="main",
                blocks=953902,
                headers=953902,
                synced=True,
                fee_sat_vb=2.5,
                mempool_tx=7,
            ),
            8.0,
        )

    monkeypatch.setattr("app.chain.cache.get_cached_chain_status", _fake_cached)
    body = TestClient(_make_app()).get("/dashboard/api/chain").json()
    assert body["state"] == "ok" and body["reachable"] is True
    assert body["blocks"] == 953902 and body["synced"] is True
    assert body["fee_sat_vb"] == 2.5 and body["mempool_tx"] == 7
    assert body["age_seconds"] == 8.0


def test_markets_derivatives_empty_is_honest(monkeypatch) -> None:
    """Ohne Snapshot-Cache: ehrlich leer (available False), kein erfundener Wert."""

    class _Empty:
        def __init__(self, _path) -> None:  # noqa: ANN001
            pass

        def read_all(self):  # noqa: ANN201
            return {}

    monkeypatch.setattr("app.signals.funding_snapshot_store.FundingSnapshotStore", _Empty)
    monkeypatch.setattr("app.signals.oi_snapshot_store.OpenInterestSnapshotStore", _Empty)
    body = TestClient(_make_app()).get("/dashboard/api/markets/derivatives").json()
    assert body["available"] is False and body["rows"] == []


def test_markets_derivatives_serves_own_ingestion(monkeypatch) -> None:
    """Funding + OI aus KAIs eigenen Snapshot-Stores werden je Symbol gemerged."""
    from app.market_data.models import FundingRateSnapshot, OpenInterestSnapshot

    class _Funding:
        def __init__(self, _path) -> None:  # noqa: ANN001
            pass

        def read_all(self):  # noqa: ANN201
            return {
                "BTC/USDT": FundingRateSnapshot(
                    symbol="BTC/USDT",
                    timestamp_utc="2026-06-17T15:41:10Z",
                    rate=7.06e-06,
                    mark_price=65436.6,
                    source="bybit",
                )
            }

    class _OI:
        def __init__(self, _path) -> None:  # noqa: ANN001
            pass

        def read_all(self):  # noqa: ANN201
            return {
                "BTC/USDT": OpenInterestSnapshot(
                    symbol="BTC/USDT",
                    timestamp_utc="2026-06-17T15:00:00Z",
                    open_interest=51176.86,
                    oi_change_zscore=-1.21,
                    source="bybit",
                )
            }

    monkeypatch.setattr("app.signals.funding_snapshot_store.FundingSnapshotStore", _Funding)
    monkeypatch.setattr("app.signals.oi_snapshot_store.OpenInterestSnapshotStore", _OI)
    body = TestClient(_make_app()).get("/dashboard/api/markets/derivatives").json()
    assert body["available"] is True and len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["symbol"] == "BTC/USDT"
    assert row["funding_rate"] == 7.06e-06 and row["mark_price"] == 65436.6
    assert row["open_interest"] == 51176.86 and row["oi_change_zscore"] == -1.21
    assert row["funding_source"] == "bybit" and row["oi_source"] == "bybit"


def test_markets_sentiment_endpoint(monkeypatch) -> None:
    """Fear & Greed über den server-gecachten Adapter; Wert + Alter im Payload."""
    from app.market_data.sentiment import SentimentSnapshot

    async def _fake_cached():  # noqa: ANN202
        return SentimentSnapshot(available=True, value=61, classification="Greed"), 42.0

    monkeypatch.setattr("app.market_data.sentiment.get_cached_sentiment", _fake_cached)
    body = TestClient(_make_app()).get("/dashboard/api/markets/sentiment").json()
    assert body["available"] is True and body["value"] == 61
    assert body["classification"] == "Greed" and body["age_seconds"] == 42.0
    assert body["source"] == "alternative.me"


def test_markets_sentiment_cold_is_honest(monkeypatch) -> None:
    """Kalter Cache: ehrlich available False, kein erfundener Wert."""
    from app.market_data.sentiment import SentimentSnapshot

    async def _fake_cold():  # noqa: ANN202
        return SentimentSnapshot.unavailable("warming up"), None

    monkeypatch.setattr("app.market_data.sentiment.get_cached_sentiment", _fake_cold)
    body = TestClient(_make_app()).get("/dashboard/api/markets/sentiment").json()
    assert body["available"] is False and body["age_seconds"] is None


def test_markets_liquidations_endpoint(monkeypatch) -> None:
    """OKX-Liquidationen über den server-gecachten Adapter; Long/Short je Symbol."""
    from app.market_data.liquidations import LiquidationRow, LiquidationsSnapshot

    async def _fake_cached():  # noqa: ANN202
        return (
            LiquidationsSnapshot(
                available=True,
                rows=(
                    LiquidationRow(
                        symbol="BTC/USDT",
                        long_sz=7.5,
                        short_sz=3.0,
                        long_usd=4500.0,
                        short_usd=1800.0,
                        events=3,
                        last_ts_utc="x",
                    ),
                ),
            ),
            12.0,
        )

    monkeypatch.setattr("app.market_data.liquidations.get_cached_liquidations", _fake_cached)
    body = TestClient(_make_app()).get("/dashboard/api/markets/liquidations").json()
    assert body["available"] is True and body["source"] == "okx"
    assert body["rows"][0]["symbol"] == "BTC/USDT"
    assert body["rows"][0]["long_sz"] == 7.5 and body["rows"][0]["short_sz"] == 3.0
    assert body["rows"][0]["long_usd"] == 4500.0 and body["rows"][0]["short_usd"] == 1800.0
    assert body["age_seconds"] == 12.0


def test_markets_liquidations_cold_is_honest(monkeypatch) -> None:
    from app.market_data.liquidations import LiquidationsSnapshot

    async def _fake_cold():  # noqa: ANN202
        return LiquidationsSnapshot.unavailable("warming up"), None

    monkeypatch.setattr("app.market_data.liquidations.get_cached_liquidations", _fake_cold)
    body = TestClient(_make_app()).get("/dashboard/api/markets/liquidations").json()
    assert body["available"] is False and body["rows"] == [] and body["age_seconds"] is None


def test_operator_board_api_returns_curated_lists() -> None:
    """GET /dashboard/api/operator-board liefert die kuratierten Listen (fail-soft)."""
    client = TestClient(_make_app())
    r = client.get("/dashboard/api/operator-board")
    assert r.status_code == 200
    data = r.json()
    for key in ("stand", "todos", "phases", "improvements", "generated_at"):
        assert key in data
    assert isinstance(data["todos"], list)
    assert isinstance(data["phases"], list)
    assert isinstance(data["improvements"], list)


def test_markets_momentum_endpoint(monkeypatch) -> None:
    """Binance-Momentum über den server-gecachten Adapter; 24h-Änderung je Symbol."""
    from app.market_data.momentum import MomentumRow, MomentumSnapshot

    async def _fake_cached():  # noqa: ANN202
        return (
            MomentumSnapshot(
                available=True,
                rows=(MomentumRow(symbol="BTC/USDT", last_price=65000.0, change_pct_24h=-0.72),),
            ),
            9.0,
        )

    monkeypatch.setattr("app.market_data.momentum.get_cached_momentum", _fake_cached)
    body = TestClient(_make_app()).get("/dashboard/api/markets/momentum").json()
    assert body["available"] is True and body["source"] == "binance"
    assert body["rows"][0]["symbol"] == "BTC/USDT"
    assert body["rows"][0]["change_pct_24h"] == -0.72 and body["age_seconds"] == 9.0


def test_markets_momentum_cold_is_honest(monkeypatch) -> None:
    from app.market_data.momentum import MomentumSnapshot

    async def _fake_cold():  # noqa: ANN202
        return MomentumSnapshot.unavailable("warming up"), None

    monkeypatch.setattr("app.market_data.momentum.get_cached_momentum", _fake_cold)
    body = TestClient(_make_app()).get("/dashboard/api/markets/momentum").json()
    assert body["available"] is False and body["rows"] == [] and body["age_seconds"] is None


# ---------------------------------------------------------------------------
# GET /dashboard/api/integrations -- echter Config-Status (No-Fake-Doktrin)
#
# Regression-Guard: das Settings-Tab-Badge war früher hartkodiert
# ("vorbereitet"), egal ob der TradingView-Webhook live war. Der Status muss
# aus den fail-closed Settings-Flags kommen.
# ---------------------------------------------------------------------------


def _integrations_settings(
    *,
    tv_enabled: bool = False,
    tv_secret: str = "",
    telegram_token: str = "",
    operator_token: str = "",
    openai_key: str = "",
    gemini_key: str = "",
    auto_promote: bool = False,
) -> AppSettings:
    """AppSettings mit explizit gepinnten Sub-Settings.

    Init-kwargs schlagen ein ambient ``.env`` (pydantic-Priorität: init > env),
    damit der Test deterministisch ist.
    """
    settings = AppSettings()
    settings.tradingview = TradingViewSettings(
        webhook_enabled=tv_enabled,
        webhook_secret=tv_secret,
        webhook_auth_mode="hmac",
        webhook_shared_token="",
        webhook_auto_promote_enabled=auto_promote,
    )
    settings.alerts = AlertSettings(telegram_token=telegram_token)
    settings.operator = OperatorSettings(telegram_bot_token=operator_token)
    settings.providers = ProviderSettings(
        openai_api_key=openai_key,
        anthropic_api_key="",
        gemini_api_key=gemini_key,
    )
    return settings


@contextmanager
def _patch_settings(settings: AppSettings) -> Generator[None, None, None]:
    # Der Endpoint importiert get_settings lokal aus app.core.settings —
    # daher dort patchen (nicht im dashboard-Modul).
    with patch("app.core.settings.get_settings", lambda: settings):
        yield


def test_integrations_tradingview_active_when_enabled_and_secret() -> None:
    app = _make_app()
    settings = _integrations_settings(
        tv_enabled=True, tv_secret="s3cr3t", auto_promote=True
    )
    with _patch_settings(settings), TestClient(app) as client:
        r = client.get("/dashboard/api/integrations")

    assert r.status_code == 200
    tv = r.json()["integrations"]["tradingview"]
    assert tv["status"] == "active"
    assert tv["mounted"] is True
    assert tv["webhook_enabled"] is True
    assert tv["secret_configured"] is True
    assert tv["auto_promote_enabled"] is True
    assert tv["auth_mode"] == "hmac"


def test_integrations_tradingview_disabled_without_secret() -> None:
    """Fail-closed: enabled aber KEIN Secret -> Router unmounted -> disabled."""
    app = _make_app()
    settings = _integrations_settings(tv_enabled=True, tv_secret="")
    with _patch_settings(settings), TestClient(app) as client:
        r = client.get("/dashboard/api/integrations")

    tv = r.json()["integrations"]["tradingview"]
    assert tv["status"] == "disabled"
    assert tv["mounted"] is False
    assert tv["webhook_enabled"] is True
    assert tv["secret_configured"] is False


def test_integrations_tradingview_disabled_when_flag_off() -> None:
    app = _make_app()
    settings = _integrations_settings(tv_enabled=False, tv_secret="s3cr3t")
    with _patch_settings(settings), TestClient(app) as client:
        r = client.get("/dashboard/api/integrations")

    assert r.json()["integrations"]["tradingview"]["status"] == "disabled"


def test_integrations_telegram_and_llm_derive_from_config() -> None:
    app = _make_app()
    settings = _integrations_settings(
        telegram_token="tg-token", openai_key="sk-x", gemini_key="g-x"
    )
    with _patch_settings(settings), TestClient(app) as client:
        r = client.get("/dashboard/api/integrations")

    integ = r.json()["integrations"]
    assert integ["telegram"]["status"] == "active"
    assert integ["llm"]["status"] == "active"
    assert set(integ["llm"]["providers"]) == {"openai", "gemini"}
    # SMTP ist nicht backend-konfigurierbar -> ehrlich disabled.
    assert integ["email"]["status"] == "disabled"


def test_integrations_disabled_when_nothing_configured() -> None:
    app = _make_app()
    settings = _integrations_settings()
    with _patch_settings(settings), TestClient(app) as client:
        r = client.get("/dashboard/api/integrations")

    integ = r.json()["integrations"]
    assert integ["telegram"]["status"] == "disabled"
    assert integ["llm"]["status"] == "disabled"
    assert integ["llm"]["providers"] == []
    assert integ["tradingview"]["status"] == "disabled"
