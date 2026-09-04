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
from scripts.litellm_shadow_eval.policy import policy_hash

TOOL_VERSION = "1.0.0"


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
    metrics = {route: route_metrics(route, paired.pairs, issues) for route in routes}
    consensus_routes = {
        record.logical_route for record in loaded.records if record.purpose.lower() == "consensus"
    }
    global_invalid = any(issue.logical_route is None for issue in issues)
    decisions = {
        route: evaluate_graduation(
            route_result,
            policy,
            runtime_flags,
            consensus=route in consensus_routes,
            global_invalid_evidence=global_invalid,
        )
        for route, route_result in metrics.items()
    }
    generated = clock()
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
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
    )


__all__ = ["TOOL_VERSION", "evaluate"]
