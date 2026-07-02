"""D-227 blocked-outcome vs dispatched hit-rate reconciliation (read-only).

Cross-checks two independent outcome streams for the same alert population:

- **D-227 blocked stream** (``blocked_outcome_report``): alerts the system
  *suppressed* (blocked), with their later hit/miss outcome.
- **Dispatched hit-rate** (``hit_rate.HitRateReport``): alerts the system
  *dispatched*, with their hit/miss outcome.

Why reconcile: if the *blocked* alerts hit about as often as (or more than) the
*dispatched* alerts, the block gate is **over-suppressing good alerts** — a recall
problem the D-227 proxy exists to surface. If the blocked hit-rate is clearly
below the dispatched hit-rate, suppression is well-calibrated. A divergence that
cannot be judged on sample size stays **INSUFFICIENT_DATA**, never a verdict.

Pure / IO-free: takes the two already-built report dicts and returns a
reconciliation dict. No disk, no execution, no env, no runtime effect.
"""

from __future__ import annotations

from typing import Any

VERDICT_INSUFFICIENT = "INSUFFICIENT_DATA"
VERDICT_OVER_BLOCKING = "OVER_BLOCKING_SUSPECT"
VERDICT_CALIBRATED = "SUPPRESSION_CALIBRATED"


def _pct(hits: int, resolved: int) -> float | None:
    return None if resolved <= 0 else round(hits / resolved * 100.0, 2)


def _blocked_overall(blocked_report: dict[str, Any]) -> tuple[int, int, float | None]:
    """Aggregate (resolved, hits, hit_rate_pct) across the blocked stream.

    Every ``hit_miss_by_*`` axis partitions the same latest-by-doc rows, so any
    one axis sums to the overall. Uses block_reason as the canonical axis.
    """
    rows = blocked_report.get("hit_miss_by_block_reason") or []
    hits = sum(int(r.get("hit", 0)) for r in rows)
    resolved = sum(int(r.get("resolved", 0)) for r in rows)
    return resolved, hits, _pct(hits, resolved)


def _blocked_by_sentiment(
    blocked_report: dict[str, Any],
) -> dict[str, tuple[int, int, float | None]]:
    out: dict[str, tuple[int, int, float | None]] = {}
    for r in blocked_report.get("hit_miss_by_sentiment") or []:
        label = str(r.get("sentiment", "unknown"))
        hits = int(r.get("hit", 0))
        resolved = int(r.get("resolved", 0))
        out[label] = (resolved, hits, _pct(hits, resolved))
    return out


def _dispatched_by_sentiment(
    hitrate_report: dict[str, Any],
) -> dict[str, tuple[int, int, float | None]]:
    out: dict[str, tuple[int, int, float | None]] = {}
    for label, bd in (hitrate_report.get("by_sentiment") or {}).items():
        resolved = int(bd.get("resolved", 0))
        hits = int(bd.get("hits", 0))
        out[str(label)] = (resolved, hits, _pct(hits, resolved))
    return out


def _verdict(
    blocked_resolved: int,
    blocked_pct: float | None,
    dispatched_resolved: int,
    dispatched_pct: float | None,
    *,
    min_sample: int,
    tolerance_pct: float,
) -> str:
    if blocked_resolved < min_sample or dispatched_resolved < min_sample:
        return VERDICT_INSUFFICIENT
    if blocked_pct is None or dispatched_pct is None:
        return VERDICT_INSUFFICIENT
    # blocked alerts hit ~as often as (or more than) dispatched → over-suppression
    if blocked_pct >= dispatched_pct - tolerance_pct:
        return VERDICT_OVER_BLOCKING
    return VERDICT_CALIBRATED


