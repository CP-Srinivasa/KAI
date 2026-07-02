from __future__ import annotations

from datetime import UTC, date, datetime

from scripts.f3_v2_confidence_recalibration import (
    MIN_LABEL_RESOLVED,
    ConfidenceOutcome,
    analyze,
    build_universe,
    evaluate_triggers,
    find_optimal_threshold,
    recommendation_for_label,
    threshold_curve,
)


def _rec(label: str, confidence: float, outcome: str | None) -> ConfidenceOutcome:
    return ConfidenceOutcome(
        document_id=f"{label}-{confidence}-{outcome}",
        label=label,
        confidence=confidence,
        source_name="fixture",
        block_reason="fixture_reason",
        outcome=outcome,
        observed_at=datetime(2026, 6, 16, tzinfo=UTC),
    )


def test_threshold_curve_counts_precision_and_recall() -> None:
    records = [
        _rec("bullish", 0.90, "hit"),
        _rec("bullish", 0.85, "miss"),
        _rec("bullish", 0.70, "hit"),
        _rec("bearish", 0.95, "hit"),
    ]

    points = threshold_curve(records, label="bullish", bins=(0.70, 0.80, 0.90))

    assert points[0].resolved == 3
    assert points[0].precision_pct == 66.67
    assert points[0].recall_pct == 100.0
    assert points[1].resolved == 2
    assert points[2].hit == 1
    assert points[2].miss == 0


def test_evaluate_triggers_requires_date_total_and_each_label() -> None:
    records = [_rec("bullish", 0.9, "hit")] * 70 + [_rec("bearish", 0.9, "miss")] * 30
    triggers = evaluate_triggers(date(2026, 6, 16), records)

    assert triggers["all_met"] is True
    assert triggers["total_resolved"] == 100
    assert triggers["label_resolved"]["bearish"] == MIN_LABEL_RESOLVED


def test_evaluate_triggers_blocks_before_date() -> None:
    records = [_rec("bullish", 0.9, "hit")] * 70 + [_rec("bearish", 0.9, "miss")] * 30
    triggers = evaluate_triggers(date(2026, 6, 14), records)

    assert triggers["date_ok"] is False
    assert triggers["all_met"] is False


def test_find_optimal_threshold_returns_first_plateau_candidate() -> None:
    records = [_rec("bullish", 0.80, "hit")] * 35 + [_rec("bullish", 0.95, "hit")] * 5
    points = threshold_curve(records, label="bullish", bins=(0.80, 0.85, 0.90, 0.95))

    optimal = find_optimal_threshold(points, target_floor_pct=50.0, min_resolved=30)

    assert optimal["status"] == "found"
    assert optimal["threshold"] == 0.8
    assert optimal["next_precision_gain_pp"] == 0.0


def test_recommendation_confirms_current_threshold_when_delta_small() -> None:
    rec = recommendation_for_label(
        label="bullish",
        optimal={"status": "found", "threshold": 0.8, "point": {}, "next_precision_gain_pp": 0.0},
        current_threshold=0.8,
    )

    assert rec["decision"] == "current_threshold_confirmed"


def test_build_universe_joins_latest_outcome_and_filters() -> None:
    ts = datetime(2026, 6, 16, tzinfo=UTC).isoformat()
    blocked = [
        {
            "document_id": "b1",
            "sentiment_label": "bullish",
            "directional_confidence": 0.8,
            "blocked_at": ts,
            "source_name": "cryptobriefing",
            "block_reason": "low_directional_confidence",
        },
        {
            "document_id": "b2",
            "sentiment_label": "bearish",
            "directional_confidence": 0.9,
            "blocked_at": ts,
            "source_name": "cointelegraph",
            "block_reason": "bearish_directional_disabled",
        },
        {
            "document_id": "ignored-no-conf",
            "sentiment_label": "bullish",
            "blocked_at": ts,
        },
    ]
    outcomes = [
        {"document_id": "b1", "outcome": "miss", "annotated_at": ts},
        {"document_id": "b1", "outcome": "hit", "annotated_at": ts},
        {"document_id": "b2", "outcome": "miss", "annotated_at": ts},
        {"document_id": "ignored-no-conf", "outcome": "hit", "annotated_at": ts},
    ]

    uni = build_universe(blocked, outcomes, date(2026, 6, 16))

    assert uni.total_directional_confidence_known == 2
    assert uni.raw_outcomes == 4
    assert uni.distinct_outcomes == 3
    by_id = {r.document_id: r for r in uni.records}
    assert by_id["b1"].outcome == "hit"
    assert by_id["b1"].source_name == "cryptobriefing"
    assert by_id["b1"].block_reason == "low_directional_confidence"
    assert by_id["b2"].outcome == "miss"


def test_analyze_reports_block_reason_hit_miss_bucket(tmp_path) -> None:
    ts = datetime(2026, 6, 16, tzinfo=UTC).isoformat()
    blocked = tmp_path / "blocked_alerts.jsonl"
    outcomes = tmp_path / "blocked_outcomes.jsonl"
    blocked.write_text(
        "\n".join(
            [
                (
                    '{"document_id":"d1","sentiment_label":"bullish",'
                    '"directional_confidence":0.6,"blocked_at":"'
                    + ts
                    + '","block_reason":"low_directional_confidence"}'
                ),
                (
                    '{"document_id":"d2","sentiment_label":"bullish",'
                    '"directional_confidence":0.7,"blocked_at":"'
                    + ts
                    + '","block_reason":"low_directional_confidence"}'
                ),
                (
                    '{"document_id":"d3","sentiment_label":"bearish",'
                    '"directional_confidence":0.8,"blocked_at":"'
                    + ts
                    + '","block_reason":"not_actionable"}'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    outcomes.write_text(
        "\n".join(
            [
                '{"document_id":"d1","outcome":"hit","annotated_at":"' + ts + '"}',
                '{"document_id":"d2","outcome":"miss","annotated_at":"' + ts + '"}',
                '{"document_id":"d3","outcome":"miss","annotated_at":"' + ts + '"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = analyze(blocked, outcomes, date(2026, 6, 16))

    rows = {row["block_reason"]: row for row in result["blocked_outcome_tables"]["by_block_reason"]}
    assert rows["low_directional_confidence"]["hit"] == 1
    assert rows["low_directional_confidence"]["miss"] == 1
    assert rows["not_actionable"]["hit"] == 0
    assert rows["not_actionable"]["miss"] == 1
