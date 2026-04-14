"""Tests for the operator dashboard (app.api.routers.dashboard).

Covers:
- GET /dashboard returns HTML with quality-bar markup
- GET /dashboard/api/quality returns structured JSON from artifacts
- 404 when hold report is missing
- Auth middleware exempts all /dashboard/* paths
- JSONL loading helper edge cases
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import dashboard as dashboard_mod
from app.api.routers.dashboard import (
    _load_hold_report,
    _load_jsonl,
    router,
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


def _sample_hold_report() -> dict[str, Any]:
    return {
        "generated_at": "2026-04-14T10:00:00+00:00",
        "signal_quality_validation": {
            "resolved_precision_pct": 52.54,
            "resolved_false_positive_rate_pct": 47.46,
            "priority_hit_correlation": 0.29,
            "directional_actionable_rate_pct": 83.0,
            "high_priority_hit_rate_pct": 52.54,
            "low_priority_hit_rate_pct": None,
            "paper_real_price_cycle_count": 179,
        },
        "alert_hit_rate_evidence": {
            "resolved_directional_documents": 59,
            "directional_alert_documents": 261,
            "alert_hits": 31,
            "alert_misses": 28,
        },
        "paper_trading_evidence": {
            "loop_metrics": {"total_cycles": 255},
        },
        "hold_gate_evaluation": {
            "overall_status": "hold_releasable",
            "blocking_reasons": [],
        },
    }


@contextmanager
def _patch_artifacts(
    d: Path,
) -> Generator[None, None, None]:
    """Patch all artifact path constants to point at tmp dir."""
    report = d / "ph5_hold" / "ph5_hold_metrics_report.json"
    with (
        patch.object(dashboard_mod, "_ARTIFACTS", d),
        patch.object(dashboard_mod, "_HOLD_REPORT", report),
        patch.object(
            dashboard_mod, "_ALERT_AUDIT",
            d / "alert_audit.jsonl",
        ),
        patch.object(
            dashboard_mod, "_ALERT_OUTCOMES",
            d / "alert_outcomes.jsonl",
        ),
        patch.object(
            dashboard_mod, "_TRADING_LOOP_AUDIT",
            d / "trading_loop_audit.jsonl",
        ),
        patch.object(
            dashboard_mod, "_PAPER_EXECUTION_AUDIT",
            d / "paper_execution_audit.jsonl",
        ),
    ):
        yield


@pytest.fixture()
def artifacts_dir(tmp_path: Path) -> Path:
    """Temp artifacts directory with sample data."""
    ph5 = tmp_path / "ph5_hold"
    ph5.mkdir()
    report = ph5 / "ph5_hold_metrics_report.json"
    report.write_text(
        json.dumps(_sample_hold_report()), encoding="utf-8",
    )

    (tmp_path / "alert_audit.jsonl").write_text(
        json.dumps({
            "document_id": "abc12345-dead-beef",
            "sentiment_label": "bullish",
            "priority": 9,
            "affected_assets": ["BTC/USDT"],
            "dispatched_at": "2026-04-14T10:00:00",
            "is_digest": False,
        }) + "\n",
        encoding="utf-8",
    )

    (tmp_path / "alert_outcomes.jsonl").write_text(
        json.dumps({
            "document_id": "abc12345-dead-beef",
            "outcome": "hit",
        }) + "\n",
        encoding="utf-8",
    )

    (tmp_path / "trading_loop_audit.jsonl").write_text(
        json.dumps({"status": "no_signal"}) + "\n"
        + json.dumps({"status": "no_signal"}) + "\n"
        + json.dumps({"status": "completed"}) + "\n",
        encoding="utf-8",
    )

    (tmp_path / "paper_execution_audit.jsonl").write_text(
        json.dumps({"event_type": "order_filled", "side": "buy"})
        + "\n"
        + json.dumps({"event_type": "cycle_start"}) + "\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# GET /dashboard -- HTML
# ---------------------------------------------------------------------------


def test_dashboard_returns_html() -> None:
    app = _make_app()
    with TestClient(app) as client:
        r = client.get("/dashboard")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "KAI Operator Dashboard" in r.text


def test_dashboard_html_contains_quality_bar() -> None:
    app = _make_app()
    with TestClient(app) as client:
        r = client.get("/dashboard")
    assert "quality-bar" in r.text
    assert "Precision" in r.text
    assert "/dashboard/api/quality" in r.text


def test_dashboard_html_has_auto_refresh() -> None:
    app = _make_app()
    with TestClient(app) as client:
        r = client.get("/dashboard")
    assert "setInterval(load, 60000)" in r.text


# ---------------------------------------------------------------------------
# GET /dashboard/api/quality -- JSON API
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
    assert data["precision_pct"] == 52.54
    assert data["resolved_count"] == 59
    assert data["hits"] == 31
    assert data["misses"] == 28
    assert data["priority_corr"] == 0.29
    assert data["paper_fills"] == 1
    assert data["paper_cycles"] == 255
    assert data["gate_status"] == "hold_releasable"
    assert data["loop_status_counts"]["no_signal"] == 2
    assert data["loop_status_counts"]["completed"] == 1


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


def test_quality_api_404_without_hold_report() -> None:
    app = _make_app()
    with patch.object(
        dashboard_mod, "_HOLD_REPORT",
        Path("/nonexistent/report.json"),
    ):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/quality")

    assert r.status_code == 404
    assert r.json()["error"] == "hold_report_not_found"


# ---------------------------------------------------------------------------
# Auth exemption: /dashboard/* paths pass without bearer
# ---------------------------------------------------------------------------


def test_dashboard_exempt_from_auth() -> None:
    app = _make_app(api_key="secret-key")
    with TestClient(app) as client:
        r = client.get("/dashboard")
    assert r.status_code == 200


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
        '{"a":1}\nnot-json\n{"b":2}\n', encoding="utf-8",
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


def test_load_hold_report_none_for_missing() -> None:
    with patch.object(
        dashboard_mod, "_HOLD_REPORT",
        Path("/does/not/exist.json"),
    ):
        assert _load_hold_report() is None


def test_load_hold_report_none_for_bad_json(
    tmp_path: Path,
) -> None:
    f = tmp_path / "bad.json"
    f.write_text("not json at all", encoding="utf-8")
    with patch.object(dashboard_mod, "_HOLD_REPORT", f):
        assert _load_hold_report() is None


# ---------------------------------------------------------------------------
# Route inventory in main app
# ---------------------------------------------------------------------------


def test_dashboard_routes_in_main_app() -> None:
    from app.api.main import create_app

    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/dashboard" in paths
    assert "/dashboard/api/quality" in paths
