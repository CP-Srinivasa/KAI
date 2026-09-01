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

__all__ = ["precision_metric_contracts"]


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
