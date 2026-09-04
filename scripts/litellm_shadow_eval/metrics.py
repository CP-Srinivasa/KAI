"""Deterministic route metrics without external statistics dependencies."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from statistics import fmean, median

from scripts.litellm_shadow_eval.models import (
    EvidencePair,
    EvidenceRecord,
    PairStatus,
    QualityComparison,
    RouteMetrics,
    ValidationIssue,
)


def _stable(value: float | None) -> float | None:
    return round(value, 9) if value is not None else None


def nearest_rank(values: list[float], percentile: float) -> float | None:
    """Nearest-rank percentile: rank=ceil(p*n), clamped to the population."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(percentile * len(ordered))))
    return _stable(ordered[rank - 1])


def _rate(numerator: int, denominator: int) -> float | None:
    return _stable(numerator / denominator) if denominator else None


def _mean(values: list[float]) -> float | None:
    return _stable(fmean(values)) if values else None


def _median(values: list[float]) -> float | None:
    return _stable(float(median(values))) if values else None


def _distribution(values: Sequence[str | None]) -> dict[str, int]:
    counter = Counter(value if value is not None else "UNKNOWN" for value in values)
    return dict(sorted(counter.items()))


def _quality(valid: list[EvidencePair]) -> QualityComparison:
    observed = [
        (pair.direct.quality_score, pair.shadow.quality_score)
        for pair in valid
        if pair.direct is not None
        and pair.shadow is not None
        and pair.direct.quality_score is not None
        and pair.shadow.quality_score is not None
    ]
    if not observed:
        return QualityComparison("NOT_MEASURED", 0, None, None, None, None, 0, 0, 0)
    direct = [item[0] for item in observed]
    shadow = [item[1] for item in observed]
    deltas = [shadow_value - direct_value for direct_value, shadow_value in observed]
    return QualityComparison(
        status="MEASURED",
        sample_count=len(observed),
        direct_mean=_mean(direct),
        shadow_mean=_mean(shadow),
        delta_mean=_mean(deltas),
        delta_median=_median(deltas),
        shadow_better_count=sum(delta > 0 for delta in deltas),
        direct_better_count=sum(delta < 0 for delta in deltas),
        equal_count=sum(delta == 0 for delta in deltas),
    )


def _known(records: list[EvidenceRecord], attribute: str) -> list[float]:
    return [float(value) for record in records if (value := getattr(record, attribute)) is not None]


