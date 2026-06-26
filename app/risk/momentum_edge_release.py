"""momentum_edge_release — G5: cohort edge → release recommendation (gated).

Measures the ``momentum_universe`` cohort's cost-netto edge from the paper audit
(reusing the edge_report cohort aggregation — same cost SSOT) and maps it to an
``EntryMode`` recommendation via ``edge_release_policy.decide_release``. Honest +
gated by construction:

* no cohort closes yet → ``available=False`` (nothing to judge).
* below ``min_n`` or no defensible posterior → DISABLED.
* a LIVE recommendation ALWAYS carries ``requires_operator_signoff=True`` — this
  module only RECOMMENDS; it never flips ``entry_mode`` and never touches capital.

OOS stability is assessed over the cohort's OWN disjoint day sub-cohorts (the
report is built on the filtered cohort trades), so it is cohort-specific, not
polluted by other sources.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_COHORT = "momentum_universe"
_DEFAULT_MIN_N = 30  # Goal gate: a defensible momentum_universe edge needs n >= 30.


def build_momentum_release(
    audit_path: str | Path,
    *,
    cohort: str = _COHORT,
    min_n: int = _DEFAULT_MIN_N,
    safety_margin_bps: float = 0.0,
) -> dict[str, Any]:
    """Build the cohort edge-release verdict dict. Read-only; never raises on no data."""
    from app.observability.edge_report import (
        build_edge_report,
        load_audit_events,
        parse_closed_trades_with_exclusions,
    )
    from app.risk.edge_release_policy import assess_oos_stability, decide_release

    events = load_audit_events(audit_path)
    closed, _excluded = parse_closed_trades_with_exclusions(events)
    cohort_trades = [t for t in closed if t.signal_source == cohort]
    if not cohort_trades:
        return {
            "available": False,
            "cohort": cohort,
            "resolved": 0,
            "reason": "no_cohort_closes",
        }

    report = build_edge_report(cohort_trades, venue="paper", safety_margin_bps=safety_margin_bps)
    edge = next((c for c in report.by_source if c.cohort_key == cohort), report.overall)
    oos_stable, oos_breakdown = assess_oos_stability(
        report.by_day, safety_margin_bps=safety_margin_bps
    )
    decision = decide_release(
        edge,
        min_n=min_n,
        safety_margin_bps=safety_margin_bps,
        oos_stable=oos_stable,
        oos_breakdown=oos_breakdown,
    )
    return {
        "available": True,
        "cohort": cohort,
        "resolved": len(cohort_trades),
        **decision.to_dict(),
    }


__all__ = ["build_momentum_release"]
