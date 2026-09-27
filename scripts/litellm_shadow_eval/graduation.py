"""Advisory-only graduation evaluation; never activates PRIMARY."""

from __future__ import annotations

from datetime import datetime, timedelta

from scripts.litellm_shadow_eval.models import (
    GraduationDecision,
    GraduationPolicy,
    GraduationStatus,
    RouteMetrics,
    RuntimeEvidenceFlags,
    RuntimeProof,
)
from scripts.litellm_shadow_eval.policy import effective_policy

#: Politikschalter -> verlangte Nachweise -> Grund, wenn einer fehlt.
_REQUIRED_PROOFS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("require_off_mode_proven", ("off_mode_proven",), "OFF_MODE_NOT_PROVEN"),
    ("require_rollback_proven", ("rollback_proven",), "ROLLBACK_NOT_PROVEN"),
    (
        "require_gateway_down_fallback_proven",
        ("gateway_down_proven", "direct_fallback_proven"),
        "GATEWAY_DOWN_FALLBACK_NOT_PROVEN",
    ),
    ("require_auth_no_retry_proven", ("auth_no_retry_proven",), "AUTH_RETRY_CONTRACT_NOT_PROVEN"),
    ("require_timeout_retry_proven", ("timeout_retry_proven",), "TIMEOUT_RETRY_NOT_PROVEN"),
    (
        "require_rate_limit_retry_proven",
        ("rate_limit_retry_proven",),
        "RATE_LIMIT_RETRY_NOT_PROVEN",
    ),
    (
        "require_server_error_retry_proven",
        ("server_error_retry_proven",),
        "SERVER_ERROR_RETRY_NOT_PROVEN",
    ),
    ("require_circuit_proven", ("circuit_proven",), "CIRCUIT_NOT_PROVEN"),
)


def _proof_reasons(
    name: str,
    proof: RuntimeProof | None,
    active: GraduationPolicy,
    evaluated_at: datetime,
) -> list[str]:
    """Ist ein erbrachter Nachweis auch BELEGT -- referenziert, datiert, frisch?"""
    if not active.require_referenced_runtime_evidence:
        return []
    if proof is None or not proof.referenced or proof.proven_at is None:
        return [f"RUNTIME_PROOF_UNREFERENCED:{name}"]
    moment = datetime.fromisoformat(proof.proven_at)
    # Vor der Altersgrenze und unabhaengig von ihr: ein Datum in der Zukunft
    # waere sonst der bequemste Weg, nie zu veralten.
    if moment > evaluated_at:
        return [f"RUNTIME_PROOF_IN_FUTURE:{name}"]
    limit = active.maximum_runtime_proof_age_days
    if limit is not None and evaluated_at - moment > timedelta(days=limit):
        return [f"RUNTIME_PROOF_STALE:{name}"]
    return []


def _runtime_reasons(
    active: GraduationPolicy, flags: RuntimeEvidenceFlags, evaluated_at: datetime
) -> list[str]:
    reasons: list[str] = []
    for gate, names, missing in _REQUIRED_PROOFS:
        if not getattr(active, gate):
            continue
        if not all(getattr(flags, name) for name in names):
            reasons.append(missing)
            continue
        for name in names:
            reasons.extend(_proof_reasons(name, flags.proof(name), active, evaluated_at))
    if active.require_trading_gate_unchanged and flags.trading_gate_changed:
        reasons.append("TRADING_GATE_CHANGED")
    if active.require_execution_gate_unchanged and flags.execution_gate_changed:
        reasons.append("EXECUTION_GATE_CHANGED")
    return reasons


def _quality_reasons(
    metrics: RouteMetrics, active: GraduationPolicy
) -> tuple[list[str], list[str]]:
    """(blockierende Gruende, Hinweise) zur Qualitaet."""
    quality = metrics.quality
    if quality.status == "NOT_MEASURED":
        # Fehlende Qualitaetsbelege sind ein Grund INNERHALB der Bewertung,
        # nicht eine Fussnote danach. Der beratende Hinweis wird am Ende der
        # Bewertung ergaenzt, wenn die Politik Qualitaet ausdruecklich freigibt.
        return (["QUALITY_NOT_MEASURED"] if active.require_quality_evidence else []), []
    blocking: list[str] = []
    advisory: list[str] = []
    minimum = (
        active.minimum_sample_count
        if active.minimum_quality_sample_count is None
        else active.minimum_quality_sample_count
    )
    if (
        quality.sample_count < minimum
        or quality.coverage is None
        or quality.coverage < active.minimum_quality_coverage
    ):
        if active.require_quality_evidence:
            blocking.append("QUALITY_SAMPLE_TOO_SMALL")
        else:
            advisory.append("QUALITY_SAMPLE_TOO_SMALL_ADVISORY")
    # `delta_mean` statt `shadow_mean - direct_mean`: es ist bereits auf neun
    # Stellen stabilisiert, die Differenz zweier gerundeter Mittel waere es
    # nicht (0.78 - 0.8 < -0.02 in Gleitkomma). Eine GEMESSENE Regression
    # blockiert auch bei beratender Politik: beratend heisst "fehlende Belege
    # halten nicht auf", nicht "gemessener Schaden zaehlt nicht".
    if quality.delta_mean is None or quality.delta_mean < -active.maximum_quality_regression:
        blocking.append("QUALITY_REGRESSION")
    return blocking, advisory


