from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.jev_shadow_eval.evaluator import (
    EvaluationStatus,
    JevShadowPolicy,
    evaluate,
    load_cases,
    policy_from_dict,
)


@pytest.mark.parametrize("value", [True, None, "100", 1.5, float("nan"), float("inf"), 0, -1])
def test_invalid_sample_count_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        policy_from_dict({"minimum_sample_count": value})


def _row(case_id: str, *, expected: bool, probability: float, cost=0.000001) -> dict:
    return {
        "schema_version": "jev-shadow-case/v1",
        "case_id": case_id,
        "expected_relevant": expected,
        "baseline_relevant": expected,
        "latency_ms": 12.5,
        "cost_usd": cost,
        "response": {
            "model": "typesafe/jev-1.13.0",
            "answers": {"relevant": {"type": "noul", "noul": probability}},
            "usage": {"input_tokens": 20, "output_tokens": 1},
        },
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_ready_report_is_advisory_and_never_primary(tmp_path: Path) -> None:
    source = tmp_path / "cases.jsonl"
    _write(
        source,
        [
            _row("positive", expected=True, probability=0.95),
            _row("negative", expected=False, probability=0.05),
        ],
    )
    cases, issues, digest = load_cases(source)

    report = evaluate(cases, issues, JevShadowPolicy(minimum_sample_count=2))

    assert digest
    assert report["status"] == EvaluationStatus.READY_FOR_SHADOW_REVIEW
    assert report["primary_ready"] is False
    assert report["metrics"]["coverage"] == 1.0
    assert report["metrics"]["false_negative_rate"] == 0.0
    assert report["metrics"]["baseline_accuracy"] == 1.0
    assert report["metrics"]["accuracy_gain_vs_baseline"] == 0.0
    assert report["metrics"]["total_cost_usd"] == 0.000002
    assert len(report["policy_sha256"]) == 64


def test_review_band_reduces_coverage_and_blocks_readiness(tmp_path: Path) -> None:
    source = tmp_path / "cases.jsonl"
    _write(
        source,
        [
            _row("uncertain", expected=True, probability=0.5),
            _row("negative", expected=False, probability=0.05),
        ],
    )
    cases, issues, _ = load_cases(source)

    report = evaluate(
        cases,
        issues,
        JevShadowPolicy(minimum_sample_count=2, minimum_coverage=0.8),
    )

    assert report["status"] == EvaluationStatus.NOT_READY
    assert report["reasons"] == ["COVERAGE_TOO_LOW"]
    assert report["metrics"]["review_count"] == 1


def test_unknown_cost_is_visible_and_blocks_when_required(tmp_path: Path) -> None:
    source = tmp_path / "cases.jsonl"
    _write(
        source,
        [
            _row("positive", expected=True, probability=0.95, cost=None),
            _row("negative", expected=False, probability=0.05),
        ],
    )
    cases, issues, _ = load_cases(source)

    report = evaluate(cases, issues, JevShadowPolicy(minimum_sample_count=2))

    assert report["status"] == EvaluationStatus.NOT_READY
    assert "COST_EVIDENCE_INCOMPLETE" in report["reasons"]
    assert report["metrics"]["total_cost_usd"] is None


def test_invalid_row_invalidates_entire_evidence_set(tmp_path: Path) -> None:
    source = tmp_path / "cases.jsonl"
    rows = [_row("valid", expected=True, probability=0.95)]
    rows.append(_row("invalid", expected=False, probability=1.5))
    _write(source, rows)

    cases, issues, _ = load_cases(source)
    report = evaluate(cases, issues, JevShadowPolicy(minimum_sample_count=1))

    assert len(cases) == 1
    assert len(issues) == 1
    assert report["status"] == EvaluationStatus.INVALID_EVIDENCE
    assert report["reasons"] == ["INVALID_RECORDS_PRESENT"]


def test_duplicate_case_id_is_invalid_evidence(tmp_path: Path) -> None:
    source = tmp_path / "cases.jsonl"
    _write(
        source,
        [
            _row("same", expected=True, probability=0.95),
            _row("same", expected=True, probability=0.95),
        ],
    )

    cases, issues, _ = load_cases(source)

    assert len(cases) == 1
    assert issues[0].detail == "duplicate case_id"


def test_model_identity_drift_blocks_readiness(tmp_path: Path) -> None:
    source = tmp_path / "cases.jsonl"
    rows = [
        _row("first", expected=True, probability=0.95),
        _row("second", expected=False, probability=0.05),
    ]
    rows[1]["response"]["model"] = "typesafe/jev-next"
    _write(source, rows)
    cases, issues, _ = load_cases(source)

    report = evaluate(cases, issues, JevShadowPolicy(minimum_sample_count=2))

    assert report["status"] == EvaluationStatus.NOT_READY
    assert "MODEL_IDENTITY_DRIFT" in report["reasons"]


@pytest.mark.parametrize("expected", [True, False])
def test_single_reference_class_cannot_establish_readiness(tmp_path: Path, expected: bool) -> None:
    source = tmp_path / "cases.jsonl"
    _write(
        source,
        [
            _row(str(index), expected=expected, probability=0.95 if expected else 0.05)
            for index in range(100)
        ],
    )
    cases, issues, _ = load_cases(source)
    report = evaluate(cases, issues, JevShadowPolicy())
    assert report["status"] == EvaluationStatus.INSUFFICIENT_EVIDENCE
    assert report["reasons"] == ["REFERENCE_CLASS_MISSING"]
    assert report["reference_positive_count"] == (100 if expected else 0)
    assert report["reference_negative_count"] == (0 if expected else 100)
    assert report["primary_ready"] is False
