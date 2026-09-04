"""Advisory-only graduation evaluation; never activates PRIMARY."""

from __future__ import annotations

from scripts.litellm_shadow_eval.models import (
    GraduationDecision,
    GraduationPolicy,
    GraduationStatus,
    RouteMetrics,
    RuntimeEvidenceFlags,
)
from scripts.litellm_shadow_eval.policy import effective_policy


def evaluate_graduation(
    metrics: RouteMetrics,
    policy: GraduationPolicy,
    flags: RuntimeEvidenceFlags,
    *,
    consensus: bool = False,
    global_invalid_evidence: bool = False,
) -> GraduationDecision:
    """Return evidence status and reasons, never an activation instruction."""
    active = effective_policy(policy, metrics.logical_route)
    reasons: list[str] = []
    if global_invalid_evidence or metrics.invalid_record_count:
        reasons.append("INVALID_RECORDS_PRESENT")
        status = GraduationStatus.INVALID_EVIDENCE
    elif metrics.complete_pair_count < active.minimum_sample_count:
        reasons.append("SAMPLE_COUNT_TOO_LOW")
        status = GraduationStatus.INSUFFICIENT_EVIDENCE
    else:
        if (
            metrics.shadow_success_rate is None
            or metrics.shadow_success_rate < active.minimum_success_rate
        ):
            reasons.append("SUCCESS_RATE_TOO_LOW")
        if (
            metrics.shadow_schema_valid_rate is None
            or metrics.shadow_schema_valid_rate < active.minimum_schema_valid_rate
        ):
            reasons.append("SCHEMA_RATE_TOO_LOW")
        if active.require_identity_observability and (
            metrics.provider_identity_known_rate != 1.0 or metrics.model_identity_known_rate != 1.0
        ):
            reasons.append("IDENTITY_OBSERVABILITY_TOO_LOW")
        retry_keys = [int(key) for key in metrics.retry_distribution if key.isdigit()]
        if not active.maximum_unbounded_retry and retry_keys and max(retry_keys) > 2:
            reasons.append("UNBOUNDED_RETRY_OBSERVED")
        if active.require_off_mode_proven and not flags.off_mode_proven:
            reasons.append("OFF_MODE_NOT_PROVEN")
        if active.require_rollback_proven and not flags.rollback_proven:
            reasons.append("ROLLBACK_NOT_PROVEN")
        if active.require_gateway_down_fallback_proven and (
            not flags.gateway_down_proven or not flags.direct_fallback_proven
        ):
            reasons.append("GATEWAY_DOWN_FALLBACK_NOT_PROVEN")
        if active.require_auth_no_retry_proven and not flags.auth_no_retry_proven:
            reasons.append("AUTH_RETRY_CONTRACT_NOT_PROVEN")
        if active.require_trading_gate_unchanged and flags.trading_gate_changed:
            reasons.append("TRADING_GATE_CHANGED")
        if active.require_execution_gate_unchanged and flags.execution_gate_changed:
            reasons.append("EXECUTION_GATE_CHANGED")
        status = GraduationStatus.NOT_READY if reasons else GraduationStatus.READY

    if metrics.quality.status == "NOT_MEASURED":
        reasons.append("QUALITY_NOT_MEASURED")
    shadow_validated = status is GraduationStatus.READY
    if consensus and shadow_validated:
        reasons.append("CONSENSUS_SHADOW_ONLY")
    return GraduationDecision(
        logical_route=metrics.logical_route,
        status=status,
        reasons=tuple(reasons),
        shadow_validated=shadow_validated,
        consensus_primary_allowed=False,
    )


__all__ = ["evaluate_graduation"]
