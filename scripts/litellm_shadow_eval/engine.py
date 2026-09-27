"""Offline evaluation orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from scripts.litellm_shadow_eval.graduation import evaluate_graduation
from scripts.litellm_shadow_eval.loader import load_evidence
from scripts.litellm_shadow_eval.metrics import route_metrics
from scripts.litellm_shadow_eval.models import (
    EvaluationReport,
    GraduationPolicy,
    RuntimeEvidenceFlags,
)
from scripts.litellm_shadow_eval.pairing import pair_records
from scripts.litellm_shadow_eval.policy import effective_policy, policy_hash

#: 1.1.0: strengere Reife (Qualitaetsumfang/-grenze, belegte Identitaet,
#: Versuchs-/Kostenzaehlung, erklaerte halbe Paare, Ausfallnachweise,
#: referenzierte und datierte Betriebsnachweise). Berichtsschema v2.
TOOL_VERSION = "1.1.0"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def evaluate(
    inputs: list[Path],
    policy: GraduationPolicy,
    runtime_flags: RuntimeEvidenceFlags,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> EvaluationReport:
    """Evaluate local files. This package has no network or runtime imports."""
    loaded = load_evidence(inputs)
    paired = pair_records(loaded.records)
    issues = tuple(
        sorted(
            (*loaded.issues, *paired.issues),
            key=lambda item: (item.record_ref, item.code, item.message),
        )
    )
    routes = tuple(
        sorted(
            {record.logical_route for record in loaded.records}
            | {issue.logical_route for issue in issues if issue.logical_route}
        )
    )
    metrics = {
        route: route_metrics(
            route,
            paired.pairs,
            issues,
            allowed_exclusion_reasons=effective_policy(policy, route).allowed_exclusion_reasons,
        )
        for route in routes
    }
    # Vor den Entscheidungen: das Alter der Betriebsnachweise wird gegen genau
    # den Zeitpunkt gemessen, der im Bericht als `generated_at` steht.
    generated = clock()
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    # Vereinigung aus gueltigen Datensaetzen UND rohen Zeilen: eine verworfene
    # Consensus-Zeile darf die Decke nicht mitnehmen.
    consensus_routes = {
        record.logical_route for record in loaded.records if record.purpose.lower() == "consensus"
    } | set(loaded.consensus_routes)
    global_invalid = any(issue.logical_route is None for issue in issues)
    decisions = {
        route: evaluate_graduation(
            route_result,
            policy,
            runtime_flags,
            evaluated_at=generated,
            consensus=route in consensus_routes,
            global_invalid_evidence=global_invalid,
        )
        for route, route_result in metrics.items()
    }
    return EvaluationReport(
        tool_version=TOOL_VERSION,
        policy_hash=policy_hash(policy),
        input_sha256=loaded.input_sha256,
        input_files=loaded.input_files,
        record_count=loaded.record_count,
        generated_at=generated.astimezone(UTC).isoformat(),
        routes=routes,
        invalid_record_count=len({issue.record_ref for issue in issues}),
        validation_issues=issues,
        metrics=metrics,
        decisions=decisions,
        runtime_evidence=runtime_flags,
    )


__all__ = ["TOOL_VERSION", "evaluate"]
