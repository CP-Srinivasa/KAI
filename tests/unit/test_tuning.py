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
    assert "generated_at" in data
    assert data["operator_note"] == "Operator approved after review"


def test_save_promotion_record_with_tuning_artifact(tmp_path) -> None:
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}", encoding="utf-8")
    tuning = tmp_path / "manifest.json"
    tuning.write_text("{}", encoding="utf-8")

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
