import json

import pytest

from app.research.evaluation import (
    EvaluationMetrics,
    EvaluationReport,
    compare_evaluation_reports,
    save_evaluation_comparison_report,
    save_evaluation_report,
)
from app.research.training import save_training_job_record
from app.research.tuning import save_promotion_record
from app.research.upgrade_cycle import (
    UpgradeCycleReport,
    build_upgrade_cycle_report,
    derive_cycle_status,
    save_upgrade_cycle_report,
)


def _write_dataset_file(path, *, content: str = "{}\n") -> None:
    path.write_text(content, encoding="utf-8")


def _make_metrics(*, promotable: bool) -> EvaluationMetrics:
    if promotable:
        return EvaluationMetrics(
            sentiment_agreement=0.92,
            priority_mae=0.8,
            relevance_mae=0.08,
            impact_mae=0.09,
            tag_overlap_mean=0.45,
            actionable_accuracy=0.95,
            false_actionable_rate=0.01,
            sample_count=5,
            missing_pairs=0,
        )
    return EvaluationMetrics(
        sentiment_agreement=0.70,
        priority_mae=1.9,
        relevance_mae=0.22,
        impact_mae=0.25,
        tag_overlap_mean=0.10,
        actionable_accuracy=0.60,
        false_actionable_rate=0.12,
        sample_count=5,
        missing_pairs=0,
    )


def _write_evaluation_report(tmp_path, name: str, *, promotable: bool):
    teacher_dataset = tmp_path / f"{name}_teacher.jsonl"
    candidate_dataset = tmp_path / f"{name}_candidate.jsonl"
    _write_dataset_file(teacher_dataset)
    _write_dataset_file(candidate_dataset)

    report = EvaluationReport(
        metrics=_make_metrics(promotable=promotable),
        dataset_type="internal_benchmark",
        teacher_count=5,
        baseline_count=5,
        paired_count=5,
    )
    report_path = save_evaluation_report(
        report,
        tmp_path / f"{name}_evaluation_report.json",
        teacher_dataset=teacher_dataset,
        candidate_dataset=candidate_dataset,
    )
    return report, report_path


def _write_training_job_record(tmp_path):
    teacher_dataset = tmp_path / "teacher.jsonl"
    _write_dataset_file(teacher_dataset)
    return save_training_job_record(
        tmp_path / "training_job_record.json",
        teacher_dataset=teacher_dataset,
        model_base="llama3.2:3b",
        target_model_id="kai-upgrade-v1",
        row_count=1,
    )


def _write_comparison_report(tmp_path, *, baseline_promotable: bool, candidate_promotable: bool):
    baseline_report, baseline_path = _write_evaluation_report(
        tmp_path,
        "baseline",
        promotable=baseline_promotable,
    )
    candidate_report, candidate_path = _write_evaluation_report(
        tmp_path,
        "candidate",
        promotable=candidate_promotable,
    )
    comparison = compare_evaluation_reports(baseline_report, candidate_report)
    comparison_path = save_evaluation_comparison_report(
        comparison,
        tmp_path / "comparison_report.json",
        baseline_report=baseline_path,
        candidate_report=candidate_path,
    )
    return comparison_path


def _write_promotion_record(tmp_path, evaluation_report):
    return save_promotion_record(
        tmp_path / "promotion_record.json",
        promoted_model="kai-upgrade-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=evaluation_report,
        operator_note="Manual approval after audit review",
        gates_summary={
            "sentiment_pass": True,
            "priority_pass": True,
            "relevance_pass": True,
            "impact_pass": True,
            "tag_overlap_pass": True,
            "false_actionable_pass": True,
        },
    )


def test_upgrade_cycle_report_to_json_dict_structure(tmp_path) -> None:
    teacher_dataset = tmp_path / "teacher.jsonl"
    _write_dataset_file(teacher_dataset)

    report = UpgradeCycleReport(
        teacher_dataset_path=str(teacher_dataset.resolve()),
        status="prepared",
        notes=["Result summary: teacher dataset is present."],
    )

    payload = report.to_json_dict()
    assert payload["report_type"] == "upgrade_cycle_report"
    assert payload["status"] == "prepared"
    assert payload["teacher_dataset_path"] == str(teacher_dataset.resolve())
    assert payload["promotion_readiness"] is False
    assert payload["promotion_record_path"] is None
    assert payload["notes"] == ["Result summary: teacher dataset is present."]


def test_derive_cycle_status_priority_order(tmp_path) -> None:
    teacher_dataset = tmp_path / "teacher.jsonl"
    training_job = tmp_path / "training.json"
    evaluation_report = tmp_path / "evaluation.json"
    comparison_report = tmp_path / "comparison.json"
    promotion_record = tmp_path / "promotion.json"

    for path in (
        teacher_dataset,
        training_job,
        evaluation_report,
        comparison_report,
        promotion_record,
    ):
        path.write_text("{}", encoding="utf-8")

    status = derive_cycle_status(
        str(teacher_dataset.resolve()),
        str(training_job.resolve()),
        str(evaluation_report.resolve()),
        str(comparison_report.resolve()),
        str(promotion_record.resolve()),
        True,
    )

    assert status == "promoted_manual"


def test_build_upgrade_cycle_report_prepared_status(tmp_path) -> None:
    teacher_dataset = tmp_path / "teacher.jsonl"
    _write_dataset_file(teacher_dataset)

    report = build_upgrade_cycle_report(teacher_dataset)

    assert report.status == "prepared"
    assert report.training_job_record_path is None
    assert report.evaluation_report_path is None
    assert report.promotion_readiness is False
    assert "teacher dataset is present" in report.notes[0].lower()


