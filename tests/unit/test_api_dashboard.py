"""Tests for the operator dashboard (app.api.routers.dashboard).

Covers:
- GET /dashboard/api/quality returns structured JSON built live from audit files
- Auth middleware exempts all /dashboard/* paths
- JSONL loading helper edge cases
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import dashboard as dashboard_mod
from app.api.routers.dashboard import _load_jsonl, router
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