def route_metrics(
    route: str,
    pairs: tuple[EvidencePair, ...],
    issues: tuple[ValidationIssue, ...],
) -> RouteMetrics:
    """Compute metrics; rates use complete pairs unless explicitly observational."""
    route_pairs = [pair for pair in pairs if pair.logical_route == route]
    valid = [pair for pair in route_pairs if pair.status is PairStatus.VALID]
    incomplete = [pair for pair in route_pairs if pair.status is PairStatus.INCOMPLETE]
    complete = [
        (pair.direct, pair.shadow)
        for pair in valid
        if pair.direct is not None and pair.shadow is not None
    ]
    direct = [item[0] for item in complete]
    shadow = [item[1] for item in complete]
    count = len(valid)

    direct_latency = _known(direct, "latency_ms")
    shadow_latency = _known(shadow, "latency_ms")
    latency_deltas = [
        shadow_record.latency_ms - direct_record.latency_ms
        for direct_record, shadow_record in complete
        if direct_record.latency_ms is not None and shadow_record.latency_ms is not None
    ]
    latency_ratios = [
        shadow_record.latency_ms / direct_record.latency_ms
        for direct_record, shadow_record in complete
        if direct_record.latency_ms is not None
        and shadow_record.latency_ms is not None
        and direct_record.latency_ms > 0
    ]

    direct_costs = [
        record.cost_usd for record in direct if record.cost_known and record.cost_usd is not None
    ]
    shadow_costs = [
        record.cost_usd for record in shadow if record.cost_known and record.cost_usd is not None
    ]
    cost_deltas = [
        shadow_record.cost_usd - direct_record.cost_usd
        for direct_record, shadow_record in complete
        if direct_record.cost_known
        and shadow_record.cost_known
        and direct_record.cost_usd is not None
        and shadow_record.cost_usd is not None
    ]

    provider_comparable = [
        (direct_record, shadow_record)
        for direct_record, shadow_record in complete
        if direct_record.actual_provider is not None and shadow_record.actual_provider is not None
    ]
    model_comparable = [
        (direct_record, shadow_record)
        for direct_record, shadow_record in complete
        if direct_record.actual_model is not None and shadow_record.actual_model is not None
    ]
    schema_comparable = [
        (direct_record, shadow_record)
        for direct_record, shadow_record in complete
        if direct_record.schema_valid is not None and shadow_record.schema_valid is not None
    ]
    fingerprint_comparable = [
        (direct_record, shadow_record)
        for direct_record, shadow_record in complete
        if direct_record.response_fingerprint is not None
        and shadow_record.response_fingerprint is not None
    ]
    retry_known = [record for record in shadow if record.retry_count is not None]
    invalid_refs = {issue.record_ref for issue in issues if issue.logical_route == route}

    return RouteMetrics(
        logical_route=route,
        sample_count=len(valid) + len(incomplete),
        complete_pair_count=count,
        incomplete_pair_count=len(incomplete),
        invalid_record_count=len(invalid_refs),
        direct_success_rate=_rate(sum(record.success for record in direct), count),
        shadow_success_rate=_rate(sum(record.success for record in shadow), count),
        direct_schema_valid_rate=_rate(
            sum(record.schema_valid is True for record in direct), count
        ),
        shadow_schema_valid_rate=_rate(
            sum(record.schema_valid is True for record in shadow), count
        ),
        shadow_fallback_rate=_rate(sum(record.fallback_used is True for record in shadow), count),
        shadow_retry_rate=_rate(
            sum((record.retry_count or 0) > 0 for record in retry_known), len(retry_known)
        ),
        direct_p50_latency_ms=nearest_rank(direct_latency, 0.50),
        direct_p95_latency_ms=nearest_rank(direct_latency, 0.95),
        shadow_p50_latency_ms=nearest_rank(shadow_latency, 0.50),
        shadow_p95_latency_ms=nearest_rank(shadow_latency, 0.95),
        latency_delta_p50_ms=nearest_rank(latency_deltas, 0.50),
        latency_delta_p95_ms=nearest_rank(latency_deltas, 0.95),
        latency_ratio_p50=nearest_rank(latency_ratios, 0.50),
        latency_ratio_p95=nearest_rank(latency_ratios, 0.95),
        provider_identity_known_rate=_rate(
            sum(record.actual_provider is not None for record in shadow), count
        ),
        model_identity_known_rate=_rate(
            sum(record.actual_model is not None for record in shadow), count
        ),
        cost_known_rate=_rate(sum(record.cost_known for record in shadow), count),
        direct_mean_cost_usd=_mean(direct_costs),
        direct_median_cost_usd=_median(direct_costs),
        shadow_mean_cost_usd=_mean(shadow_costs),
        shadow_median_cost_usd=_median(shadow_costs),
        known_cost_delta_mean=_mean(cost_deltas),
        known_cost_delta_median=_median(cost_deltas),
        unknown_cost_count=sum(not record.cost_known for record in [*direct, *shadow]),
        unknown_identity_count=sum(not record.identity_proven for record in shadow),
        model_substitution_rate=_rate(
            sum(
                direct_record.actual_model != shadow_record.actual_model
                for direct_record, shadow_record in model_comparable
            ),
            len(model_comparable),
        ),
        provider_substitution_rate=_rate(
            sum(
                direct_record.actual_provider != shadow_record.actual_provider
                for direct_record, shadow_record in provider_comparable
            ),
            len(provider_comparable),
        ),
        error_distribution_direct=_distribution([record.error_class for record in direct]),
        error_distribution_shadow=_distribution([record.error_class for record in shadow]),
        # Getrennt von `error_distribution_shadow`: dort steht, womit die
        # logische Seite ENDETE, hier, was auf dem Weg dorthin passierte. Ein
        # Upstream, der bei jedem zweiten Aufruf einen Timeout wirft und beim
        # zweiten Versuch antwortet, ist in der ersten Verteilung unsichtbar.
        attempt_error_distribution_shadow=_distribution(
            [error for record in shadow for error in record.attempt_error_classes]
        ),
        outcome_distribution=_distribution(
            [f"DIRECT:{record.outcome or 'UNKNOWN'}" for record in direct]
            + [f"SHADOW:{record.outcome or 'UNKNOWN'}" for record in shadow]
        ),
        retry_distribution=_distribution(
            [
                str(record.retry_count) if record.retry_count is not None else None
                for record in shadow
            ]
        ),
        fallback_distribution=_distribution(
            [
                "USED"
                if record.fallback_used
                else "NOT_USED"
                if record.fallback_used is not None
                else None
                for record in shadow
            ]
        ),
        schema_divergence_rate=_rate(
            sum(
                direct_record.schema_valid != shadow_record.schema_valid
                for direct_record, shadow_record in schema_comparable
            ),
            len(schema_comparable),
        ),
        success_divergence_rate=_rate(
            sum(
                direct_record.success != shadow_record.success
                for direct_record, shadow_record in complete
            ),
            count,
        ),
        response_divergence_rate=_rate(
            sum(
                direct_record.response_fingerprint != shadow_record.response_fingerprint
                for direct_record, shadow_record in fingerprint_comparable
            ),
            len(fingerprint_comparable),
        ),
        quality=_quality(valid),
    )


__all__ = ["nearest_rank", "route_metrics"]
