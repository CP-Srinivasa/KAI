"""Tests for app/research/distillation.py (Sprint 11, I-58–I-62).

Covers:
- compute_shadow_coverage: batch format, live format, mixed, error records, FileNotFoundError
- build_distillation_report: teacher+candidate, eval_report_path, with shadow, missing inputs
- save_distillation_manifest: creates valid JSON, creates parent dirs
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.distillation import (
    DistillationInputs,
    DistillationReadinessReport,
    build_distillation_report,
    compute_shadow_coverage,
    save_distillation_manifest,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _batch_shadow_line(
    *,
    priority_diff: int = 2,
    relevance_diff: float = 0.1,
    impact_diff: float = 0.05,
    sentiment_match: bool = True,
    actionable_match: bool = True,
    tag_overlap: float = 0.8,
) -> str:
    record = {
        "document_id": "doc1",
        "primary_provider": "openai",
        "divergence": {
            "priority_diff": priority_diff,
            "relevance_diff": relevance_diff,
            "impact_diff": impact_diff,
            "sentiment_match": sentiment_match,
            "actionable_match": actionable_match,
            "tag_overlap": tag_overlap,
        },
    }
    return json.dumps(record)


def _live_shadow_line(
    *,
    priority_delta: int = 1,
    relevance_delta: float = 0.05,
    impact_delta: float = 0.02,
    sentiment_match: bool = False,
    actionable_match: bool = True,
    tag_overlap: float = 0.6,
) -> str:
    record = {
        "record_type": "companion_shadow_run",
        "document_id": "doc2",
        "deviations": {
            "priority_delta": priority_delta,
            "relevance_delta": relevance_delta,
            "impact_delta": impact_delta,
            "sentiment_match": sentiment_match,
            "actionable_match": actionable_match,
            "tag_overlap": tag_overlap,
        },
    }
    return json.dumps(record)


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_eval_report_dict(
    sentiment_agreement: float = 0.9,
    priority_mae: float = 1.0,
    false_actionable_rate: float = 0.02,
) -> dict:
    return {
        "dataset_type": "internal_benchmark",
        "teacher_count": 10,
        "baseline_count": 10,
        "paired_count": 9,
        "notes": [],
        "metrics": {
            "sentiment_agreement": sentiment_agreement,
            "priority_mae": priority_mae,
            "relevance_mae": 0.10,
            "impact_mae": 0.12,
            "tag_overlap_mean": 0.40,
            "actionable_accuracy": 0.90,
            "false_actionable_rate": false_actionable_rate,
            "sample_count": 9,
            "missing_pairs": 1,
        },
    }


def _make_dataset_row(doc_id: str, analysis_source: str) -> dict:
    """Build a JSONL dataset row in the format expected by compare_datasets()."""
    target = json.dumps(
        {
            "sentiment_label": "bullish",
            "priority_score": 7,
            "relevance_score": 0.8,
            "impact_score": 0.6,
            "actionable": True,
            "tags": ["crypto"],
        }
    )
    return {
        "metadata": {
            "document_id": doc_id,
            "analysis_source": analysis_source,
        },
        "messages": [
            {"role": "system", "content": "Analyze this."},
            {"role": "user", "content": "Title: BTC\nContent:\nBitcoin rallied."},
            {"role": "assistant", "content": target},
        ],
    }


def _make_teacher_row(doc_id: str = "d1") -> dict:
    return _make_dataset_row(doc_id, "external_llm")


def _make_candidate_row(doc_id: str = "d1") -> dict:
    return _make_dataset_row(doc_id, "internal")


# ── compute_shadow_coverage ───────────────────────────────────────────────────


def test_compute_shadow_coverage_batch_format(tmp_path: Path) -> None:
    """Reads batch shadow format (divergence keys) correctly."""
    f = tmp_path / "shadow.jsonl"
    _write_jsonl(f, [_batch_shadow_line(priority_diff=3, sentiment_match=True)])
    report = compute_shadow_coverage(f)
    assert report.total_records == 1
    assert report.valid_records == 1
    assert report.error_records == 0
    assert report.avg_priority_diff == pytest.approx(3.0)
    assert report.sentiment_agreement_rate == pytest.approx(1.0)


def test_compute_shadow_coverage_live_format(tmp_path: Path) -> None:
    """Reads live shadow format (deviations keys) correctly."""
    f = tmp_path / "shadow.jsonl"
    _write_jsonl(f, [_live_shadow_line(priority_delta=2, sentiment_match=False)])
    report = compute_shadow_coverage(f)
    assert report.total_records == 1
    assert report.valid_records == 1
    assert report.error_records == 0
    assert report.avg_priority_diff == pytest.approx(2.0)
    assert report.sentiment_agreement_rate == pytest.approx(0.0)


def test_compute_shadow_coverage_mixed_formats(tmp_path: Path) -> None:
    """Handles a JSONL file containing both batch and live format records."""
    f = tmp_path / "shadow.jsonl"
    _write_jsonl(
        f,
        [
            _batch_shadow_line(priority_diff=4, sentiment_match=True),
            _live_shadow_line(priority_delta=2, sentiment_match=False),
        ],
    )
    report = compute_shadow_coverage(f)
    assert report.total_records == 2
    assert report.valid_records == 2
    assert report.error_records == 0
    assert report.avg_priority_diff == pytest.approx(3.0)  # (4+2)/2
    assert report.sentiment_agreement_rate == pytest.approx(0.5)  # 1 of 2 match


def test_compute_shadow_coverage_error_records(tmp_path: Path) -> None:
    """Records with null divergence/deviations counted as error_records."""
    f = tmp_path / "shadow.jsonl"
    error_line = json.dumps({"document_id": "d3", "companion_result": None, "divergence": None})
    _write_jsonl(f, [error_line, _batch_shadow_line()])
    report = compute_shadow_coverage(f)
    assert report.total_records == 2
    assert report.error_records == 1
    assert report.valid_records == 1


def test_compute_shadow_coverage_file_not_found(tmp_path: Path) -> None:
    """FileNotFoundError raised when shadow file does not exist."""
    with pytest.raises(FileNotFoundError):
        compute_shadow_coverage(tmp_path / "missing.jsonl")


# ── build_distillation_report ─────────────────────────────────────────────────


def test_build_distillation_report_with_teacher_candidate(tmp_path: Path) -> None:
    """Builds report from teacher + candidate JSONL files."""
    teacher_f = tmp_path / "teacher.jsonl"
    candidate_f = tmp_path / "candidate.jsonl"
    teacher_f.write_text(json.dumps(_make_teacher_row("d1")) + "\n", encoding="utf-8")
    candidate_f.write_text(json.dumps(_make_candidate_row("d1")) + "\n", encoding="utf-8")

    inputs = DistillationInputs(
        teacher_path=str(teacher_f),
        candidate_path=str(candidate_f),
        dataset_type="internal_benchmark",
    )
    report = build_distillation_report(inputs)
    assert isinstance(report, DistillationReadinessReport)
    assert report.evaluation.paired_count == 1
    assert report.shadow_coverage is None


def test_build_distillation_report_with_eval_report_path(tmp_path: Path) -> None:
    """Loads pre-computed EvaluationReport JSON — skips compare_datasets()."""
    eval_file = tmp_path / "eval.json"
    eval_file.write_text(json.dumps(_make_eval_report_dict()), encoding="utf-8")

    inputs = DistillationInputs(eval_report_path=str(eval_file))
    report = build_distillation_report(inputs)
    assert report.evaluation.metrics.sentiment_agreement == pytest.approx(0.9)
    assert report.evaluation.paired_count == 9


def test_build_distillation_report_with_shadow(tmp_path: Path) -> None:
    """Shadow coverage included when shadow_path is set."""
    eval_file = tmp_path / "eval.json"
    eval_file.write_text(json.dumps(_make_eval_report_dict()), encoding="utf-8")

    shadow_f = tmp_path / "shadow.jsonl"
    _write_jsonl(shadow_f, [_batch_shadow_line(), _live_shadow_line()])

    inputs = DistillationInputs(
        eval_report_path=str(eval_file),
        shadow_path=str(shadow_f),
    )
    report = build_distillation_report(inputs)
    assert report.shadow_coverage is not None
    assert report.shadow_coverage.total_records == 2


def test_build_distillation_report_missing_inputs_raises() -> None:
    """ValueError raised when neither eval_report_path nor teacher+candidate set."""
    with pytest.raises(ValueError, match="eval_report_path"):
        build_distillation_report(DistillationInputs())


# ── save_distillation_manifest ────────────────────────────────────────────────


def test_save_distillation_manifest_creates_valid_json(tmp_path: Path) -> None:
    """Saved manifest is valid JSON with expected keys."""
    eval_file = tmp_path / "eval.json"
    eval_file.write_text(json.dumps(_make_eval_report_dict()), encoding="utf-8")
    inputs = DistillationInputs(eval_report_path=str(eval_file))
    report = build_distillation_report(inputs)

    manifest_path = tmp_path / "manifest.json"
    result_path = save_distillation_manifest(report, manifest_path)

    assert result_path.exists()
    parsed = json.loads(result_path.read_text(encoding="utf-8"))
    assert "generated_at" in parsed
    assert "evaluation" in parsed
    assert "promotion_validation" in parsed
    assert "is_promotable" in parsed["promotion_validation"]


def test_save_distillation_manifest_creates_parent_dirs(tmp_path: Path) -> None:
    """save_distillation_manifest creates missing parent directories."""
    eval_file = tmp_path / "eval.json"
    eval_file.write_text(json.dumps(_make_eval_report_dict()), encoding="utf-8")
    inputs = DistillationInputs(eval_report_path=str(eval_file))
    report = build_distillation_report(inputs)

    deep_path = tmp_path / "a" / "b" / "c" / "manifest.json"
    save_distillation_manifest(report, deep_path)
    assert deep_path.exists()
