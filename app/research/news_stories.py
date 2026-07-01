"""Story clustering — the cluster-robust answer to cross-source news duplication.

The IVW source pool treats sources as independent, but crypto news is syndicated:
cointelegraph, coindesk and cryptobriefing cover the SAME ETF story within hours,
and two events on the same symbol two hours apart share 22h of the same 1d
forward-return window. Pooling such outcomes multiple times overstates the
effective sample — a low I² can then be a SYMPTOM (everyone pools the same
events), not a quality seal.

The fix implemented here: cluster outcomes into STORIES — same symbol, same
declared direction, published within a window of the story's FIRST event,
ACROSS sources — and let one story contribute exactly one observation. The
representative is the EARLIEST event (the first no-look-ahead moment the story
was knowable), so the story-level series stays causally clean and time-ordered
for the moving-block bootstrap.

Pure and unit-tested; consumed by :mod:`app.research.news_signal_eval` as the
``stories`` (headline) cohort.
"""

from __future__ import annotations

from typing import Any

# A follow-up article within a day is coverage of the same story, not new
# information — matches the longest measured horizon band's granularity.
DEFAULT_STORY_WINDOW_S = 86_400.0


def cluster_stories(
    outcomes: list[dict[str, Any]],
    *,
    window_s: float = DEFAULT_STORY_WINDOW_S,
) -> list[dict[str, Any]]:
    """Collapse a time-ordered outcome pool to one observation per story.

    Story identity: (symbol, side); an event joins the current story of its
    (symbol, side) lane when it is within ``window_s`` of that story's FIRST
    event (anchored windows — no unbounded chaining), else it OPENS a new story.
    The story's outcome is its first event's outcome, annotated with
    ``story_n_members`` and the contributing ``story_sources`` so dedup pressure
    stays visible. Output is time-ordered.
    """
    open_story: dict[tuple[str, str], dict[str, Any]] = {}
    stories: list[dict[str, Any]] = []
    for o in sorted(outcomes, key=lambda x: x["entry_ts"]):
        key = (str(o["symbol"]), str(o["side"]))
        cur = open_story.get(key)
        if cur is not None and ((o["entry_ts"] - cur["entry_ts"]).total_seconds() <= window_s):
            cur["story_n_members"] += 1
            src = str(o.get("source", "unknown"))
            if src not in cur["story_sources"]:
                cur["story_sources"].append(src)
            continue
        rep = dict(o)
        rep["story_n_members"] = 1
        rep["story_sources"] = [str(o.get("source", "unknown"))]
        open_story[key] = rep
        stories.append(rep)
    return stories


def dedup_stats(raw: list[dict[str, Any]], stories: list[dict[str, Any]]) -> dict[str, Any]:
    """How much duplication the clustering removed (kept visible, never silent)."""
    n_raw = len(raw)
    n_stories = len(stories)
    multi = [s for s in stories if s["story_n_members"] > 1]
    return {
        "n_raw": n_raw,
        "n_stories": n_stories,
        "dedup_ratio": round(1.0 - (n_stories / n_raw), 3) if n_raw else 0.0,
        "n_multi_member_stories": len(multi),
        "max_story_members": max((s["story_n_members"] for s in stories), default=0),
    }


__all__ = ["DEFAULT_STORY_WINDOW_S", "cluster_stories", "dedup_stats"]
