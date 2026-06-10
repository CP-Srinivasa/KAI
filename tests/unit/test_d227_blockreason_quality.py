"""D-227 block-reason suppression-quality diagnostic (read-only)."""

from __future__ import annotations

from app.alerts.d227_blockreason_quality import (
    VERDICT_CALIBRATED,
    VERDICT_INSUFFICIENT,
    VERDICT_OVER_BLOCKING,
    assess_blockreason_quality,
    render_blockreason_quality,
)


def _reason(reason: str, hit: int, miss: int) -> dict:
    resolved = hit + miss
    return {
        "block_reason": reason,
        "hit": hit,
        "miss": miss,
        "resolved": resolved,
        "precision_pct": None if resolved == 0 else round(hit / resolved * 100.0, 2),
    }


def _blocked(rows: list[dict]) -> dict:
    return {"hit_miss_by_block_reason": rows}


def test_over_blocking_reason_flagged() -> None:
    # a rule whose suppressed alerts hit 70% is over-blocking
    r = assess_blockreason_quality(
        _blocked([_reason("low_directional_confidence", 35, 15)]),
        min_sample=20,
        over_block_threshold_pct=50.0,
    )
    row = r["by_block_reason"][0]
    assert row["block_reason"] == "low_directional_confidence"
    assert row["blocked_hit_rate_pct"] == 70.0
    assert row["verdict"] == VERDICT_OVER_BLOCKING
    assert r["over_blocking_count"] == 1
    assert "low_directional_confidence" in r["over_blocking_reasons"]
    assert r["influences_execution"] is False


def test_calibrated_reason_when_suppressed_mostly_miss() -> None:
    r = assess_blockreason_quality(
        _blocked([_reason("spam_filter", 5, 45)]),
        min_sample=20,  # 10%
    )
    assert r["by_block_reason"][0]["verdict"] == VERDICT_CALIBRATED
    assert r["over_blocking_count"] == 0


def test_insufficient_below_min_sample() -> None:
    r = assess_blockreason_quality(_blocked([_reason("rare_rule", 2, 1)]), min_sample=20)
    assert r["by_block_reason"][0]["verdict"] == VERDICT_INSUFFICIENT


def test_worst_first_ranking() -> None:
    r = assess_blockreason_quality(
        _blocked(
            [
                _reason("calibrated_rule", 5, 45),  # 10% calibrated
                _reason("mild_over", 28, 22),  # 56% over-blocking
                _reason("bad_rule", 45, 5),  # 90% over-blocking
                _reason("tiny", 1, 1),  # insufficient
            ]
        ),
        min_sample=20,
        over_block_threshold_pct=50.0,
    )
    order = [row["block_reason"] for row in r["by_block_reason"]]
    # over-blocking first (highest hit-rate), then calibrated, then insufficient
    assert order == ["bad_rule", "mild_over", "calibrated_rule", "tiny"]
    assert r["over_blocking_reasons"] == ["mild_over", "bad_rule"] or set(
        r["over_blocking_reasons"]
    ) == {"bad_rule", "mild_over"}


def test_render_lists_over_blocking() -> None:
    r = assess_blockreason_quality(_blocked([_reason("bad_rule", 45, 5)]), min_sample=20)
    text = render_blockreason_quality(r)
    assert "SUPPRESSION-QUALITY" in text
    assert "bad_rule" in text
    assert VERDICT_OVER_BLOCKING in text
