"""D-227 blocked vs source-reliability per-source cross-check (read-only)."""

from __future__ import annotations

from app.alerts.d227_source_reliability_crosscheck import (
    VERDICT_CALIBRATED,
    VERDICT_INSUFFICIENT,
    VERDICT_OVER_BLOCKED_GOOD,
    VERDICT_UNRATED,
    crosscheck_blocked_vs_reliability,
    render_crosscheck,
)


def _blocked(source_rows: list[dict]) -> dict:
    return {"hit_miss_by_source": source_rows}


def _reliability(scores: dict[str, dict]) -> dict:
    return {"scores": scores}


def _row(source: str, hit: int, miss: int) -> dict:
    return {"source": source, "hit": hit, "miss": miss, "resolved": hit + miss}


def _score(tier: str, point_estimate: float | None, n: int) -> dict:
    return {"tier": tier, "point_estimate": point_estimate, "n": n}


def test_over_blocked_good_source_flagged() -> None:
    # trusted source whose BLOCKED alerts hit 70% -> over-blocking a good source
    r = crosscheck_blocked_vs_reliability(
        _blocked([_row("cryptobriefing", 35, 15)]),
        _reliability({"cryptobriefing": _score("trusted", 0.72, 200)}),
        min_sample=20,
        over_block_threshold_pct=50.0,
    )
    s = r["by_source"]["cryptobriefing"]
    assert s["blocked_hit_rate_pct"] == 70.0
    assert s["dispatched_tier"] == "trusted"
    assert s["dispatched_hit_rate_pct"] == 72.0
    assert s["verdict"] == VERDICT_OVER_BLOCKED_GOOD
    assert r["over_blocked_good_count"] == 1
    assert "cryptobriefing" in r["over_blocked_good_sources"]
    assert r["influences_execution"] is False


def test_calibrated_when_low_tier_blocked() -> None:
    # low-tier source whose blocked alerts also miss -> suppression calibrated
    r = crosscheck_blocked_vs_reliability(
        _blocked([_row("spamfeed", 5, 45)]),
        _reliability({"spamfeed": _score("low", 0.10, 120)}),
        min_sample=20,
    )
    assert r["by_source"]["spamfeed"]["verdict"] == VERDICT_CALIBRATED
    assert r["over_blocked_good_count"] == 0


def test_good_tier_but_blocked_misses_is_calibrated() -> None:
    # neutral source whose blocked alerts mostly miss (below threshold) -> ok
    r = crosscheck_blocked_vs_reliability(
        _blocked([_row("midfeed", 10, 40)]),  # 20%
        _reliability({"midfeed": _score("neutral", 0.5, 80)}),
        min_sample=20,
        over_block_threshold_pct=50.0,
    )
    assert r["by_source"]["midfeed"]["verdict"] == VERDICT_CALIBRATED


def test_insufficient_below_min_sample() -> None:
    r = crosscheck_blocked_vs_reliability(
        _blocked([_row("tiny", 2, 1)]),
        _reliability({"tiny": _score("trusted", 0.9, 100)}),
        min_sample=20,
    )
    assert r["by_source"]["tiny"]["verdict"] == VERDICT_INSUFFICIENT


def test_unrated_source_when_no_reliability_score() -> None:
    r = crosscheck_blocked_vs_reliability(
        _blocked([_row("ghostfeed", 30, 20)]),
        _reliability({}),  # no dispatched reliability for this source
        min_sample=20,
    )
    s = r["by_source"]["ghostfeed"]
    assert s["dispatched_tier"] is None
    assert s["verdict"] == VERDICT_UNRATED


def test_insufficient_tier_is_unrated() -> None:
    r = crosscheck_blocked_vs_reliability(
        _blocked([_row("newfeed", 30, 20)]),
        _reliability({"newfeed": _score("insufficient", None, 3)}),
        min_sample=20,
    )
    assert r["by_source"]["newfeed"]["verdict"] == VERDICT_UNRATED


def test_render_lists_over_blocked() -> None:
    r = crosscheck_blocked_vs_reliability(
        _blocked([_row("cryptobriefing", 35, 15)]),
        _reliability({"cryptobriefing": _score("trusted", 0.72, 200)}),
        min_sample=20,
    )
    text = render_crosscheck(r)
    assert "CROSS-CHECK" in text
    assert "cryptobriefing" in text
    assert VERDICT_OVER_BLOCKED_GOOD in text
