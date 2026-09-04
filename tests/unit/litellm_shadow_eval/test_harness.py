from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts.litellm_shadow_eval.engine import evaluate
from scripts.litellm_shadow_eval.loader import load_evidence, normalize_record
from scripts.litellm_shadow_eval.metrics import nearest_rank
from scripts.litellm_shadow_eval.models import (
    GraduationPolicy,
    GraduationStatus,
    RuntimeEvidenceFlags,
)
from scripts.litellm_shadow_eval.policy import policy_hash
from scripts.litellm_shadow_eval.reporting import canonical_json

from tests.unit.litellm_shadow_eval.helpers import proven_flags, row, write_jsonl

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _dataset(tmp_path: Path, count: int, mutate: Any | None = None) -> Path:
    rows: list[dict[str, Any]] = []
    for number in range(count):
        direct = row("DIRECT", number)
        shadow = row("SHADOW", number)
        if mutate is not None:
            mutate(number, direct, shadow)
        rows.extend((direct, shadow))
    return write_jsonl(tmp_path / "evidence.jsonl", rows)


def _evaluate(
    path: Path,
    *,
    minimum: int = 1,
    flags: RuntimeEvidenceFlags | None = None,
):
    return evaluate(
        [path],
        GraduationPolicy(minimum_sample_count=minimum),
        flags or proven_flags(),
        clock=lambda: NOW,
    )


def _with_quality(number: int, direct: dict[str, Any], shadow: dict[str, Any]) -> None:
    """Ein vollstaendiger Beleg enthaelt Qualitaet -- sonst ist er nicht vollstaendig."""
    direct["quality_score"] = 0.80
    shadow["quality_score"] = 0.80


def test_100_perfect_pairs_are_ready_with_complete_metrics(tmp_path: Path) -> None:
    report = _evaluate(_dataset(tmp_path, 100, _with_quality), minimum=100)
    metrics = report.metrics["standard"]
    assert metrics.sample_count == metrics.complete_pair_count == 100
    assert metrics.incomplete_pair_count == metrics.invalid_record_count == 0
    assert metrics.direct_success_rate == metrics.shadow_success_rate == 1.0
    assert metrics.direct_schema_valid_rate == metrics.shadow_schema_valid_rate == 1.0
    assert metrics.shadow_p50_latency_ms == metrics.shadow_p95_latency_ms == 12.0
    assert metrics.latency_delta_p50_ms == 2.0
    assert metrics.latency_ratio_p50 == 1.2
    assert report.decisions["standard"].status is GraduationStatus.READY


def test_99_samples_are_insufficient_for_preregistered_minimum(tmp_path: Path) -> None:
    report = _evaluate(_dataset(tmp_path, 99), minimum=100)
    decision = report.decisions["standard"]
    assert decision.status is GraduationStatus.INSUFFICIENT_EVIDENCE
    assert "SAMPLE_COUNT_TOO_LOW" in decision.reasons