def evaluate_graduation(
    metrics: RouteMetrics,
    policy: GraduationPolicy,
    flags: RuntimeEvidenceFlags,
    *,
    evaluated_at: datetime,
    consensus: bool = False,
    global_invalid_evidence: bool = False,
) -> GraduationDecision:
    """Return evidence status and reasons, never an activation instruction.

    ``evaluated_at`` ist der Zeitpunkt, gegen den das Alter der
    Betriebsnachweise gemessen wird (im Bericht: ``generated_at``).
    """
    active = effective_policy(policy, metrics.logical_route)
    reasons: list[str] = []
    advisories: list[str] = []
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
        if active.require_identity_observability:
            if (
                metrics.provider_identity_known_rate != 1.0
                or metrics.model_identity_known_rate != 1.0
            ):
                reasons.append("IDENTITY_OBSERVABILITY_TOO_LOW")
            # Gesetzte Felder sind eine Behauptung; `identity_proven` ist der
            # Beleg. Ohne ihn weiss niemand, welches Modell wirklich antwortete.
            if metrics.unknown_identity_count:
                reasons.append("IDENTITY_NOT_PROVEN")
        # Ohne vollstaendige Zaehlung sieht die Retry-Grenze unten nur Zahlen:
        # "UNKNOWN" faellt dort heraus und haette nie einen Befund ausgeloest.
        if metrics.unknown_attempt_accounting_count:
            if active.require_complete_attempt_accounting:
                reasons.append("ATTEMPT_ACCOUNTING_INCOMPLETE")
            else:
                advisories.append("ATTEMPT_ACCOUNTING_INCOMPLETE_ADVISORY")
        retry_keys = [int(key) for key in metrics.retry_distribution if key.isdigit()]
        if not active.maximum_unbounded_retry and retry_keys and max(retry_keys) > 2:
            reasons.append("UNBOUNDED_RETRY_OBSERVED")
        if metrics.cost_known_rate != 1.0:
            if active.require_cost_known:
                reasons.append("COST_NOT_FULLY_KNOWN")
            else:
                advisories.append("COST_NOT_FULLY_KNOWN_ADVISORY")
        # Halbe Paare sind keine neutrale Luecke: ein fehlender Schatten kann
        # ein Schattenausfall sein. Hundert saubere Paare hinter tausend halben
        # belegen nichts ueber die tausend.
        if (
            metrics.unexplained_incomplete_rate is None
            or metrics.unexplained_incomplete_rate > active.maximum_unexplained_incomplete_rate
        ):
            reasons.append("INCOMPLETE_PAIRS_UNEXPLAINED")
        reasons.extend(_runtime_reasons(active, flags, evaluated_at))
        quality_blocking, quality_advisory = _quality_reasons(metrics, active)
        reasons.extend(quality_blocking)
        advisories.extend(quality_advisory)
        status = GraduationStatus.NOT_READY if reasons else GraduationStatus.READY

    reasons.extend(advisories)
    if metrics.quality.status == "NOT_MEASURED" and "QUALITY_NOT_MEASURED" not in reasons:
        # Politik erklaert Qualitaet ausdruecklich fuer beratend: der Befund
        # bleibt sichtbar, aendert den Status aber nicht.
        reasons.append("QUALITY_NOT_MEASURED_ADVISORY")
    shadow_validated = status is GraduationStatus.READY
    if consensus and shadow_validated:
        reasons.append("CONSENSUS_SHADOW_ONLY")
    return GraduationDecision(
        logical_route=metrics.logical_route,
        status=status,
        reasons=tuple(reasons),
        shadow_validated=shadow_validated,
        consensus_route=consensus,
    )


__all__ = ["evaluate_graduation"]