def test_build_upgrade_cycle_report_training_recorded_status(tmp_path) -> None:
    training_job = _write_training_job_record(tmp_path)

    report = build_upgrade_cycle_report(
        tmp_path / "teacher.jsonl",
        training_job_record_path=training_job,
    )

    assert report.status == "training_recorded"
    assert report.training_job_record_path == str(training_job.resolve())
    assert "training job record is present" in report.notes[0].lower()


def test_build_upgrade_cycle_report_evaluated_status(tmp_path) -> None:
    _write_training_job_record(tmp_path)
    _, evaluation_report = _write_evaluation_report(tmp_path, "candidate", promotable=False)

    report = build_upgrade_cycle_report(
        tmp_path / "teacher.jsonl",
        evaluation_report_path=evaluation_report,
    )

    assert report.status == "evaluated"
    assert report.promotion_readiness is False
    assert "evaluation artifact is present" in report.notes[0].lower()


def test_build_upgrade_cycle_report_compared_status_includes_comparison_summary(tmp_path) -> None:
    _write_training_job_record(tmp_path)
    _, evaluation_report = _write_evaluation_report(tmp_path, "candidate", promotable=False)
    comparison_report = _write_comparison_report(
        tmp_path,
        baseline_promotable=True,
        candidate_promotable=False,
    )

    report = build_upgrade_cycle_report(
        tmp_path / "teacher.jsonl",
        evaluation_report_path=evaluation_report,
        comparison_report_path=comparison_report,
    )

    assert report.status == "compared"
    assert report.comparison_report_path == str(comparison_report.resolve())
    assert any(note.startswith("Comparison summary:") for note in report.notes)


def test_build_upgrade_cycle_report_promotable_status_without_auto_promotion(tmp_path) -> None:
    _write_training_job_record(tmp_path)
    _, evaluation_report = _write_evaluation_report(tmp_path, "candidate", promotable=True)

    report = build_upgrade_cycle_report(
        tmp_path / "teacher.jsonl",
        evaluation_report_path=evaluation_report,
    )

    assert report.status == "promotable"
    assert report.promotion_readiness is True
    assert report.promotion_record_path is None
    assert "ready for manual promotion only" in report.notes[0].lower()


def test_build_upgrade_cycle_report_promoted_manual_status(tmp_path) -> None:
    _write_training_job_record(tmp_path)
    _, evaluation_report = _write_evaluation_report(tmp_path, "candidate", promotable=True)
    promotion_record = _write_promotion_record(tmp_path, evaluation_report)

    report = build_upgrade_cycle_report(
        tmp_path / "teacher.jsonl",
        evaluation_report_path=evaluation_report,
        promotion_record_path=promotion_record,
    )

    assert report.status == "promoted_manual"
    assert report.promotion_record_path == str(promotion_record.resolve())
    assert "manual" in report.notes[0].lower()


def test_build_upgrade_cycle_report_reads_comparison_link_from_promotion_record(tmp_path) -> None:
    _write_training_job_record(tmp_path)
    _, evaluation_report = _write_evaluation_report(tmp_path, "candidate", promotable=True)
    comparison_report = _write_comparison_report(
        tmp_path,
        baseline_promotable=False,
        candidate_promotable=True,
    )
    promotion_record = save_promotion_record(
        tmp_path / "promotion_record.json",
        promoted_model="kai-upgrade-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=evaluation_report,
        operator_note="Manual approval after audit review",
        comparison_report=comparison_report,
        gates_summary={
            "sentiment_pass": True,
            "priority_pass": True,
            "relevance_pass": True,
            "impact_pass": True,
            "tag_overlap_pass": True,
            "false_actionable_pass": True,
        },
    )

    report = build_upgrade_cycle_report(
        tmp_path / "teacher.jsonl",
        evaluation_report_path=evaluation_report,
        promotion_record_path=promotion_record,
    )

    assert report.status == "promoted_manual"
    assert report.comparison_report_path == str(comparison_report.resolve())
    assert any(note.startswith("Comparison summary:") for note in report.notes)


def test_build_upgrade_cycle_report_raises_on_missing_teacher(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Teacher dataset"):
        build_upgrade_cycle_report(tmp_path / "missing_teacher.jsonl")


def test_build_upgrade_cycle_report_raises_on_missing_training_job_if_provided(tmp_path) -> None:
    teacher_dataset = tmp_path / "teacher.jsonl"
    _write_dataset_file(teacher_dataset)

    with pytest.raises(FileNotFoundError, match="Training job record not found"):
        build_upgrade_cycle_report(
            teacher_dataset,
            training_job_record_path=tmp_path / "missing_training_job.json",
        )


def test_build_upgrade_cycle_report_raises_on_invalid_comparison_report(tmp_path) -> None:
    teacher_dataset = tmp_path / "teacher.jsonl"
    _write_dataset_file(teacher_dataset)
    comparison_report = tmp_path / "comparison.json"
    comparison_report.write_text(
        json.dumps({"report_type": "wrong_type"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Comparison report"):
        build_upgrade_cycle_report(
            teacher_dataset,
            comparison_report_path=comparison_report,
        )


def test_save_upgrade_cycle_report_creates_valid_json(tmp_path) -> None:
    teacher_dataset = tmp_path / "teacher.jsonl"
    _write_dataset_file(teacher_dataset)
    report = build_upgrade_cycle_report(teacher_dataset)

    saved = save_upgrade_cycle_report(report, tmp_path / "artifacts" / "upgrade_cycle.json")

    assert saved.exists()
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["report_type"] == "upgrade_cycle_report"
    assert payload["status"] == "prepared"
    assert payload["teacher_dataset_path"] == str(teacher_dataset.resolve())
    assert payload["notes"]
