"""End-to-end tests für GET /operator/portfolio/realized-by-asset und
GET /operator/paper-pipeline-status (Forensik 2026-05-25).

Diese Endpoints sind die operative Antwort auf den Operator-Eindruck "Paper-
Portfolio ist eingefroren". Sie lesen direkt aus paper_execution_audit.jsonl
ohne Live-Mode, ohne Backtest-Endpoint, ohne Exchange-Call.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.operator import router
from app.core.settings import get_settings


def _make_app(api_key: str = "test-key") -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    def _override_settings() -> SimpleNamespace:
        return SimpleNamespace(api_key=api_key, cf_access_allowed_emails="")
    app.dependency_overrides[get_settings] = _override_settings
    return app


def _close_event(symbol: str, pnl: float, *, ts: str) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": ts,
        "symbol": symbol,
        "quantity": 1.0,
        "trade_pnl_usd": pnl,
        "fee_usd": 0.0,
    }


def test_realized_by_asset_requires_auth(tmp_path):
    c = TestClient(_make_app())
    r = c.get("/operator/portfolio/realized-by-asset")
    assert r.status_code == 401


def test_realized_by_asset_returns_aggregation(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        "\n".join(json.dumps(e) for e in [
            _close_event("BTC/USDT", 500.0, ts="2026-05-01T10:00:00+00:00"),
            _close_event("ETH/USDT", -100.0, ts="2026-05-02T10:00:00+00:00"),
            _close_event("BTC/USDT", 200.0, ts="2026-05-03T10:00:00+00:00"),
        ]) + "\n",
        encoding="utf-8",
    )
    c = TestClient(_make_app())
    r = c.get(
        f"/operator/portfolio/realized-by-asset?audit_path={audit}",
        headers={"Authorization": "Bearer test-key"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["available"] is True
    assert data["totals"]["realized_pnl_usd"] == 600.0
    assert data["totals"]["closed_trades"] == 3
    assert data["totals"]["assets_count"] == 2
    assert data["top_performer"]["symbol"] == "BTC/USDT"
    assert data["worst_performer"]["symbol"] == "ETH/USDT"


def test_realized_by_asset_missing_audit_file_returns_200(tmp_path):
    """Even when audit file missing, endpoint must NOT 5xx — Forensik-Anforderung:
    "Operator sieht Diagnose statt Crash"."""
    c = TestClient(_make_app())
    r = c.get(
        f"/operator/portfolio/realized-by-asset?audit_path={tmp_path}/none.jsonl",
        headers={"Authorization": "Bearer test-key"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["error"] == "audit_file_missing"


def test_paper_pipeline_status_returns_shape(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        "\n".join(json.dumps(e) for e in [
            {
                "schema_version": "v2",
                "event_type": "order_filled",
                "timestamp_utc": "2026-05-20T12:00:00+00:00",
                "fill_id": "f1",
                "order_id": "o1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": 1.0,
                "fill_price": 70000.0,
                "fee_usd": 0.0,
                "filled_at": "2026-05-20T12:00:00+00:00",
                "slippage_pct": 0.0,
                "pnl_usd": 0.0,
                "position_side": "long",
                "fee_venue": "paper",
                "fee_role": "taker",
                "fee_bps_applied": 0.0,
                "fee_table_version": "1.0.0",
                "correlation_id": "",
                "portfolio_cash": 10000.0,
                "realized_pnl_usd": 0.0,
            },
            _close_event("BTC/USDT", 500.0, ts="2026-05-21T10:00:00+00:00"),
        ]) + "\n",
        encoding="utf-8",
    )
    cron = tmp_path / "cron.log"
    cron.write_text(
        "2026-05-25 10:00:00  BTC/USDT  cycle=cyc_a  status=priority_rejected  fill=False\n"
        "2026-05-25 10:10:00  ETH/USDT  cycle=cyc_b  status=priority_rejected  fill=False\n",
        encoding="utf-8",
    )
    blocked = tmp_path / "blocked.jsonl"
    blocked.write_text(
        "\n".join(json.dumps(e) for e in [
            {"block_reason": "not_actionable",
             "blocked_at": "2026-05-25T05:00:00+00:00"},
            {"block_reason": "not_actionable",
             "blocked_at": "2026-05-25T06:00:00+00:00"},
            {"block_reason": "bearish_directional_disabled",
             "blocked_at": "2026-05-25T07:00:00+00:00"},
        ]) + "\n",
        encoding="utf-8",
    )
    c = TestClient(_make_app())
    r = c.get(
        f"/operator/paper-pipeline-status"
        f"?audit_path={audit}"
        f"&loop_audit_path={tmp_path}/loop_missing.jsonl"
        f"&cron_log_path={cron}"
        f"&blocked_alerts_path={blocked}",
        headers={"Authorization": "Bearer test-key"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # Shape checks
    assert "audit_files" in data
    assert "replay_health" in data
    assert "cron_recent_1000" in data
    assert "block_reasons_24h" in data
    assert "realized_summary" in data
    assert "freeze_indicators" in data
    # Replay must work and find the fill
    assert data["replay_health"]["available"] is True
    paper_audit_meta = data["audit_files"]["paper_execution_audit"]
    assert paper_audit_meta["last_order_filled_utc"] == "2026-05-20T12:00:00+00:00"
    assert paper_audit_meta["last_position_close_utc"] == "2026-05-21T10:00:00+00:00"
    # Cron counter
    assert data["cron_recent_1000"]["priority_rejected"] == 2
    assert data["cron_recent_1000"]["completed"] == 0
    assert data["cron_recent_1000"]["priority_rejected_share_pct"] == 100.0
    # Block reasons aggregation
    assert data["block_reasons_24h"]["not_actionable"] == 2
    assert data["block_reasons_24h"]["bearish_directional_disabled"] == 1
    # Realized summary
    assert data["realized_summary"]["total_realized_pnl_usd"] == 500.0
    # Freeze indicator: all cron priority_rejected
    assert data["freeze_indicators"]["all_cron_priority_rejected"] is True


def test_paper_pipeline_status_handles_missing_files_gracefully(tmp_path):
    """Wenn keine Logfiles existieren darf der Endpoint nicht 5xx werfen."""
    c = TestClient(_make_app())
    r = c.get(
        "/operator/paper-pipeline-status"
        f"?audit_path={tmp_path}/none1.jsonl"
        f"&loop_audit_path={tmp_path}/none2.jsonl"
        f"&cron_log_path={tmp_path}/none3.log"
        f"&blocked_alerts_path={tmp_path}/none4.jsonl",
        headers={"Authorization": "Bearer test-key"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["audit_files"]["paper_execution_audit"]["exists"] is False
    assert data["replay_health"]["available"] is True  # empty/missing returns available
    assert data["cron_recent_1000"]["total_status_rows"] == 0
    assert data["block_reasons_24h"] == {}
