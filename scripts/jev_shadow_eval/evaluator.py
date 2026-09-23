"""Evaluate prerecorded Jev relevance decisions against fixed labels."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.ai.decisions import DecisionDisposition, ProbabilityPolicy
from app.integrations.litellm.jev import (
    JevQuestion,
    JevSchemaError,
    parse_systemone_response,
)

SCHEMA_VERSION = "jev-shadow-case/v1"
REPORT_SCHEMA_VERSION = "jev-shadow-report/v1"


class EvaluationStatus(StrEnum):
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_READY = "NOT_READY"
    READY_FOR_SHADOW_REVIEW = "READY_FOR_SHADOW_REVIEW"


@dataclass(frozen=True, slots=True)
class JevShadowPolicy:
    minimum_sample_count: int = 100
    negative_below: float = 0.20
    positive_at: float = 0.80
    minimum_coverage: float = 0.80
    maximum_false_negative_rate: float = 0.02
    maximum_brier_score: float = 0.15
    minimum_accuracy_gain: float = 0.0
    require_all_costs_known: bool = True
    require_single_model_identity: bool = True

    def __post_init__(self) -> None:
        if (
            isinstance(self.minimum_sample_count, bool)
            or not isinstance(self.minimum_sample_count, int)
            or self.minimum_sample_count < 1
        ):
            raise ValueError("minimum_sample_count must be a positive integer")
        ProbabilityPolicy(self.negative_below, self.positive_at)
        for name in ("minimum_coverage", "maximum_false_negative_rate", "maximum_brier_score"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0 <= value <= 1
            ):
                raise ValueError(f"{name} must be within [0, 1]")
        if (
            isinstance(self.minimum_accuracy_gain, bool)
            or not isinstance(self.minimum_accuracy_gain, (int, float))
            or not math.isfinite(float(self.minimum_accuracy_gain))
            or not -1 <= self.minimum_accuracy_gain <= 1
        ):
            raise ValueError("minimum_accuracy_gain must be within [-1, 1]")
        for name in ("require_all_costs_known", "require_single_model_identity"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    line: int
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    expected_relevant: bool
    baseline_relevant: bool
    probability: float
    actual_model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float | None


def _finite_non_negative(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return number


def _case(raw: object) -> Case:
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unknown schema_version")
    case_id = raw.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("case_id is missing")
    expected = raw.get("expected_relevant")
    baseline = raw.get("baseline_relevant")
    if not isinstance(expected, bool) or not isinstance(baseline, bool):
        raise ValueError("expected_relevant and baseline_relevant must be boolean")
    evaluation = parse_systemone_response(
        raw.get("response"), questions={"relevant": JevQuestion("noul")}
    )
    probability = evaluation.answers["relevant"].probability
    assert probability is not None
    cost = raw.get("cost_usd")
    return Case(
        case_id=case_id.strip(),
        expected_relevant=expected,
        baseline_relevant=baseline,
        probability=probability,
        actual_model=evaluation.actual_model,
        input_tokens=evaluation.input_tokens,
        output_tokens=evaluation.output_tokens,
        latency_ms=_finite_non_negative(raw.get("latency_ms"), "latency_ms"),
        cost_usd=None if cost is None else _finite_non_negative(cost, "cost_usd"),
    )


def load_cases(path: Path) -> tuple[tuple[Case, ...], tuple[ValidationIssue, ...], str]:
    data = path.read_bytes()
    cases: list[Case] = []
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = _case(json.loads(line))
            if parsed.case_id in seen:
                raise ValueError("duplicate case_id")
            seen.add(parsed.case_id)
            cases.append(parsed)
        except (json.JSONDecodeError, ValueError, JevSchemaError) as exc:
            issues.append(ValidationIssue(line_number, type(exc).__name__, str(exc)))
    return tuple(cases), tuple(issues), hashlib.sha256(data).hexdigest()


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def evaluate(
    cases: tuple[Case, ...], issues: tuple[ValidationIssue, ...], policy: JevShadowPolicy
) -> dict[str, Any]:
    threshold = ProbabilityPolicy(policy.negative_below, policy.positive_at)
    dispositions = [threshold.classify(item.probability) for item in cases]
    decided = [item is not DecisionDisposition.REVIEW for item in dispositions]
    predictions = [item is DecisionDisposition.POSITIVE for item in dispositions]
    positives = sum(item.expected_relevant for item in cases)
    false_negatives = sum(
        item.expected_relevant and disposition is DecisionDisposition.NEGATIVE
        for item, disposition in zip(cases, dispositions, strict=True)
    )
    false_positives = sum(
        not item.expected_relevant and disposition is DecisionDisposition.POSITIVE
        for item, disposition in zip(cases, dispositions, strict=True)
    )
    decided_count = sum(decided)
    correct_decided = sum(
        was_decided and prediction == item.expected_relevant
        for item, was_decided, prediction in zip(cases, decided, predictions, strict=True)
    )
    baseline_correct = sum(item.baseline_relevant == item.expected_relevant for item in cases)
    jev_forced_correct = sum((item.probability >= 0.5) == item.expected_relevant for item in cases)
    baseline_accuracy = _rate(baseline_correct, len(cases))
    jev_forced_accuracy = _rate(jev_forced_correct, len(cases))
    accuracy_gain = (
        jev_forced_accuracy - baseline_accuracy
        if jev_forced_accuracy is not None and baseline_accuracy is not None
        else None
    )
    brier = (
        statistics.fmean((item.probability - float(item.expected_relevant)) ** 2 for item in cases)
        if cases
        else None
    )
    coverage = _rate(decided_count, len(cases))
    false_negative_rate = _rate(false_negatives, positives)
    known_costs = [item.cost_usd for item in cases if item.cost_usd is not None]
    reasons: list[str] = []
    if issues:
        status = EvaluationStatus.INVALID_EVIDENCE
        reasons.append("INVALID_RECORDS_PRESENT")
    elif len(cases) < policy.minimum_sample_count:
        status = EvaluationStatus.INSUFFICIENT_EVIDENCE
        reasons.append("SAMPLE_COUNT_TOO_LOW")
    elif positives == 0 or positives == len(cases):
        # Both classes are needed to assess missed signals and false alarms.
        status = EvaluationStatus.INSUFFICIENT_EVIDENCE
        reasons.append("REFERENCE_CLASS_MISSING")
    else:
        if coverage is None or coverage < policy.minimum_coverage:
            reasons.append("COVERAGE_TOO_LOW")
        if false_negative_rate is None or false_negative_rate > policy.maximum_false_negative_rate:
            reasons.append("FALSE_NEGATIVE_RATE_TOO_HIGH")
        if brier is None or brier > policy.maximum_brier_score:
            reasons.append("BRIER_SCORE_TOO_HIGH")
        if policy.require_all_costs_known and len(known_costs) != len(cases):
            reasons.append("COST_EVIDENCE_INCOMPLETE")
        if policy.require_single_model_identity and len({item.actual_model for item in cases}) != 1:
            reasons.append("MODEL_IDENTITY_DRIFT")
        if accuracy_gain is None or accuracy_gain < policy.minimum_accuracy_gain:
            reasons.append("ACCURACY_GAIN_TOO_LOW")
        status = EvaluationStatus.NOT_READY if reasons else EvaluationStatus.READY_FOR_SHADOW_REVIEW
    models = sorted({item.actual_model for item in cases})
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": status.value,
        "reasons": reasons,
        "primary_ready": False,
        "sample_count": len(cases),
        "reference_positive_count": positives,
        "reference_negative_count": len(cases) - positives,
        "invalid_record_count": len(issues),
        "validation_issues": [asdict(issue) for issue in issues],
        "actual_models": models,
        "metrics": {
            "coverage": coverage,
            "review_count": len(cases) - decided_count,
            "accuracy_decided": _rate(correct_decided, decided_count),
            "false_negative_count": false_negatives,
            "false_negative_rate": false_negative_rate,
            "false_positive_count": false_positives,
            "brier_score": brier,
            "baseline_accuracy": baseline_accuracy,
            "jev_forced_accuracy": jev_forced_accuracy,
            "accuracy_gain_vs_baseline": accuracy_gain,
            "latency_p50_ms": statistics.median(item.latency_ms for item in cases)
            if cases
            else None,
            "latency_p95_ms": _percentile([item.latency_ms for item in cases], 0.95),
            "cost_known_count": len(known_costs),
            "total_cost_usd": sum(known_costs) if len(known_costs) == len(cases) else None,
            "input_tokens": sum(item.input_tokens for item in cases),
            "output_tokens": sum(item.output_tokens for item in cases),
        },
        "policy": asdict(policy),
        "policy_sha256": hashlib.sha256(
            json.dumps(asdict(policy), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def policy_from_dict(raw: object) -> JevShadowPolicy:
    if not isinstance(raw, dict):
        raise ValueError("policy must be an object")
    allowed = set(JevShadowPolicy.__dataclass_fields__)
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown policy fields: {sorted(unknown)}")
    return JevShadowPolicy(**raw)


__all__ = [
    "Case",
    "EvaluationStatus",
    "JevShadowPolicy",
    "ValidationIssue",
    "evaluate",
    "load_cases",
    "policy_from_dict",
]
