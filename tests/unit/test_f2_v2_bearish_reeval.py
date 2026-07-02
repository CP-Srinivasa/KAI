"""Tests for the F2-V2 bearish re-eval analysis (scripts.f2_v2_bearish_reeval).

Covers the pure decision logic: Wilson lower bound, priority bucketing, rolling
window, trigger evaluation, the §4/§5 recommendation mapping, and the
audit↔outcome join. The script activates nothing — these tests assert it only
computes and recommends.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from scripts.f2_v2_bearish_reeval import (
    N_BEARISH_MIN,
    TRIGGER_DATE,
    BearishOutcome,
    BucketStat,
    bucket_stats,
    build_universe,
    combined_stat,
    coverage_pct,
    evaluate_triggers,
    in_window,
    priority_bucket,
    recommend,
    rolling_window_start,
    wilson_lower_pct,
)

# ── wilson_lower_pct ─────────────────────────────────────────────────────────


def test_wilson_lower_empty_is_none() -> None:
    assert wilson_lower_pct(0, 0) is None


def test_wilson_lower_all_hits_below_100() -> None:
    val = wilson_lower_pct(10, 10)
    assert val is not None
    assert 0 < val < 100  # Wilson never reaches 100% on finite n


def test_wilson_lower_small_sample_is_low() -> None:
    # 1 hit / 17 (V1-shaped) → lower bound well under 15%.
    val = wilson_lower_pct(1, 17)
    assert val is not None
    assert val < 15.0


# ── priority_bucket ──────────────────────────────────────────────────────────


def test_priority_bucket_boundaries() -> None:
    assert priority_bucket(10) == "p>=10"
    assert priority_bucket(11) == "p>=10"
    assert priority_bucket(9) == "p=8/9"
    assert priority_bucket(8) == "p=8/9"
    assert priority_bucket(7) == "p<8"
    assert priority_bucket(None) == "unknown"


# ── rolling window ───────────────────────────────────────────────────────────


def test_rolling_window_uses_floor_when_recent() -> None:
    # today close to floor → 8w-back is before floor → floor wins.
    today = date(2026, 5, 1)
    assert rolling_window_start(today) == date(2026, 4, 15)


def test_rolling_window_uses_8w_when_far_from_floor() -> None:
    today = date(2026, 8, 1)
    assert rolling_window_start(today) == today - timedelta(weeks=8)


def test_in_window_inclusive_and_excludes_outside() -> None:
    start = date(2026, 5, 1)
    today = date(2026, 5, 28)
    inside = datetime(2026, 5, 10, tzinfo=UTC)
    before = datetime(2026, 4, 1, tzinfo=UTC)
    after = datetime(2026, 6, 1, tzinfo=UTC)
    assert in_window(inside, start, today) is True
    assert in_window(before, start, today) is False
    assert in_window(after, start, today) is False
    assert in_window(None, start, today) is False


# ── bucket / combined / coverage ─────────────────────────────────────────────


def _rec(priority: int | None, outcome: str | None) -> BearishOutcome:
    return BearishOutcome(
        document_id=f"d-{priority}-{outcome}",
        priority=priority,
        outcome=outcome,
        dispatched_at=datetime(2026, 5, 10, tzinfo=UTC),
    )


def test_bucket_stats_counts_only_resolved() -> None:
    records = [
        _rec(10, "hit"),
        _rec(10, "miss"),
        _rec(10, "inconclusive"),  # ignored
        _rec(8, "miss"),
        _rec(None, "hit"),
    ]
    buckets = bucket_stats(records)
    assert buckets["p>=10"].hit == 1
    assert buckets["p>=10"].miss == 1
    assert buckets["p>=10"].resolved == 2
    assert buckets["p=8/9"].miss == 1
    assert "unknown" in buckets
    # inconclusive did not create/extend a bucket beyond resolved counting
    assert buckets["p>=10"].resolved == 2


def test_combined_stat_precision_and_wilson() -> None:
    records = [_rec(10, "hit")] * 3 + [_rec(10, "miss")] * 7
    combined = combined_stat(records)
    assert combined.resolved == 10
    assert combined.precision_pct == 30.0
    assert combined.wilson_lower_pct is not None


def test_coverage_pct_and_zero_universe() -> None:
    assert coverage_pct(5, 20) == 25.0
    assert coverage_pct(0, 0) is None


# ── trigger evaluation ───────────────────────────────────────────────────────


def test_triggers_all_met() -> None:
    t = evaluate_triggers(date(2026, 6, 20), coverage=25.0, n_bearish_resolved=40)
    assert t["all_met"] is True
    assert t["met_count"] == 3


def test_triggers_date_blocks() -> None:
    t = evaluate_triggers(date(2026, 5, 28), coverage=25.0, n_bearish_resolved=40)
    assert t["date_ok"] is False
    assert t["all_met"] is False


def test_triggers_coverage_strict_greater_than() -> None:
    # exactly 20% does NOT pass (spec: > 20%).
    t = evaluate_triggers(date(2026, 6, 20), coverage=20.0, n_bearish_resolved=40)
    assert t["coverage_ok"] is False


# ── recommendation mapping (§4/§5) ───────────────────────────────────────────


def _combined(hits: int, misses: int) -> BucketStat:
    return BucketStat(bucket="combined", hit=hits, miss=misses)


def test_recommend_before_trigger_date_waits() -> None:
    triggers = evaluate_triggers(date(2026, 5, 28), 25.0, 40)
    rec = recommend(date(2026, 5, 28), triggers, _combined(20, 20))
    assert rec["decision"] == "wait_until_trigger_date"
    assert rec["next_eval_date"] == TRIGGER_DATE.isoformat()


def test_recommend_defers_when_n_too_low() -> None:
    today = date(2026, 6, 20)
    triggers = evaluate_triggers(today, 25.0, 5)
    rec = recommend(today, triggers, _combined(2, 3))
    assert rec["decision"] == "defer_n_too_low"


def test_recommend_d142_confirmed_when_wilson_under_5() -> None:
    today = date(2026, 6, 20)
    # 1 hit / 39 → precision ~2.6%, wilson lower well below 5%.
    combined = _combined(1, 39)
    triggers = evaluate_triggers(today, 25.0, combined.resolved)
    rec = recommend(today, triggers, combined)
    assert rec["decision"] == "d142_confirmed_hardened"
    assert rec["stage"] == "stop"


def test_recommend_shadow_eligible_when_strong() -> None:
    today = date(2026, 6, 20)
    # 35 hit / 15 miss → precision 70%, n=50, wilson lower >= 15%.
    combined = _combined(35, 15)
    triggers = evaluate_triggers(today, 40.0, combined.resolved)
    rec = recommend(today, triggers, combined)
    assert rec["decision"] == "shadow_start_eligible"
    assert rec["stage"] == "shadow-eligible"
    assert rec["next_eval_date"] is None


def test_recommend_inconclusive_band() -> None:
    today = date(2026, 6, 20)
    # Tune so wilson lower lands in [5, 15): ~18 hit / 42 miss → precision 30%,
    # but with n=60 wilson lower is ~20% — too high. Use weaker precision.
    # 12 hit / 48 miss → precision 20%, n=60 → wilson lower ~ 11-12%.
    combined = _combined(12, 48)
    triggers = evaluate_triggers(today, 40.0, combined.resolved)
    rec = recommend(today, triggers, combined)
    assert rec["decision"] == "inconclusive"


# ── audit↔outcome join (build_universe) ──────────────────────────────────────


def test_build_universe_joins_and_filters() -> None:
    today = date(2026, 5, 28)
    in_win = datetime(2026, 5, 10, tzinfo=UTC).isoformat()
    audit = [
        {
            "document_id": "b1",
            "sentiment_label": "bearish",
            "actionable": True,
            "priority": 10,
            "dispatched_at": in_win,
            "directional_confidence": 0.8,
        },
        {
            "document_id": "b2",
            "sentiment_label": "bearish",
            "actionable": True,
            "priority": 8,
            "dispatched_at": in_win,
        },
        # bullish → excluded
        {
            "document_id": "u1",
            "sentiment_label": "bullish",
            "actionable": True,
            "priority": 10,
            "dispatched_at": in_win,
        },
        # actionable False → excluded
        {
            "document_id": "b3",
            "sentiment_label": "bearish",
            "actionable": False,
            "priority": 10,
            "dispatched_at": in_win,
        },
        # out of window → excluded
        {
            "document_id": "b4",
            "sentiment_label": "bearish",
            "actionable": True,
            "priority": 10,
            "dispatched_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        },
    ]
    outcomes = [
        {"document_id": "b1", "outcome": "hit"},
        {"document_id": "b2", "outcome": "inconclusive"},
    ]
    uni = build_universe(audit, outcomes, today)
    assert uni.bearish_in_window == 2  # b1, b2 only
    assert uni.f3_confidence_known == 1  # b1 has confidence
    docs = {r.document_id: r for r in uni.records}
    assert docs["b1"].outcome == "hit"
    assert docs["b2"].outcome == "inconclusive"
    combined = combined_stat(uni.records)
    assert combined.resolved == 1  # only b1 (hit); b2 inconclusive not resolved


def test_build_universe_latest_outcome_wins() -> None:
    today = date(2026, 5, 28)
    in_win = datetime(2026, 5, 10, tzinfo=UTC).isoformat()
    audit = [
        {
            "document_id": "b1",
            "sentiment_label": "bearish",
            "actionable": True,
            "priority": 10,
            "dispatched_at": in_win,
        }
    ]
    outcomes = [
        {"document_id": "b1", "outcome": "inconclusive"},
        {"document_id": "b1", "outcome": "hit"},  # later line wins
    ]
    uni = build_universe(audit, outcomes, today)
    assert uni.records[0].outcome == "hit"


def test_build_universe_dedups_audit_doc_ids() -> None:
    today = date(2026, 5, 28)
    in_win = datetime(2026, 5, 10, tzinfo=UTC).isoformat()
    audit = [
        {
            "document_id": "b1",
            "sentiment_label": "bearish",
            "actionable": True,
            "priority": 10,
            "dispatched_at": in_win,
        }
    ] * 3
    uni = build_universe(audit, [], today)
    assert uni.bearish_in_window == 1


def test_n_bearish_min_constant_matches_spec() -> None:
    assert N_BEARISH_MIN == 30
