"""D-227 blocked-outcome vs source-reliability per-source cross-check (read-only).

Cross-checks the per-source hit-rate of the **blocked** stream (alerts the system
suppressed — ``blocked_outcome_report.hit_miss_by_source``) against each source's
**dispatched** reliability tier/point-estimate (FS-3 ``source_reliability``).

Why: my D-227-vs-hit_rate reconciliation could only compare overall + by_sentiment
(the dispatched hit-rate report carries no per-source axis). Source-reliability
*does* carry a per-source dispatched recall + tier, so this closes the per-source
gap. The load-bearing signal is **over-blocking a good source**: if a
``trusted``/``neutral`` source's *blocked* alerts hit often, the block gate is
suppressing alerts the system otherwise trusts.

Pure / IO-free: takes the two already-built report dicts and returns a
cross-check dict. No disk, no execution, no env, no runtime effect.
"""

from __future__ import annotations

from typing import Any

VERDICT_INSUFFICIENT = "INSUFFICIENT_DATA"
VERDICT_UNRATED = "SOURCE_UNRATED"
VERDICT_OVER_BLOCKED_GOOD = "OVER_BLOCKED_GOOD_SOURCE"
VERDICT_CALIBRATED = "SUPPRESSION_CALIBRATED"

# Tiers the system otherwise trusts — suppressing their hitting alerts is the
# over-blocking signal this check exists to surface.
_GOOD_TIERS = frozenset({"trusted", "neutral"})


def _pct(hits: int, resolved: int) -> float | None:
    return None if resolved <= 0 else round(hits / resolved * 100.0, 2)


def _blocked_by_source(blocked_report: dict[str, Any]) -> dict[str, tuple[int, int, float | None]]:
    out: dict[str, tuple[int, int, float | None]] = {}
    for r in blocked_report.get("hit_miss_by_source") or []:
        source = str(r.get("source", "unknown"))
        hits = int(r.get("hit", 0))
        resolved = int(r.get("resolved", 0))
        out[source] = (resolved, hits, _pct(hits, resolved))
    return out


def _source_verdict(
    blocked_resolved: int,
    blocked_pct: float | None,
    tier: str | None,
    *,
    min_sample: int,
    over_block_threshold_pct: float,
) -> str:
    if blocked_resolved < min_sample or blocked_pct is None:
        return VERDICT_INSUFFICIENT
    if tier is None or tier == "insufficient":
        return VERDICT_UNRATED
    if tier in _GOOD_TIERS and blocked_pct >= over_block_threshold_pct:
        return VERDICT_OVER_BLOCKED_GOOD
    return VERDICT_CALIBRATED


def crosscheck_blocked_vs_reliability(
    blocked_report: dict[str, Any],
    reliability_report: dict[str, Any],
    *,
    min_sample: int = 20,
    over_block_threshold_pct: float = 50.0,
) -> dict[str, Any]:
    """Per-source cross-check of the blocked stream against source reliability.

    ``min_sample`` is the minimum *blocked-resolved* count for a source before a
    verdict is rendered. ``over_block_threshold_pct`` is the blocked hit-rate at
    or above which a good-tier source counts as over-blocked.
    """
    blocked = _blocked_by_source(blocked_report)
    scores = reliability_report.get("scores") or {}

    by_source: dict[str, Any] = {}
    over_blocked_good: list[str] = []
    for source in sorted(set(blocked) | set(scores)):
        b_resolved, b_hits, b_pct = blocked.get(source, (0, 0, None))
        score = scores.get(source) or {}
        tier = score.get("tier") if isinstance(score, dict) else None
        dispatched_pct = score.get("point_estimate") if isinstance(score, dict) else None
        # point_estimate is a fraction [0,1]; surface as percent for symmetry.
        dispatched_pct_pct = (
            round(float(dispatched_pct) * 100.0, 2)
            if isinstance(dispatched_pct, (int, float))
            else None
        )
        verdict = _source_verdict(
            b_resolved,
            b_pct,
            tier if isinstance(tier, str) else None,
            min_sample=min_sample,
            over_block_threshold_pct=over_block_threshold_pct,
        )
        if verdict == VERDICT_OVER_BLOCKED_GOOD:
            over_blocked_good.append(source)
        by_source[source] = {
            "blocked_resolved": b_resolved,
            "blocked_hit_rate_pct": b_pct,
            "dispatched_tier": tier if isinstance(tier, str) else None,
            "dispatched_hit_rate_pct": dispatched_pct_pct,
            "dispatched_n": int(score.get("n", 0)) if isinstance(score, dict) else 0,
            "verdict": verdict,
        }

    return {
        "report_type": "d227_source_reliability_crosscheck",
        "min_sample": min_sample,
        "over_block_threshold_pct": over_block_threshold_pct,
        "over_blocked_good_sources": over_blocked_good,
        "over_blocked_good_count": len(over_blocked_good),
        "by_source": by_source,
        # Read-only diagnostic — never gates execution.
        "influences_execution": False,
    }


def render_crosscheck(report: dict[str, Any]) -> str:
    """Compact operator render of the cross-check."""
    lines = [
        "D-227 BLOCKED vs SOURCE-RELIABILITY CROSS-CHECK",
        f"min_sample={report['min_sample']} over_block_pct={report['over_block_threshold_pct']}",
        f"over_blocked_good_sources={report['over_blocked_good_count']} "
        f"{report['over_blocked_good_sources']}",
        "",
        "BY SOURCE",
    ]
    by_source = report.get("by_source", {})
    if not by_source:
        lines.append("  (none)")
    for source, s in by_source.items():
        lines.append(
            f"  {source}: blocked={s['blocked_hit_rate_pct']}%({s['blocked_resolved']}) "
            f"tier={s['dispatched_tier']} dispatched={s['dispatched_hit_rate_pct']}%"
            f"({s['dispatched_n']}) verdict={s['verdict']}"
        )
    return "\n".join(lines).rstrip()


__all__ = [
    "VERDICT_CALIBRATED",
    "VERDICT_INSUFFICIENT",
    "VERDICT_OVER_BLOCKED_GOOD",
    "VERDICT_UNRATED",
    "crosscheck_blocked_vs_reliability",
    "render_crosscheck",
]
