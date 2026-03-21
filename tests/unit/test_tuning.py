import json

import pytest

from app.research.tuning import (
    save_promotion_record,
    save_tuning_artifact,
)

# ---------------------------------------------------------------------------
# TuningArtifact
# ---------------------------------------------------------------------------


def test_save_tuning_artifact_creates_valid_json(tmp_path) -> None:
    path = save_tuning_artifact(
        tmp_path / "manifest.json",
        teacher_dataset="/a/teacher.jsonl",
        model_base="llama3.2:3b",
        row_count=50,
    )

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["artifact_type"] == "tuning_manifest"
    assert data["model_base"] == "llama3.2:3b"
    assert data["training_format"] == "openai_chat"
    assert data["row_count"] == 50
    assert data["evaluation_report"] is None
    assert "generated_at" in data
    assert isinstance(data["notes"], list)


def test_save_tuning_artifact_with_eval_report(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")

    path = save_tuning_artifact(
        tmp_path / "manifest.json",
        teacher_dataset="/a/teacher.jsonl",
        model_base="kai-v1",
        row_count=10,
        evaluation_report=eval_report,
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["evaluation_report"] is not None
    assert data["evaluation_report"].endswith("report.json")


def test_save_tuning_artifact_creates_parent_dirs(tmp_path) -> None:
    nested = tmp_path / "deep" / "nested" / "manifest.json"
    path = save_tuning_artifact(
        nested,
        teacher_dataset="/a/teacher.jsonl",
        model_base="llama3.2:3b",
        row_count=5,
    )
    assert path.exists()


def test_tuning_artifact_training_format_is_always_openai_chat(tmp_path) -> None:
    path = save_tuning_artifact(
        tmp_path / "m.json",
        teacher_dataset="/a/teacher.jsonl",
        model_base="any-model",
        row_count=1,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["training_format"] == "openai_chat"


# ---------------------------------------------------------------------------
# PromotionRecord
# ---------------------------------------------------------------------------


def test_save_promotion_record_creates_valid_json(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")

    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="kai-analyst-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        operator_note="Operator approved after review",
    )

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["record_type"] == "companion_promotion"
    assert data["promoted_model"] == "kai-analyst-v1"
    assert data["promoted_endpoint"] == "http://localhost:11434"
    assert "reversal_instructions" in data
    assert data["tuning_artifact"] is None
    assert data["gates_summary"] is None
    assert data["training_job_record"] is None
    assert "generated_at" in data
    assert data["operator_note"] == "Operator approved after review"


def test_save_promotion_record_with_tuning_artifact(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")
    tuning = tmp_path / "manifest.json"
    tuning.write_text(
        json.dumps({"evaluation_report": str(eval_report.resolve())}),
        encoding="utf-8",
    )

    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="kai-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        tuning_artifact=tuning,
        operator_note="Reviewed and approved",
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tuning_artifact"] is not None
    assert data["tuning_artifact"].endswith("manifest.json")


def test_save_promotion_record_with_training_job_record(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    training_job = tmp_path / "training_job.json"
    eval_report.write_text("{}", encoding="utf-8")
    training_job.write_text("{}", encoding="utf-8")

    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="kai-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        operator_note="Reviewed and approved",
        training_job_record=training_job,
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["training_job_record"] == str(training_job.resolve())


def test_save_promotion_record_with_comparison_report(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    comparison_report = tmp_path / "comparison.json"
    eval_report.write_text("{}", encoding="utf-8")
    comparison_report.write_text(
        json.dumps(
            {
                "report_type": "evaluation_report_comparison",
                "regression_summary": {"has_regression": False},
            }
        ),
        encoding="utf-8",
    )

    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="kai-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        operator_note="Reviewed and approved",
        comparison_report=comparison_report,
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["comparison_report_path"] == str(comparison_report.resolve())


def test_save_promotion_record_blank_note_raises(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="operator_note"):
        save_promotion_record(
            tmp_path / "promo.json",
            promoted_model="m",
            promoted_endpoint="http://localhost:11434",
            evaluation_report=eval_report,
            operator_note="   ",
        )


def test_save_promotion_record_whitespace_only_note_raises(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="operator_note"):
        save_promotion_record(
            tmp_path / "promo.json",
            promoted_model="m",
            promoted_endpoint="http://localhost:11434",
            evaluation_report=eval_report,
            operator_note="\t\n",
        )


def test_save_promotion_record_missing_report_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        save_promotion_record(
            tmp_path / "promo.json",
            promoted_model="m",
            promoted_endpoint="http://localhost:11434",
            evaluation_report=tmp_path / "nonexistent.json",
            operator_note="ok",
        )


def test_save_promotion_record_missing_training_job_record_raises(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="Training job record not found"):
        save_promotion_record(
            tmp_path / "promo.json",
            promoted_model="m",
            promoted_endpoint="http://localhost:11434",
            evaluation_report=eval_report,
            operator_note="ok",
            training_job_record=tmp_path / "missing_training_job.json",
        )


def test_save_promotion_record_missing_comparison_report_raises(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="Comparison report not found"):
        save_promotion_record(
            tmp_path / "promo.json",
            promoted_model="m",
            promoted_endpoint="http://localhost:11434",
            evaluation_report=eval_report,
            operator_note="ok",
            comparison_report=tmp_path / "missing_comparison.json",
        )


def test_save_promotion_record_invalid_comparison_report_raises(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    comparison_report = tmp_path / "comparison.json"
    eval_report.write_text("{}", encoding="utf-8")
    comparison_report.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="Comparison report is not valid JSON"):
        save_promotion_record(
            tmp_path / "promo.json",
            promoted_model="m",
            promoted_endpoint="http://localhost:11434",
            evaluation_report=eval_report,
            operator_note="ok",
            comparison_report=comparison_report,
        )


def test_promotion_record_reversal_instructions_hardcoded(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")

    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="m",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        operator_note="approved",
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "APP_LLM_PROVIDER" in data["reversal_instructions"]


# ---------------------------------------------------------------------------
# Sprint 9 - gates_summary + Artifact Linkage (I-47, I-49)
# ---------------------------------------------------------------------------


def test_save_promotion_record_embeds_gates_summary(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")
    gates = {
        "sentiment_pass": True,
        "priority_pass": True,
        "relevance_pass": True,
        "impact_pass": True,
        "tag_overlap_pass": True,
        "false_actionable_pass": True,
    }

    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="kai-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        operator_note="Approved",
        gates_summary=gates,
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["gates_summary"] == gates
    assert data["gates_summary"]["false_actionable_pass"] is True


def test_save_promotion_record_null_gates_summary(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")

    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="kai-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        operator_note="Approved",
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["gates_summary"] is None


def test_save_promotion_record_tuning_artifact_linkage_success(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")
    tuning_artifact = tmp_path / "manifest.json"
    tuning_artifact.write_text(
        json.dumps({"evaluation_report": str(eval_report.resolve())}),
        encoding="utf-8",
    )

    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="kai-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        tuning_artifact=tuning_artifact,
        operator_note="Approved",
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tuning_artifact"] == str(tuning_artifact.resolve())


def test_save_promotion_record_tuning_artifact_linkage_mismatch(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")
    other_report = tmp_path / "other.json"
    other_report.write_text("{}", encoding="utf-8")
    tuning_artifact = tmp_path / "manifest.json"
    tuning_artifact.write_text(
        json.dumps({"evaluation_report": str(other_report.resolve())}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="mismatch"):
        save_promotion_record(
            tmp_path / "promo.json",
            promoted_model="kai-v1",
            promoted_endpoint="http://localhost:11434",
            evaluation_report=eval_report,
            tuning_artifact=tuning_artifact,
            operator_note="Approved",
        )


def test_save_promotion_record_tuning_artifact_missing_eval_report_field_raises(
    tmp_path,
) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")
    tuning_artifact = tmp_path / "manifest.json"
    tuning_artifact.write_text(
        json.dumps({"model_base": "llama3", "row_count": 10}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing evaluation_report linkage"):
        save_promotion_record(
            tmp_path / "promo.json",
            promoted_model="kai-v1",
            promoted_endpoint="http://localhost:11434",
            evaluation_report=eval_report,
            tuning_artifact=tuning_artifact,
            operator_note="Approved",
        )
