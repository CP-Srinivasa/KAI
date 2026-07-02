"""Unit tests for story clustering (cross-source dedup of syndicated news)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.research.news_stories import cluster_stories, dedup_stats

_T0 = datetime(2026, 6, 1, tzinfo=UTC)


def _o(
    hours: float, *, symbol: str = "ETH/USDT", side: str = "long", source: str = "a"
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "side": side,
        "source": source,
        "entry_ts": _T0 + timedelta(hours=hours),
        "fwd": {86400: 10.0},
    }


def test_same_story_across_sources_collapses_to_first_event() -> None:
    raw = [_o(0, source="cointelegraph"), _o(2, source="coindesk"), _o(5, source="cryptobriefing")]
    stories = cluster_stories(raw)
    assert len(stories) == 1
    s = stories[0]
    assert s["source"] == "cointelegraph"  # earliest event represents the story
    assert s["story_n_members"] == 3
    assert s["story_sources"] == ["cointelegraph", "coindesk", "cryptobriefing"]


def test_window_is_anchored_at_first_event_no_chaining() -> None:
    # 0h, 20h join (within 24h of anchor); 30h is outside the ANCHOR window and
    # opens a new story even though it is within 24h of the 20h event.
    stories = cluster_stories([_o(0), _o(20), _o(30)])
    assert [s["story_n_members"] for s in stories] == [2, 1]


def test_opposite_sides_are_distinct_stories() -> None:
    stories = cluster_stories([_o(0, side="long"), _o(1, side="short")])
    assert len(stories) == 2


def test_different_symbols_are_distinct_stories() -> None:
    stories = cluster_stories([_o(0, symbol="ETH/USDT"), _o(0.5, symbol="SOL/USDT")])
    assert len(stories) == 2


def test_output_time_ordered_and_unsorted_input_tolerated() -> None:
    stories = cluster_stories([_o(30), _o(0), _o(2)])
    assert [s["entry_ts"] for s in stories] == sorted(s["entry_ts"] for s in stories)
    assert [s["story_n_members"] for s in stories] == [2, 1]


def test_dedup_stats_shape() -> None:
    raw = [_o(0), _o(1), _o(2), _o(40)]
    stories = cluster_stories(raw)
    st = dedup_stats(raw, stories)
    assert st["n_raw"] == 4
    assert st["n_stories"] == 2
    assert st["dedup_ratio"] == 0.5
    assert st["n_multi_member_stories"] == 1
    assert st["max_story_members"] == 3
    assert dedup_stats([], [])["dedup_ratio"] == 0.0
