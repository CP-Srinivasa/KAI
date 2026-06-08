"""D-227 block-reason suppression-quality diagnostic (read-only).

Completes the over-blocking axis trio: the D-227-vs-hit_rate reconciliation
judged *overall* + *by_sentiment* (#202), the source cross-check judged
*by_source* (#205); this judges **by_block_reason** — i.e. *which gate rule* is
over-suppressing.

Signal: a block_reason whose *suppressed* alerts later hit often is an
over-blocking rule (it kills alerts that would have been right). A block_reason
whose suppressed alerts mostly miss is well-calibrated suppression. The reasons
are ranked worst-first so the operator sees the offending gate rule immediately.

Pure / IO-free: takes the built ``blocked_outcome_report`` dict and returns a
quality dict. No disk, no execution, no env, no runtime effect.
"""

from __future__ import annotations

from typing import Any

VERDICT_INSUFFICIENT = "INSUFFICIENT_DATA"
VERDICT_OVER_BLOCKING = "OVER_BLOCKING_REASON"
VERDICT_CALIBRATED = "CALIBRATED_REASON"

# Verdict sort order for worst-first ranking.
_ORDER = {VERDICT_OVER_BLOCKING: 0, VERDICT_CALIBRATED: 1, VERDICT_INSUFFICIENT: 2}


def _verdict(
    resolved: int,
    hit_rate_pct: float | None,
    *,
    min_sample: int,
    over_block_threshold_pct: float,
) -> str:
    if resolved < min_sample or hit_rate_pct is None:
        return VERDICT_INSUFFICIENT
    if hit_rate_pct >= over_block_threshold_pct:
        return VERDICT_OVER_BLOCKING
    return VERDICT_CALIBRATED


def assess_blockreason_quality(
    blocked_report: dict[str, Any],
    *,
    min_sample: int = 20,
    over_block_threshold_pct: float = 50.0,
) -> dict[str, Any]:
    """Rank block reasons by suppression quality.

    ``min_sample`` is the minimum resolved count for a reason before a verdict is
    rendered. ``over_block_threshold_pct`` is the blocked hit-rate at or above
    which a reason counts as over-blocking.
    """
    rows = blocked_report.get("hit_miss_by_block_reason") or []
    assessed: list[dict[str, Any]] = []
    over_blocking: list[str] = []
    for r in rows:
        reason = str(r.get("block_reason", "unknown"))
        resolved = int(r.get("resolved", 0))
        pct = r.get("precision_pct")
        pct = float(pct) if isinstance(pct, (int, float)) else None
        verdict = _verdict(
            resolved, pct, min_sample=min_sample, over_block_threshold_pct=over_block_threshold_pct
        )
        if verdict == VERDICT_OVER_BLOCKING:
            over_blocking.append(reason)
        assessed.append(
            {
                "block_reason": reason,
                "resolved": resolved,
                "blocked_hit_rate_pct": pct,
                "verdict": verdict,
            }
        )

    # Worst-first: over-blocking (highest hit-rate first), then calibrated, then
    # insufficient. ``-1`` floors a None hit-rate so it sorts last within a group.
    assessed.sort(key=lambda a: (_ORDER[a["verdict"]], -(a["blocked_hit_rate_pct"] or -1.0)))

    return {
        "report_type": "d227_blockreason_quality",
        "min_sample": min_sample,
        "over_block_threshold_pct": over_block_threshold_pct,
        "over_blocking_reasons": over_blocking,
        "over_blocking_count": len(over_blocking),
        "by_block_reason": assessed,
        # Read-only diagnostic — never gates execution.
        "influences_execution": False,
    }


def render_blockreason_quality(report: dict[str, Any]) -> str:
    """Compact operator render (worst-first)."""
    lines = [
        "D-227 BLOCK-REASON SUPPRESSION-QUALITY",
        f"min_sample={report['min_sample']} over_block_pct={report['over_block_threshold_pct']}",
        f"over_blocking_reasons={report['over_blocking_count']} {report['over_blocking_reasons']}",
        "",
        "BY BLOCK REASON (worst first)",
    ]
    rows = report.get("by_block_reason", [])
    if not rows:
        lines.append("  (none)")
    for r in rows:
        lines.append(
            f"  {r['block_reason']}: blocked={r['blocked_hit_rate_pct']}%"
            f"({r['resolved']}) verdict={r['verdict']}"
        )
    return "\n".join(lines).rstrip()


__all__ = [
    "VERDICT_CALIBRATED",
    "VERDICT_INSUFFICIENT",
    "VERDICT_OVER_BLOCKING",
    "assess_blockreason_quality",
    "render_blockreason_quality",
]
