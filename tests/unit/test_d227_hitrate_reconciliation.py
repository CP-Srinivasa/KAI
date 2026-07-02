"""D-227 vs hit_rate reconciliation (read-only diagnostic)."""

from __future__ import annotations

from app.alerts.d227_hitrate_reconciliation import (
    VERDICT_CALIBRATED,
    VERDICT_INSUFFICIENT,
    VERDICT_OVER_BLOCKING,
    reconcile_d227_vs_hitrate,
    render_reconciliation,
)


def _blocked(hit: int, miss: int, *, sentiment_rows=None) -> dict:
    """Build a minimal blocked_outcome_report-shaped dict."""
    resolved = hit + miss
    return {
        "hit_miss_by_block_reason": [
            {"block_reason": "r", "hit": hit, "miss": miss, "resolved": resolved}
        ],
        "hit_miss_by_sentiment": sentiment_rows or [],
    }


def _hitrate(hit: int, miss: int, *, by_sentiment=None) -> dict:
    """Build a minimal HitRateReport.to_dict()-shaped dict."""
    resolved = hit + miss
    return {
        "resolved_count": resolved,
        "hit_count": hit,
        "miss_count": miss,
        "hit_rate_pct": None if resolved == 0 else round(hit / resolved * 100.0, 2),
        "by_sentiment": by_sentiment or {},
    }


def test_insufficient_when_below_min_sample() -> None:
    r = reconcile_d227_vs_hitrate(_blocked(2, 1), _hitrate(30, 10), min_sample=20)
    assert r["overall"]["verdict"] == VERDICT_INSUFFICIENT
    assert r["influences_execution"] is False


def test_over_blocking_when_blocked_hits_like_dispatched() -> None:
    # blocked hit-rate 60% vs dispatched 62% (within tolerance) -> over-blocking
    r = reconcile_d227_vs_hitrate(
        _blocked(60, 40), _hitrate(62, 38), min_sample=20, tolerance_pct=5.0
    )
    o = r["overall"]
    assert o["blocked_hit_rate_pct"] == 60.0
    assert o["dispatched_hit_rate_pct"] == 62.0
    assert o["delta_pct"] == 2.0
    assert o["verdict"] == VERDICT_OVER_BLOCKING


def test_calibrated_when_blocked_clearly_lower() -> None:
    # blocked 20% vs dispatched 70% -> suppression well calibrated
    r = reconcile_d227_vs_hitrate(
        _blocked(20, 80), _hitrate(70, 30), min_sample=20, tolerance_pct=5.0
    )
    assert r["overall"]["verdict"] == VERDICT_CALIBRATED
    assert r["overall"]["delta_pct"] == 50.0


def test_per_sentiment_reconciliation() -> None:
    blocked = _blocked(
        30,
        70,
        sentiment_rows=[
            {"sentiment": "bullish", "hit": 25, "miss": 25, "resolved": 50},
            {"sentiment": "bearish", "hit": 5, "miss": 45, "resolved": 50},
        ],
    )
    hr = _hitrate(
        60,
        40,
        by_sentiment={
            "bullish": {"resolved": 50, "hits": 26, "hit_rate_pct": 52.0},
            "bearish": {"resolved": 50, "hits": 34, "hit_rate_pct": 68.0},
        },
    )
    r = reconcile_d227_vs_hitrate(blocked, hr, min_sample=20, tolerance_pct=5.0)
    bull = r["by_sentiment"]["bullish"]
    assert bull["blocked_hit_rate_pct"] == 50.0
    assert bull["dispatched_hit_rate_pct"] == 52.0
    assert bull["verdict"] == VERDICT_OVER_BLOCKING  # 50 >= 52-5
    bear = r["by_sentiment"]["bearish"]
    assert bear["blocked_hit_rate_pct"] == 10.0
    assert bear["dispatched_hit_rate_pct"] == 68.0
    assert bear["verdict"] == VERDICT_CALIBRATED


def test_missing_sentiment_on_one_side_is_insufficient() -> None:
    blocked = _blocked(
        30, 70, sentiment_rows=[{"sentiment": "bullish", "hit": 25, "miss": 25, "resolved": 50}]
    )
    hr = _hitrate(60, 40, by_sentiment={})  # dispatched has no per-sentiment data
    r = reconcile_d227_vs_hitrate(blocked, hr, min_sample=20)
    assert r["by_sentiment"]["bullish"]["dispatched_resolved"] == 0
    assert r["by_sentiment"]["bullish"]["verdict"] == VERDICT_INSUFFICIENT


def test_render_contains_overall_and_verdict() -> None:
    r = reconcile_d227_vs_hitrate(_blocked(20, 80), _hitrate(70, 30), min_sample=20)
    text = render_reconciliation(r)
    assert "RECONCILIATION" in text
    assert "OVERALL" in text
    assert VERDICT_CALIBRATED in text