@pytest.mark.parametrize(
    ("field", "reason"),
    [("success", "SUCCESS_RATE_TOO_LOW"), ("schema_valid", "SCHEMA_RATE_TOO_LOW")],
)
def test_98_percent_threshold_is_not_ready(tmp_path: Path, field: str, reason: str) -> None:
    def mutate(number: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        if number < 2:
            shadow[field] = False

    report = _evaluate(_dataset(tmp_path, 100, mutate), minimum=100)
    decision = report.decisions["standard"]
    assert decision.status is GraduationStatus.NOT_READY
    assert reason in decision.reasons


def test_missing_cost_stays_unknown_and_is_counted(tmp_path: Path) -> None:
    def mutate(_: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        shadow.pop("cost_usd")
        shadow["cost_known"] = False

    metrics = _evaluate(_dataset(tmp_path, 1, mutate)).metrics["standard"]
    assert metrics.shadow_mean_cost_usd is None
    assert metrics.cost_known_rate == 0.0
    assert metrics.unknown_cost_count == 1


def test_missing_provider_never_infers_identity_from_alias(tmp_path: Path) -> None:
    def mutate(_: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        shadow.pop("actual_provider")
        shadow["identity_proven"] = False

    metrics = _evaluate(_dataset(tmp_path, 1, mutate)).metrics["standard"]
    assert metrics.provider_identity_known_rate == 0.0
    assert metrics.unknown_identity_count == 1


def test_malformed_json_is_invalid_evidence(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text('{"not":\n', encoding="utf-8")
    report = _evaluate(path)
    assert report.invalid_record_count == 1
    assert report.routes == ()
    assert report.validation_issues[0].code == "MALFORMED_JSONL"


@pytest.mark.parametrize(
    ("side", "code"),
    [("DIRECT", "DUPLICATE_DIRECT"), ("SHADOW", "DUPLICATE_SHADOW")],
)
def test_duplicate_side_is_invalid(tmp_path: Path, side: str, code: str) -> None:
    rows = [row("DIRECT"), row("SHADOW"), row(side)]
    report = _evaluate(write_jsonl(tmp_path / "duplicate.jsonl", rows))
    assert code in {issue.code for issue in report.validation_issues}
    assert report.decisions["standard"].status is GraduationStatus.INVALID_EVIDENCE


def test_incomplete_pair_is_not_counted_as_complete(tmp_path: Path) -> None:
    report = _evaluate(write_jsonl(tmp_path / "one.jsonl", [row("DIRECT")]))
    metrics = report.metrics["standard"]
    assert metrics.sample_count == metrics.incomplete_pair_count == 1
    assert metrics.complete_pair_count == 0
    assert metrics.direct_success_rate is None


def test_shadow_execution_authority_is_invalid(tmp_path: Path) -> None:
    rows = [row("DIRECT"), row("SHADOW", execution_authority=True)]
    report = _evaluate(write_jsonl(tmp_path / "authority.jsonl", rows))
    assert "SHADOW_EXECUTION_AUTHORITY" in {item.code for item in report.validation_issues}
    assert report.decisions["standard"].status is GraduationStatus.INVALID_EVIDENCE


def test_consensus_can_only_be_shadow_validated(tmp_path: Path) -> None:
    rows = [
        row("DIRECT", logical_route="reasoning", purpose="consensus", quality_score=0.9),
        row("SHADOW", logical_route="reasoning", purpose="consensus", quality_score=0.9),
    ]
    report = _evaluate(write_jsonl(tmp_path / "consensus.jsonl", rows))
    decision = report.decisions["reasoning"]
    assert decision.status is GraduationStatus.READY
    assert decision.shadow_validated is True
    assert decision.primary_ready is False, "belegt ist nicht dasselbe wie erlaubt"
    assert "CONSENSUS_SHADOW_ONLY" in decision.reasons
    payload = report.to_dict()
    assert payload["decisions"]["reasoning"]["consensus_primary_allowed"] is False
    assert payload["decisions"]["reasoning"]["primary_ready"] is False
    assert payload["primary_ready_routes"] == []


def test_shuffled_input_has_identical_semantics_and_hash(tmp_path: Path) -> None:
    rows = [row(side, number) for number in range(5) for side in ("DIRECT", "SHADOW")]
    path = write_jsonl(tmp_path / "evidence.jsonl", rows)
    report_a = _evaluate(path)
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    write_jsonl(path, shuffled)
    report_b = _evaluate(path)
    assert report_a.metrics == report_b.metrics
    assert report_a.decisions == report_b.decisions
    assert report_a.input_sha256 == report_b.input_sha256
    assert canonical_json(report_a) == canonical_json(report_b)


def test_empty_input_has_no_zero_percent_lies(tmp_path: Path) -> None:
    report = _evaluate(write_jsonl(tmp_path / "empty.jsonl", []))
    assert report.record_count == 0
    assert report.routes == ()
    assert report.metrics == {}


def test_nearest_rank_empty_and_singleton_semantics() -> None:
    assert nearest_rank([], 0.5) is None
    assert nearest_rank([7.25], 0.5) == nearest_rank([7.25], 0.95) == 7.25
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0


def test_retry_and_fallback_aggregation(tmp_path: Path) -> None:
    def mutate(number: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        shadow["retry_count"] = number
        shadow["attempt_count"] = number + 1
        shadow["fallback_used"] = number == 1

    metrics = _evaluate(_dataset(tmp_path, 2, mutate)).metrics["standard"]
    assert metrics.shadow_retry_rate == 0.5
    assert metrics.shadow_fallback_rate == 0.5
    assert metrics.retry_distribution == {"0": 1, "1": 1}
    assert metrics.fallback_distribution == {"NOT_USED": 1, "USED": 1}


def test_physical_retry_rows_collapse_to_one_logical_shadow_record(tmp_path: Path) -> None:
    first = row(
        "SHADOW",
        attempt_count=1,
        retry_count=0,
        success=False,
        outcome="fallthrough",
        error_class="timeout",
        cost_usd=None,
        cost_known=False,
    )
    final = row("SHADOW", attempt_count=2, retry_count=1, latency_ms=7.0)
    report = _evaluate(write_jsonl(tmp_path / "retry.jsonl", [row("DIRECT"), first, final]))
    metrics = report.metrics["standard"]
    assert metrics.complete_pair_count == 1 and metrics.invalid_record_count == 0
    assert metrics.shadow_retry_rate == 1.0
    assert metrics.shadow_p50_latency_ms == 19.0
    assert metrics.shadow_mean_cost_usd is None and metrics.unknown_cost_count == 1


def test_known_cost_mean_median_and_mixed_unknown(tmp_path: Path) -> None:
    def mutate(number: int, direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        direct["cost_usd"] = float(number + 1)
        shadow["cost_usd"] = float((number + 1) * 2)
        if number == 2:
            shadow.pop("cost_usd")
            shadow["cost_known"] = False

    metrics = _evaluate(_dataset(tmp_path, 3, mutate)).metrics["standard"]
    assert metrics.direct_mean_cost_usd == metrics.direct_median_cost_usd == 2.0
    assert metrics.shadow_mean_cost_usd == metrics.shadow_median_cost_usd == 3.0
    assert metrics.known_cost_delta_mean == metrics.known_cost_delta_median == 1.5
    assert metrics.unknown_cost_count == 1


def test_provider_and_model_substitution(tmp_path: Path) -> None:
    def mutate(_: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        shadow["actual_provider"] = "anthropic"
        shadow["actual_model"] = "claude"

    metrics = _evaluate(_dataset(tmp_path, 1, mutate)).metrics["standard"]
    assert metrics.provider_substitution_rate == metrics.model_substitution_rate == 1.0


def test_quality_absent_is_not_measured_not_zero(tmp_path: Path) -> None:
    report = _evaluate(_dataset(tmp_path, 1))
    quality = report.metrics["standard"].quality
    assert quality.status == "NOT_MEASURED"
    assert quality.direct_mean is quality.shadow_mean is quality.delta_mean is None
    assert "QUALITY_NOT_MEASURED" in report.decisions["standard"].reasons


def test_quality_pairwise_delta(tmp_path: Path) -> None:
    def mutate(number: int, direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        direct["quality_score"] = [0.5, 0.8, 0.7][number]
        shadow["quality_score"] = [0.7, 0.6, 0.7][number]

    quality = _evaluate(_dataset(tmp_path, 3, mutate)).metrics["standard"].quality
    assert quality.status == "MEASURED" and quality.sample_count == 3
    assert quality.delta_mean == 0.0 and quality.delta_median == 0.0
    assert (quality.shadow_better_count, quality.direct_better_count, quality.equal_count) == (
        1,
        1,
        1,
    )
    report_dict = _evaluate(_dataset(tmp_path, 3, mutate)).to_dict()
    assert report_dict["metrics"]["standard"]["quality_sample_count"] == 3


@pytest.mark.parametrize(
    ("flags", "reason"),
    [
        (proven_flags(rollback_proven=False), "ROLLBACK_NOT_PROVEN"),
        (proven_flags(trading_gate_changed=True), "TRADING_GATE_CHANGED"),
        (proven_flags(execution_gate_changed=True), "EXECUTION_GATE_CHANGED"),
    ],
)
def test_runtime_proof_flags_are_not_invented(
    tmp_path: Path, flags: RuntimeEvidenceFlags, reason: str
) -> None:
    report = _evaluate(_dataset(tmp_path, 1), flags=flags)
    assert report.decisions["standard"].status is GraduationStatus.NOT_READY
    assert reason in report.decisions["standard"].reasons


def test_policy_hash_is_reproducible_and_sensitive() -> None:
    policy = GraduationPolicy()
    assert policy_hash(policy) == policy_hash(GraduationPolicy())
    assert policy_hash(policy) != policy_hash(replace(policy, minimum_sample_count=101))


def test_input_hash_is_reproducible(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "input.jsonl", [row("DIRECT"), row("SHADOW")])
    assert load_evidence([path]).input_sha256 == load_evidence([path]).input_sha256


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"schema_version": "v999"}, "UNKNOWN_SCHEMA_VERSION"),
        ({"logical_route": ""}, "MISSING_ROUTE"),
        ({"side": "OTHER"}, "INVALID_SIDE"),
        ({"latency_ms": -1}, "NEGATIVE_LATENCY"),
        ({"input_tokens": -1}, "NEGATIVE_INPUT_TOKENS"),
        ({"output_tokens": -1}, "NEGATIVE_OUTPUT_TOKENS"),
        ({"cost_usd": -1}, "NEGATIVE_COST"),
        ({"cost_known": True, "cost_usd": None}, "KNOWN_COST_MISSING"),
        ({"cost_known": False, "cost_usd": 0}, "UNKNOWN_COST_HAS_VALUE"),
        ({"identity_proven": True, "actual_provider": None}, "IDENTITY_PROOF_INCOMPLETE"),
        ({"mode": "primary"}, "AMBIGUOUS_PRIMARY_RECORD"),
    ],
)
def test_fail_closed_record_validation(changes: dict[str, Any], code: str) -> None:
    raw = row("SHADOW")
    raw.update(changes)
    record, issues = normalize_record(raw, record_ref="fixture:1")
    assert record is None
    assert code in {issue.code for issue in issues}


def test_telemetry_v2_normalization_keeps_missing_values_unknown() -> None:
    raw = {
        "schema_version": "v2",
        "correlation_id": "corr",
        "call_id": "call",
        "logical_route": "standard",
        "purpose": "analysis",
        "role": "direct",
        "transport": "direct",
        "provider": "openai",
        "model": "gpt",
        "ok": True,
        "latency_ms": 2,
        "prompt_tokens": 3,
        "completion_tokens": 4,
        "ts": "2026-09-04T00:00:00Z",
        "api_key": "must-not-escape",
        "prompt": "private prompt",
    }
    record, issues = normalize_record(raw, record_ref="telemetry:1")
    assert not issues and record is not None
    assert record.cost_usd is None and record.cost_known is False
    assert record.actual_provider is record.actual_model is None
    assert record.identity_proven is False and record.schema_valid is None
    serialized = json.dumps(record.__dict__ if hasattr(record, "__dict__") else str(record))
    assert "must-not-escape" not in serialized and "private prompt" not in serialized


def test_canonical_json_is_deterministic_with_injected_clock(tmp_path: Path) -> None:
    path = _dataset(tmp_path, 2)
    first = _evaluate(path)
    second = _evaluate(path)
    assert canonical_json(first) == canonical_json(second)


def test_report_does_not_expose_absolute_input_path(tmp_path: Path) -> None:
    path = _dataset(tmp_path, 1)
    rendered = canonical_json(_evaluate(path))
    assert str(tmp_path) not in rendered
    assert "1:evidence.jsonl" in rendered
