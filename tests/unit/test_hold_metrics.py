from __future__ import annotations

import json
from pathlib import Path

from app.alerts.hold_metrics import build_hold_metrics_report


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def test_hold_metrics_reports_signal_quality_validation_fields(tmp_path: Path) -> None:
    alert_audit = tmp_path / "alert_audit.jsonl"
    alert_outcomes = tmp_path / "alert_outcomes.jsonl"
    trading_loop_audit = tmp_path / "trading_loop_audit.jsonl"
    paper_execution_audit = tmp_path / "paper_execution_audit.jsonl"

    _write_jsonl(
        alert_audit,
        [
            {
                "document_id": "doc-1",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-03-25T10:00:00+00:00",
                "sentiment_label": "bullish",
                "priority": 8,
                "actionable": True,
            },
            {
                "document_id": "doc-2",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-03-25T10:01:00+00:00",
                "sentiment_label": "bearish",
                "priority": 6,
                "actionable": False,
            },
        ],
    )
    _write_jsonl(
        alert_outcomes,
        [
            {
                "document_id": "doc-1",
                "outcome": "hit",
                "annotated_at": "2026-03-25T11:00:00+00:00",
            },
            {
                "document_id": "doc-2",
                "outcome": "miss",
                "annotated_at": "2026-03-25T11:05:00+00:00",
            },
        ],
    )
    _write_jsonl(trading_loop_audit, [])
    _write_jsonl(paper_execution_audit, [])

    report = build_hold_metrics_report(
        alert_audit_path=alert_audit,
        alert_outcomes_path=alert_outcomes,
        trading_loop_audit_path=trading_loop_audit,
        paper_execution_audit_path=paper_execution_audit,
    )

    quality = report["signal_quality_validation"]
    assert quality["directional_actionable_rate_pct"] == 50.0
    assert quality["resolved_precision_pct"] == 50.0
    assert quality["paper_real_price_cycle_count"] == 0
    assert "no_real_price_paper_cycles" in quality["validation_gaps"]
    assert "recall_not_computable_without_negative_ground_truth" in quality["validation_gaps"]


def test_hold_metrics_detects_real_price_cycle_source(tmp_path: Path) -> None:
    alert_audit = tmp_path / "alert_audit.jsonl"
    alert_outcomes = tmp_path / "alert_outcomes.jsonl"
    trading_loop_audit = tmp_path / "trading_loop_audit.jsonl"
    paper_execution_audit = tmp_path / "paper_execution_audit.jsonl"

    _write_jsonl(alert_audit, [])
    _write_jsonl(alert_outcomes, [])
    _write_jsonl(
        trading_loop_audit,
        [
            {
                "cycle_id": "cyc_1",
                "status": "no_signal",
                "completed_at": "2026-03-25T12:00:00+00:00",
                "notes": ["market_data_source:coingecko"],
            }
        ],
    )
    _write_jsonl(
        paper_execution_audit,
        [
            {
                "event_type": "order_filled",
                "realized_pnl_usd": 10.0,
            }
        ],
    )

    report = build_hold_metrics_report(
        alert_audit_path=alert_audit,
        alert_outcomes_path=alert_outcomes,
        trading_loop_audit_path=trading_loop_audit,
        paper_execution_audit_path=paper_execution_audit,
    )

    quality = report["signal_quality_validation"]
    assert quality["paper_real_price_cycle_count"] == 1
    assert quality["paper_market_data_source_counts"]["coingecko"] == 1
    assert quality["priority_mae_tier1_vs_teacher_baseline"] == 3.13
    assert quality["llm_error_proxy_baseline_pct"] == 27.5
