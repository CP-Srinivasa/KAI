"""The five precision numbers that stood side by side without a population.

STAB-2026-09-01 §29. Extracted from ``app/api/routers/dashboard.py`` under the
god-file ratchet; this is declarative metric metadata, not request handling.

Measured on the live ``/dashboard/api/quality`` payload:

    directional_precision_all_time   73.49 %   over 166 resolved
    forward_precision                72.41 %   over 116 forward-resolved (84 hits)
    active_precision                 72.84 %   over the active split
    high_priority_hit_rate           73.49 %   over the P10 tier
    annotation_volume                19890     annotation ROWS, not a rate

They are NOT the same measurement and were never meant to agree; nothing on the
surface said so. Each entry below names its population and carries its own
numerator and denominator, so a reader can see WHY two of them differ — and can
see that two of them coincide exactly, which is itself a finding.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["classify_priority_tier_lift", "precision_metric_contracts"]


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def classify_priority_tier_lift(quality: dict[str, Any]) -> dict[str, Any]:
    """Judge the P10-vs-P7-P9 lift against its Wilson intervals, not its sign.

    2026-09-09: the surface called a negative lift ``critical`` and told the
    operator that high priority performs "AKTIV SCHLECHTER". On that day the
    tiers measured 69,64 % (n=112, CI 60,59-77,39) against 77,78 % (n=54, CI
    65,06-86,80) — a lift of -8,14 pp whose intervals overlap over their whole
    width. The difference was noise, and the intervals proving it were already
    computed in ``hold_metrics`` and sitting unused in the same dict.

    A tier difference counts only when the two Wilson intervals are disjoint —
    the same bar the per-source precision table already applies. Without
    intervals nothing is claimed: an unmeasurable difference is not a finding.

    The lift itself is always passed through. This narrows what may be
    *asserted*, it does not hide the number.
    """
    lift = _num(quality.get("priority_tier_lift_pct"))
    high_n = quality.get("priority_tier_high_conviction_resolved")
    standard_n = quality.get("priority_tier_standard_resolved")

    # Die beiden Gruppen, deren Differenz der Lift IST. Direktive 2026-08-08
    # (kein Aggregat ohne Zerlegung): -8,14 pp ist ohne die zugrundeliegenden
    # Raten, Stichprobengroessen und Intervalle nicht beurteilbar — genau daran
    # ist die alte Vorzeichen-Regel gescheitert.
    by_tier: dict[str, Any] = {
        "high_conviction": {
            "hit_rate_pct": _num(quality.get("priority_tier_high_conviction_hit_rate_pct")),
            "resolved": high_n,
            "ci_low_pct": _num(quality.get("priority_tier_high_conviction_ci_low_pct")),
            "ci_high_pct": _num(quality.get("priority_tier_high_conviction_ci_high_pct")),
        },
        "standard": {
            "hit_rate_pct": _num(quality.get("priority_tier_standard_hit_rate_pct")),
            "resolved": standard_n,
            "ci_low_pct": _num(quality.get("priority_tier_standard_ci_low_pct")),
            "ci_high_pct": _num(quality.get("priority_tier_standard_ci_high_pct")),
        },
    }

    if lift is None:
        return {
            "verdict": "insufficient_data",
            "quality_status": "warning",
            "significant": False,
            "lift_pct": None,
            "high_priority_resolved": high_n,
            "standard_resolved": standard_n,
            "by_tier": by_tier,
            "confidence_interval": None,
            "explanation": (
                "No resolved directional alerts in both tiers — the lift is not computable."
            ),
        }

    high_lo = _num(quality.get("priority_tier_high_conviction_ci_low_pct"))
    high_hi = _num(quality.get("priority_tier_high_conviction_ci_high_pct"))
    std_lo = _num(quality.get("priority_tier_standard_ci_low_pct"))
    std_hi = _num(quality.get("priority_tier_standard_ci_high_pct"))
    # Einzeln geprueft statt ``None not in (...)``: der Tuple-Test schmaelert die
    # Typen nicht, und ein Vergleich gegen None waere hier ein Laufzeitfehler.
    if high_lo is None or high_hi is None or std_lo is None or std_hi is None:
        have_ci = False
        significant = False
    else:
        have_ci = True
        # Disjunkte 95-%-Wilson-Intervalle, in beide Richtungen.
        significant = high_hi < std_lo or high_lo > std_hi

    if not significant:
        explanation = (
            f"High-P {high_lo:.1f}-{high_hi:.1f} % overlaps standard {std_lo:.1f}-{std_hi:.1f} % "
            "(95 % Wilson) — the tiers are not distinguishable at this sample size."
            if have_ci
            else "No confidence intervals available — the difference cannot be qualified."
        )
        return {
            "verdict": "priority_inconclusive",
            "quality_status": "warning",
            "significant": False,
            "lift_pct": lift,
            "high_priority_resolved": high_n,
            "standard_resolved": standard_n,
            "by_tier": by_tier,
            "confidence_interval": [high_lo, high_hi] if have_ci else None,
            "explanation": explanation,
            "warning": (
                "High priority is not a validated quality label — but it is not proven worse "
                "either. Do not act on the sign of this number alone."
            ),
        }

    underperforming = lift < 0
    return {
        "verdict": "priority_underperforming" if underperforming else "priority_validated",
        "quality_status": "critical" if underperforming else "ok",
        "significant": True,
        "lift_pct": lift,
        "high_priority_resolved": high_n,
        "standard_resolved": standard_n,
        "by_tier": by_tier,
        "confidence_interval": [high_lo, high_hi],
        "explanation": (
            f"High-P {high_lo:.1f}-{high_hi:.1f} % and standard {std_lo:.1f}-{std_hi:.1f} % "
            "are disjoint at 95 % Wilson — the difference is carried by the evidence."
        ),
        "warning": (
            "High priority resolves WORSE than standard priority and the intervals are disjoint. "
            "Investigate the inverted ranking; do not present high-P as a quality label."
            if underperforming
            else None
        ),
    }


def precision_metric_contracts(
    *,
    quality: dict[str, Any],
    generated_at: str,
    outcomes_artifact: Path,
    contract: Callable[..., dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Contracts for the precision family, each with an explicit population."""
    return {
        # ------------------------------------------------------------------
        # STAB-2026-09-01 §29 — the five numbers that stood side by side with no
        # stated population. They are NOT the same measurement and were never
        # meant to agree; nothing on the surface said so.
        #
        #   directional_precision_all_time   73.49 %   over 166 resolved
        #   forward_precision                72.41 %   over 116 forward-resolved (84 hits)
        #   active_precision                 72.84 %   over the active split
        #   high_priority_hit_rate           73.49 %   over the P10 tier
        #   annotation_volume                19890     annotation rows, not a rate
        #
        # Each now carries population_id + numerator + denominator, so a reader can
        # see WHY two of them differ — and can see that two of them coincide.
        # ------------------------------------------------------------------
        "directional_precision_pct": contract(
            value=quality.get("precision_pct"),
            unit="percent",
            semantic_type="directional_precision",
            scope="all_time_resolved",
            generated_at=generated_at,
            source_artifact=outcomes_artifact,
            population_id="directional_alerts_resolved_all_time",
            numerator=quality.get("hits"),
            denominator=quality.get("resolved_count"),
            sample_size=quality.get("resolved_count"),
            is_decision_relevant=True,
            quality_status="ok",
            explanation=(
                "Hits over hit+miss across ALL resolved directional alerts. Inconclusive "
                "rows are excluded from the denominator, which is why this is not the "
                "annotation count."
            ),
        ),
        "forward_precision_pct": contract(
            value=quality.get("forward_precision_pct"),
            unit="percent",
            semantic_type="directional_precision",
            scope="forward_window",
            generated_at=generated_at,
            source_artifact=outcomes_artifact,
            population_id="directional_alerts_resolved_forward_window",
            numerator=quality.get("forward_hits"),
            denominator=quality.get("forward_resolved"),
            sample_size=quality.get("forward_resolved"),
            is_decision_relevant=True,
            quality_status="ok",
            explanation=(
                "A STRICT SUBSET of the all-time population: only alerts resolved after "
                "the forward-evaluation cutoff. A different denominator here is expected, "
                "not a discrepancy."
            ),
        ),
        "active_precision_pct": contract(
            value=quality.get("active_precision_pct"),
            unit="percent",
            semantic_type="directional_precision",
            scope="active_split",
            generated_at=generated_at,
            source_artifact=outcomes_artifact,
            population_id="directional_alerts_active_split",
            numerator=quality.get("active_hits"),
            denominator=quality.get("active_resolved"),
            sample_size=quality.get("active_resolved"),
            is_decision_relevant=True,
            quality_status="ok",
            explanation="Resolved alerts from currently active sources only.",
        ),
        "high_priority_hit_rate_pct": contract(
            value=quality.get("high_priority_hit_rate_pct"),
            unit="percent",
            semantic_type="directional_precision",
            scope="high_priority_tier",
            generated_at=generated_at,
            source_artifact=outcomes_artifact,
            population_id="directional_alerts_high_priority_tier",
            numerator=quality.get("high_priority_hits"),
            denominator=quality.get("high_priority_resolved"),
            sample_size=quality.get("high_priority_resolved"),
            is_decision_relevant=True,
            quality_status="ok",
            explanation=(
                "The high-priority tier's own hit rate. When it equals the all-time "
                "precision exactly, the tier is not separating anything — read it "
                "together with priority_tier_lift_pct rather than on its own."
            ),
        ),
        "annotation_rows_total": contract(
            value=quality.get("annotations_total"),
            unit="count",
            semantic_type="annotation_volume",
            scope="all_time",
            generated_at=generated_at,
            source_artifact=outcomes_artifact,
            population_id="alert_outcome_annotation_rows",
            numerator=quality.get("annotations_total"),
            denominator=quality.get("annotations_total"),
            sample_size=quality.get("annotations_total"),
            is_decision_relevant=False,
            quality_status="ok",
            explanation=(
                "COUNT OF ROWS, not a rate and not a denominator for any precision "
                "above: most annotations are inconclusive and never enter a hit/miss "
                "denominator. Citing it beside a precision invites exactly that "
                "confusion."
            ),
        ),
    }
