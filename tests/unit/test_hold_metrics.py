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
                "affected_assets": ["BTC"],
                "priority": 9,
                "actionable": True,
            },
            {
                "document_id": "doc-2",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-03-25T10:01:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["ETH"],
                "priority": 8,
                "actionable": False,
                "directional_eligible": True,
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
    assert quality["resolved_false_positive_rate_pct"] == 50.0
    assert quality["priority_calibration_finding"] == "insufficient_sample"
    assert quality["priority_hit_correlation"] == 1.0
    assert quality["priority_hit_correlation_sample"] == 2
    # Both docs are high priority (P8, P9 ≥ threshold 7): 1 hit, 1 miss
    assert quality["high_priority_hit_rate_pct"] == 50.0
    assert quality["low_priority_hit_rate_pct"] is None
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


def test_hold_metrics_excludes_blocked_directional_alerts(tmp_path: Path) -> None:
    alert_audit = tmp_path / "alert_audit.jsonl"
    alert_outcomes = tmp_path / "alert_outcomes.jsonl"
    trading_loop_audit = tmp_path / "trading_loop_audit.jsonl"
    paper_execution_audit = tmp_path / "paper_execution_audit.jsonl"

    _write_jsonl(
        alert_audit,
        [
            {
                "document_id": "doc-crypto",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-03-25T10:00:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC/USDT"],
                "priority": 8,
                "actionable": True,
                "directional_eligible": True,
            },
            {
                "document_id": "doc-non-crypto",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-03-25T10:01:00+00:00",
                "sentiment_label": "bearish",
                "affected_assets": [],
                "priority": 8,
                "actionable": True,
                "directional_eligible": False,
                "directional_block_reason": "unsupported_or_non_crypto_assets",
                "directional_blocked_assets": ["OPENAI"],
            },
        ],
    )
    _write_jsonl(
        alert_outcomes,
        [
            {
                "document_id": "doc-crypto",
                "outcome": "hit",
                "annotated_at": "2026-03-25T11:00:00+00:00",
            },
            {
                "document_id": "doc-non-crypto",
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

    hit = report["alert_hit_rate_evidence"]
    quality = report["signal_quality_validation"]
    assert hit["directional_alert_documents"] == 1
    assert hit["blocked_directional_documents"] == 1
    # D-127: bearish is blocked before asset resolution in current re-evaluation
    assert hit["blocked_directional_by_reason"] == {
        "bearish_directional_disabled": 1
    }
    assert hit["resolved_directional_documents"] == 1
    assert quality["resolved_precision_pct"] == 100.0


def test_forward_simulation_uses_source_by_doc(tmp_path: Path) -> None:
    """source_by_doc filters low-precision sources in forward simulation."""
    alert_audit = tmp_path / "alert_audit.jsonl"
    alert_outcomes = tmp_path / "alert_outcomes.jsonl"
    trading_loop_audit = tmp_path / "trading_loop_audit.jsonl"
    paper_execution_audit = tmp_path / "paper_execution_audit.jsonl"

    _write_jsonl(
        alert_audit,
        [
            {
                "document_id": "doc-good",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-04-14T10:00:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
                "priority": 9,
                "actionable": True,
                "directional_eligible": True,
            },
            {
                "document_id": "doc-decrypt",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-04-14T10:01:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
                "priority": 9,
                "actionable": True,
                "directional_eligible": True,
            },
        ],
    )
    _write_jsonl(
        alert_outcomes,
        [
            {"document_id": "doc-good", "outcome": "hit",
             "annotated_at": "2026-04-14T11:00:00+00:00"},
            {"document_id": "doc-decrypt", "outcome": "miss",
             "annotated_at": "2026-04-14T11:01:00+00:00"},
        ],
    )
    _write_jsonl(trading_loop_audit, [])
    _write_jsonl(paper_execution_audit, [])

    # Without source_by_doc: both docs are forward-eligible
    report_no_src = build_hold_metrics_report(
        alert_audit_path=alert_audit,
        alert_outcomes_path=alert_outcomes,
        trading_loop_audit_path=trading_loop_audit,
        paper_execution_audit_path=paper_execution_audit,
    )
    fwd_no = report_no_src["forward_simulation"]
    assert fwd_no["resolved"] == 2
    assert fwd_no["hits"] == 1
    assert fwd_no["miss"] == 1

    # With source_by_doc: decrypt miss gets filtered
    report_src = build_hold_metrics_report(
        alert_audit_path=alert_audit,
        alert_outcomes_path=alert_outcomes,
        trading_loop_audit_path=trading_loop_audit,
        paper_execution_audit_path=paper_execution_audit,
        source_by_doc={"doc-good": "cointelegraph", "doc-decrypt": "decrypt"},
    )
    fwd_src = report_src["forward_simulation"]
    assert fwd_src["resolved"] == 1
    assert fwd_src["hits"] == 1
    assert fwd_src["miss"] == 0
    assert fwd_src["filtered_out"] == 1
    assert fwd_src["precision_pct"] == 100.0


def test_forward_simulation_prefers_audit_source_name(tmp_path: Path) -> None:
    """source_name from audit record takes precedence over source_by_doc."""
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
                "dispatched_at": "2026-04-14T10:00:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
                "priority": 9,
                "actionable": True,
                "directional_eligible": True,
                "source_name": "cointelegraph",
            },
        ],
    )
    _write_jsonl(
        alert_outcomes,
        [
            {"document_id": "doc-1", "outcome": "hit",
             "annotated_at": "2026-04-14T11:00:00+00:00"},
        ],
    )
    _write_jsonl(trading_loop_audit, [])
    _write_jsonl(paper_execution_audit, [])

    # source_by_doc says "decrypt" but audit record says "cointelegraph" — audit wins
    report = build_hold_metrics_report(
        alert_audit_path=alert_audit,
        alert_outcomes_path=alert_outcomes,
        trading_loop_audit_path=trading_loop_audit,
        paper_execution_audit_path=paper_execution_audit,
        source_by_doc={"doc-1": "decrypt"},
    )
    fwd = report["forward_simulation"]
    assert fwd["resolved"] == 1
    assert fwd["hits"] == 1


def test_forward_simulation_filters_reactive_title(tmp_path: Path) -> None:
    """Reactive bullish titles are filtered in forward simulation."""
    alert_audit = tmp_path / "alert_audit.jsonl"
    alert_outcomes = tmp_path / "alert_outcomes.jsonl"
    trading_loop_audit = tmp_path / "trading_loop_audit.jsonl"
    paper_execution_audit = tmp_path / "paper_execution_audit.jsonl"

    _write_jsonl(
        alert_audit,
        [
            {
                "document_id": "doc-hit",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-04-14T10:00:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
                "priority": 9,
                "actionable": True,
                "directional_eligible": True,
            },
            {
                "document_id": "doc-reactive",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-04-14T10:01:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
                "priority": 10,
                "actionable": True,
                "directional_eligible": True,
            },
        ],
    )
    _write_jsonl(
        alert_outcomes,
        [
            {"document_id": "doc-hit", "outcome": "hit",
             "annotated_at": "2026-04-14T11:00:00+00:00"},
            {"document_id": "doc-reactive", "outcome": "miss",
             "annotated_at": "2026-04-14T11:01:00+00:00"},
        ],
    )
    _write_jsonl(trading_loop_audit, [])
    _write_jsonl(paper_execution_audit, [])

    # title_by_doc provides reactive title for doc-reactive
    report = build_hold_metrics_report(
        alert_audit_path=alert_audit,
        alert_outcomes_path=alert_outcomes,
        trading_loop_audit_path=trading_loop_audit,
        paper_execution_audit_path=paper_execution_audit,
        title_by_doc={
            "doc-hit": "Charles Schwab opens bitcoin trading",
            "doc-reactive": "ETF empire surging past $100 billion",
        },
    )
    fwd = report["forward_simulation"]
    # doc-reactive "surging" → reactive bullish → filtered
    assert fwd["resolved"] == 1
    assert fwd["hits"] == 1
    assert fwd["miss"] == 0
    assert fwd["filtered_out"] == 1
