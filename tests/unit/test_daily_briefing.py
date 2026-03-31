"""Tests for daily briefing and health check."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.audit import (
    ALERT_AUDIT_JSONL_FILENAME,
    ALERT_OUTCOMES_JSONL_FILENAME,
    AlertAuditRecord,
    append_alert_audit,
)
from app.alerts.daily_briefing import build_daily_briefing
from app.alerts.health_check import run_health_check


def _write_audit(tmp_path: Path, **kwargs) -> None:
    defaults = {
        "document_id": "doc-1",
        "channel": "telegram",
        "message_id": "dry_run",
        "is_digest": False,
        "dispatched_at": datetime.now(UTC).isoformat(),
        "sentiment_label": "bullish",
        "affected_assets": ["BTC/USDT"],
        "directional_eligible": True,
    }
    defaults.update(kwargs)
    rec = AlertAuditRecord(**defaults)
    append_alert_audit(rec, tmp_path / ALERT_AUDIT_JSONL_FILENAME)


def _write_cycle(tmp_path: Path, **kwargs) -> None:
    defaults = {
        "cycle_id": "cyc_test",
        "started_at": datetime.now(UTC).isoformat(),
        "symbol": "BTC/USDT",
        "status": "completed",
        "fill_simulated": True,
    }
    defaults.update(kwargs)
    audit_path = tmp_path / "trading_loop_audit.jsonl"
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(defaults) + "\n")


def _write_outcome(tmp_path: Path, doc_id: str, outcome: str) -> None:
    path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    data = {
        "document_id": doc_id,
        "outcome": outcome,
        "annotated_at": datetime.now(UTC).isoformat(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")


# ── Daily Briefing ──────────────────────────────────────────────────


def test_briefing_counts_recent_alerts(tmp_path: Path) -> None:
    _write_audit(tmp_path, document_id="d1")
    _write_audit(tmp_path, document_id="d2")
    _write_audit(
        tmp_path,
        document_id="d-old",
        dispatched_at=(
            datetime.now(UTC) - timedelta(hours=48)
        ).isoformat(),
    )

    data = build_daily_briefing(tmp_path, lookback_hours=24)
    assert data.alerts_dispatched == 2


def test_briefing_counts_blocks(tmp_path: Path) -> None:
    _write_audit(
        tmp_path,
        document_id="d1",
        directional_eligible=False,
        directional_block_reason="weak_directional_signal",
    )
    data = build_daily_briefing(tmp_path)
    assert data.alerts_blocked == 1
    assert data.block_reasons == {"weak_directional_signal": 1}


def test_briefing_precision(tmp_path: Path) -> None:
    _write_outcome(tmp_path, "d1", "hit")
    _write_outcome(tmp_path, "d2", "miss")
    _write_outcome(tmp_path, "d3", "miss")
    _write_outcome(tmp_path, "d4", "inconclusive")

    data = build_daily_briefing(tmp_path)
    assert data.hits == 1
    assert data.misses == 2
    assert data.inconclusive == 1
    assert data.precision_pct is not None
    assert abs(data.precision_pct - 33.3) < 0.5


def test_briefing_trading_cycles(tmp_path: Path) -> None:
    _write_cycle(tmp_path, cycle_id="c1", status="completed")
    _write_cycle(tmp_path, cycle_id="c2", status="no_signal", fill_simulated=False)

    data = build_daily_briefing(tmp_path)
    assert data.cycles_total == 2
    assert data.cycles_completed == 1
    assert data.fills == 1


def test_briefing_to_text(tmp_path: Path) -> None:
    _write_audit(tmp_path)
    data = build_daily_briefing(tmp_path)
    text = data.to_text()
    assert "KAI Daily Briefing" in text
    assert "Dispatched:" in text


# ── Health Check ────────────────────────────────────────────────────


def test_healthy_system(tmp_path: Path) -> None:
    # Write enough alerts and cycles to pass all checks
    for i in range(5):
        _write_audit(tmp_path, document_id=f"d{i}")
    for i in range(15):
        _write_cycle(tmp_path, cycle_id=f"c{i}")

    issues = run_health_check(tmp_path, min_expected_alerts=1)
    assert issues == []


def test_no_alerts_warning(tmp_path: Path) -> None:
    issues = run_health_check(
        tmp_path, min_expected_alerts=5, min_expected_cycles=0,
    )
    alert_issues = [i for i in issues if i.component == "alerts"]
    assert len(alert_issues) == 1
    assert alert_issues[0].severity == "warning"


def test_high_error_rate_critical(tmp_path: Path) -> None:
    for i in range(10):
        _write_cycle(
            tmp_path,
            cycle_id=f"c{i}",
            status="no_market_data",
            fill_simulated=False,
        )

    issues = run_health_check(
        tmp_path, min_expected_alerts=0, min_expected_cycles=0,
    )
    error_issues = [
        i for i in issues
        if i.component == "trading_loop" and i.severity == "critical"
    ]
    assert len(error_issues) == 1


def test_low_precision_warning(tmp_path: Path) -> None:
    # 2 hits, 18 misses = 10% precision
    for i in range(2):
        _write_outcome(tmp_path, f"hit-{i}", "hit")
    for i in range(18):
        _write_outcome(tmp_path, f"miss-{i}", "miss")

    issues = run_health_check(
        tmp_path, min_expected_alerts=0, min_expected_cycles=0,
        min_precision_pct=15.0,
    )
    prec_issues = [i for i in issues if i.component == "precision"]
    assert len(prec_issues) == 1