def reconcile_d227_vs_hitrate(
    blocked_report: dict[str, Any],
    hitrate_report: dict[str, Any],
    *,
    min_sample: int = 20,
    tolerance_pct: float = 5.0,
) -> dict[str, Any]:
    """Reconcile the blocked-outcome stream against the dispatched hit-rate.

    ``min_sample`` is the minimum resolved count on *each* side before a verdict
    is rendered (else INSUFFICIENT_DATA). ``tolerance_pct`` is how close the
    blocked hit-rate may come to the dispatched one before it reads as
    over-blocking.
    """
    b_resolved, b_hits, b_pct = _blocked_overall(blocked_report)
    d_resolved = int(hitrate_report.get("resolved_count", 0))
    d_hits = int(hitrate_report.get("hit_count", 0))
    d_pct = hitrate_report.get("hit_rate_pct")
    d_pct = float(d_pct) if isinstance(d_pct, (int, float)) else _pct(d_hits, d_resolved)

    overall_delta = round(d_pct - b_pct, 2) if (b_pct is not None and d_pct is not None) else None
    overall_verdict = _verdict(
        b_resolved, b_pct, d_resolved, d_pct, min_sample=min_sample, tolerance_pct=tolerance_pct
    )

    # Per shared sentiment bucket.
    b_sent = _blocked_by_sentiment(blocked_report)
    d_sent = _dispatched_by_sentiment(hitrate_report)
    by_sentiment: dict[str, Any] = {}
    for label in sorted(set(b_sent) | set(d_sent)):
        br, bh, bp = b_sent.get(label, (0, 0, None))
        dr, dh, dp = d_sent.get(label, (0, 0, None))
        by_sentiment[label] = {
            "blocked_resolved": br,
            "blocked_hit_rate_pct": bp,
            "dispatched_resolved": dr,
            "dispatched_hit_rate_pct": dp,
            "delta_pct": (round(dp - bp, 2) if (bp is not None and dp is not None) else None),
            "verdict": _verdict(br, bp, dr, dp, min_sample=min_sample, tolerance_pct=tolerance_pct),
        }

    return {
        "report_type": "d227_hitrate_reconciliation",
        "min_sample": min_sample,
        "tolerance_pct": tolerance_pct,
        "overall": {
            "blocked_resolved": b_resolved,
            "blocked_hit_rate_pct": b_pct,
            "dispatched_resolved": d_resolved,
            "dispatched_hit_rate_pct": d_pct,
            "delta_pct": overall_delta,
            "verdict": overall_verdict,
        },
        "by_sentiment": by_sentiment,
        # Read-only diagnostic — never gates execution.
        "influences_execution": False,
    }


def render_reconciliation(report: dict[str, Any]) -> str:
    """Compact operator render of the reconciliation."""
    o = report["overall"]
    lines = [
        "D-227 vs HIT_RATE RECONCILIATION",
        f"min_sample={report['min_sample']} tolerance_pct={report['tolerance_pct']}",
        "",
        "OVERALL",
        f"  blocked:    resolved={o['blocked_resolved']} rate={o['blocked_hit_rate_pct']}",
        f"  dispatched: resolved={o['dispatched_resolved']} rate={o['dispatched_hit_rate_pct']}",
        f"  delta_pct={o['delta_pct']}  verdict={o['verdict']}",
        "",
        "BY SENTIMENT",
    ]
    by_sent = report.get("by_sentiment", {})
    if not by_sent:
        lines.append("  (none)")
    for label, s in by_sent.items():
        lines.append(
            f"  {label}: blocked={s['blocked_hit_rate_pct']}%({s['blocked_resolved']}) "
            f"dispatched={s['dispatched_hit_rate_pct']}%({s['dispatched_resolved']}) "
            f"delta={s['delta_pct']} verdict={s['verdict']}"
        )
    return "\n".join(lines).rstrip()


__all__ = [
    "VERDICT_CALIBRATED",
    "VERDICT_INSUFFICIENT",
    "VERDICT_OVER_BLOCKING",
    "reconcile_d227_vs_hitrate",
    "render_reconciliation",
]
