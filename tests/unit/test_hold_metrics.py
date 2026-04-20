from __future__ import annotations

import json
from pathlib import Path

from app.alerts.hold_metrics import (
    MIN_ACTIVE_PRECISION_PCT,
    MIN_RESOLVED_DIRECTIONAL_ALERTS,
    build_hold_metrics_report,
)


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
    # D-149: priority tier fields present even at small n.
    assert quality["priority_tier_high_conviction_threshold"] == 10
    # P8+P9 both fall into standard tier (P7-P9), none in P10 tier.
    assert quality["priority_tier_high_conviction_resolved"] == 0
    assert quality["priority_tier_standard_resolved"] == 2
    assert quality["priority_tier_standard_hit_rate_pct"] == 50.0
    assert quality["priority_hit_correlation_deprecated_reason"] == (
        "non_monotonic_within_p7_p10_band_see_d149"
    )


def test_hold_metrics_priority_tier_splits_p10_from_p7_p9(tmp_path: Path) -> None:
    """D-149: P10 should bucket separately; lift = P10_rate - standard_rate."""
    alert_audit = tmp_path / "alert_audit.jsonl"
    alert_outcomes = tmp_path / "alert_outcomes.jsonl"
    trading_loop_audit = tmp_path / "trading_loop_audit.jsonl"
    paper_execution_audit = tmp_path / "paper_execution_audit.jsonl"

    # Four docs: two P10 (1 hit, 1 miss → 50%), two P8 (0 hit, 2 miss → 0%).
    _write_jsonl(
        alert_audit,
        [
            {
                "document_id": f"doc-{i}",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": f"2026-03-25T10:0{i}:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
                "priority": p,
                "actionable": True,
            }
            for i, p in enumerate([10, 10, 8, 8])
        ],
    )
    _write_jsonl(
        alert_outcomes,
        [
            {"document_id": "doc-0", "outcome": "hit"},
            {"document_id": "doc-1", "outcome": "miss"},
            {"document_id": "doc-2", "outcome": "miss"},
            {"document_id": "doc-3", "outcome": "miss"},
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
    assert quality["priority_tier_high_conviction_resolved"] == 2
    assert quality["priority_tier_high_conviction_hit_rate_pct"] == 50.0
    assert quality["priority_tier_standard_resolved"] == 2
    assert quality["priority_tier_standard_hit_rate_pct"] == 0.0
    # Lift = 50.0 - 0.0 = 50.0 pp
    assert quality["priority_tier_lift_pct"] == 50.0
    # Wilson CI fields are populated (bounded floats in [0,100]).
    assert 0 <= quality["priority_tier_high_conviction_ci_low_pct"] <= 100
    assert 0 <= quality["priority_tier_high_conviction_ci_high_pct"] <= 100


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
    # D-142: bearish is blocked before asset resolution in current re-evaluation
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


# ── D-151: Hold-Gate enforces sample-size + active-precision + paper ─────────


def _build_gate_fixture(
    tmp_path: Path,
    *,
    resolved_count: int,
    hits: int,
    source_for_docs: str = "rss",
    paper_fills: int = 5,
    paper_cycles: int = 15,
    pnl: float = 0.0,
) -> dict[str, object]:
    """Build a minimal hold-metrics report with controllable gate inputs."""
    alert_audit = tmp_path / "alert_audit.jsonl"
    alert_outcomes = tmp_path / "alert_outcomes.jsonl"
    trading_loop_audit = tmp_path / "trading_loop_audit.jsonl"
    paper_execution_audit = tmp_path / "paper_execution_audit.jsonl"

    audits: list[dict[str, object]] = []
    outcomes: list[dict[str, object]] = []
    source_map: dict[str, str] = {}
    for i in range(resolved_count):
        doc_id = f"doc-{i}"
        audits.append(
            {
                "document_id": doc_id,
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-04-01T10:00:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC/USDT"],
                "priority": 8,
                "actionable": True,
                "directional_eligible": True,
            }
        )
        outcomes.append(
            {
                "document_id": doc_id,
                "outcome": "hit" if i < hits else "miss",
                "annotated_at": "2026-04-01T12:00:00+00:00",
            }
        )
        source_map[doc_id] = source_for_docs
    _write_jsonl(alert_audit, audits)
    _write_jsonl(alert_outcomes, outcomes)

    loop_rows = [
        {
            "cycle_id": f"cyc-{i}",
            "started_at": "2026-04-01T09:00:00+00:00",
            "symbol": "BTC/USDT",
            "status": "completed",
            "fill_simulated": i < paper_fills,
            "notes": ["market_data_source:coingecko"],
            "completed_at": "2026-04-01T09:01:00+00:00",
        }
        for i in range(paper_cycles)
    ]
    _write_jsonl(trading_loop_audit, loop_rows)

    exec_rows = [
        {"event_type": "order_created"} for _ in range(paper_fills)
    ] + [
        {"event_type": "order_filled", "realized_pnl_usd": pnl}
        for _ in range(paper_fills)
    ]
    _write_jsonl(paper_execution_audit, exec_rows)

    return build_hold_metrics_report(
        alert_audit_path=alert_audit,
        alert_outcomes_path=alert_outcomes,
        trading_loop_audit_path=trading_loop_audit,
        paper_execution_audit_path=paper_execution_audit,
        source_by_doc=source_map,
    )


def test_hold_gate_constants_match_d151(tmp_path: Path) -> None:
    assert MIN_RESOLVED_DIRECTIONAL_ALERTS == 200
    assert MIN_ACTIVE_PRECISION_PCT == 60.0


def test_hold_gate_sample_size_below_threshold_blocks(tmp_path: Path) -> None:
    """Fewer than 200 resolved → alert_hit_rate_condition unmet."""
    report = _build_gate_fixture(tmp_path, resolved_count=100, hits=70)
    gate = report["hold_gate_evaluation"]
    assert gate["alert_hit_rate_condition_met"] is False
    assert gate["overall_status"] == "hold_remains_active"
    assert "resolved_directional_below_200" in gate["blocking_reasons"]


def test_hold_gate_active_precision_below_threshold_blocks(tmp_path: Path) -> None:
    """n=200 but precision=40% → active_precision_condition unmet."""
    report = _build_gate_fixture(tmp_path, resolved_count=200, hits=80)
    gate = report["hold_gate_evaluation"]
    assert gate["alert_hit_rate_condition_met"] is True
    assert gate["active_precision_condition_met"] is False
    assert gate["overall_status"] == "hold_remains_active"
    assert "active_precision_below_60_pct" in gate["blocking_reasons"]


def test_hold_gate_all_conditions_met_releases(tmp_path: Path) -> None:
    """n=200, precision=60%, paper positive → hold_releasable."""
    report = _build_gate_fixture(
        tmp_path, resolved_count=200, hits=120, paper_fills=5, pnl=1.0,
    )
    gate = report["hold_gate_evaluation"]
    assert gate["alert_hit_rate_condition_met"] is True
    assert gate["active_precision_condition_met"] is True
    assert gate["paper_trading_condition_met"] is True
    assert gate["feature_work_unblocked"] is True
    assert gate["overall_status"] == "hold_releasable"
    assert gate["blocking_reasons"] == []


def test_hold_gate_legacy_unknown_excluded_from_active(tmp_path: Path) -> None:
    """Legacy `source=unknown` docs don't count toward active precision."""
    # 200 resolved, 80 hits (40%) — but all legacy_unknown → active_resolved=0
    report = _build_gate_fixture(
        tmp_path, resolved_count=200, hits=80, source_for_docs="unknown",
    )
    gate = report["hold_gate_evaluation"]
    hit = report["alert_hit_rate_evidence"]
    assert hit["active_resolved_directional_documents"] == 0
    # With no active sample, condition cannot be met.
    assert gate["active_precision_condition_met"] is False
    assert "active_precision_below_60_pct" in gate["blocking_reasons"]


def test_hold_gate_exposes_thresholds(tmp_path: Path) -> None:
    """Gate output advertises the numeric thresholds for downstream consumers."""
    report = _build_gate_fixture(tmp_path, resolved_count=50, hits=30)
    gate = report["hold_gate_evaluation"]
    assert gate["minimum_resolved_directional_alerts_for_gate"] == 200
    assert gate["minimum_active_precision_pct_for_gate"] == 60.0
