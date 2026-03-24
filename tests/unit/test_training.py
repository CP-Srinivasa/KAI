import json

import pytest

from app.research.training import (
    PostTrainingEvaluationSpec,
    TrainingJobRecord,
    save_post_training_eval_spec,
    save_training_job_record,
)


def test_training_job_record_to_json_dict_structure(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    teacher_file.write_text("{}", encoding="utf-8")

    record = TrainingJobRecord(
        teacher_dataset=str(teacher_file.resolve()),
        model_base="llama3.2:3b",
        target_model_id="kai-analyst-v1",
        row_count=12,
        job_id="job-123",
    )

    payload = record.to_json_dict()

    assert payload["record_type"] == "training_job"
    assert payload["job_id"] == "job-123"
    assert payload["teacher_dataset"] == str(teacher_file.resolve())
    assert payload["model_base"] == "llama3.2:3b"
    assert payload["target_model_id"] == "kai-analyst-v1"
    assert "generated_at" in payload


def test_training_job_record_status_always_pending(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    teacher_file.write_text("{}", encoding="utf-8")

    record = TrainingJobRecord(
        teacher_dataset=str(teacher_file.resolve()),
        model_base="llama3.2:3b",
        target_model_id="kai-analyst-v1",
        row_count=5,
    )

    assert record.to_json_dict()["status"] == "pending"


def test_training_job_record_training_format_always_openai_chat(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    teacher_file.write_text("{}", encoding="utf-8")

    record = TrainingJobRecord(
        teacher_dataset=str(teacher_file.resolve()),
        model_base="llama3.2:3b",
        target_model_id="kai-analyst-v1",
        training_format="other",
        row_count=5,
    )

    assert record.training_format == "openai_chat"
    assert record.to_json_dict()["training_format"] == "openai_chat"


def test_save_training_job_record_creates_file(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    teacher_file.write_text("{}", encoding="utf-8")

    output = save_training_job_record(
        tmp_path / "training_job.json",
        teacher_dataset=teacher_file,
        model_base="llama3.2:3b",
        target_model_id="kai-analyst-v1",
        row_count=20,
    )

    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["record_type"] == "training_job"
    assert payload["status"] == "pending"
    assert payload["row_count"] == 20


def test_save_training_job_record_raises_on_missing_teacher(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Teacher dataset not found"):
        save_training_job_record(
            tmp_path / "training_job.json",
            teacher_dataset=tmp_path / "missing.jsonl",
            model_base="llama3.2:3b",
            target_model_id="kai-analyst-v1",
            row_count=10,
        )


def test_save_training_job_record_raises_on_zero_rows(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    teacher_file.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="row_count"):
        save_training_job_record(
            tmp_path / "training_job.json",
            teacher_dataset=teacher_file,
            model_base="llama3.2:3b",
            target_model_id="kai-analyst-v1",
            row_count=0,
        )


def test_save_training_job_record_raises_on_empty_target_model_id(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    teacher_file.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="target_model_id"):
        save_training_job_record(
            tmp_path / "training_job.json",
            teacher_dataset=teacher_file,
            model_base="llama3.2:3b",
            target_model_id="   ",
            row_count=10,
        )


def test_save_training_job_record_tuning_artifact_optional(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    teacher_file.write_text("{}", encoding="utf-8")
    tuning_artifact = tmp_path / "tuning_manifest.json"
    tuning_artifact.write_text("{}", encoding="utf-8")

    output = save_training_job_record(
        tmp_path / "training_job.json",
        teacher_dataset=teacher_file,
        model_base="llama3.2:3b",
        target_model_id="kai-analyst-v1",
        row_count=10,
        tuning_artifact_path=tuning_artifact,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["tuning_artifact_path"] == str(tuning_artifact.resolve())


def test_post_training_eval_spec_to_json_dict_structure(tmp_path) -> None:
    job_record = tmp_path / "training_job.json"
    job_record.write_text("{}", encoding="utf-8")

    spec = PostTrainingEvaluationSpec(
        training_job_path=str(job_record.resolve()),
        trained_model_id="kai-analyst-v1",
        trained_model_endpoint="http://localhost:11434",
        eval_report_path=None,
    )

    payload = spec.to_json_dict()
    assert payload["record_type"] == "post_training_eval"
    assert payload["training_job_path"] == str(job_record.resolve())
    assert payload["trained_model_id"] == "kai-analyst-v1"
    assert payload["trained_model_endpoint"] == "http://localhost:11434"


def test_save_post_training_eval_spec_creates_file(tmp_path) -> None:
    job_record = tmp_path / "training_job.json"
    eval_report = tmp_path / "evaluation_report.json"
    job_record.write_text("{}", encoding="utf-8")
    eval_report.write_text("{}", encoding="utf-8")

    output = save_post_training_eval_spec(
        tmp_path / "post_training_eval.json",
        training_job_path=job_record,
        trained_model_id="kai-analyst-v1",
        trained_model_endpoint="http://localhost:11434",
        eval_report_path=eval_report,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["record_type"] == "post_training_eval"
    assert payload["eval_report_path"] == str(eval_report.resolve())


def test_save_post_training_eval_spec_raises_on_missing_job_record(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Training job record not found"):
        save_post_training_eval_spec(
            tmp_path / "post_training_eval.json",
            training_job_path=tmp_path / "missing.json",
            trained_model_id="kai-analyst-v1",
            trained_model_endpoint="http://localhost:11434",
        )


def test_save_post_training_eval_spec_eval_report_optional(tmp_path) -> None:
    job_record = tmp_path / "training_job.json"
    job_record.write_text("{}", encoding="utf-8")

    output = save_post_training_eval_spec(
        tmp_path / "post_training_eval.json",
        training_job_path=job_record,
        trained_model_id="kai-analyst-v1",
        trained_model_endpoint="http://localhost:11434",
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["eval_report_path"] is None


def test_save_post_training_eval_spec_raises_on_blank_endpoint(tmp_path) -> None:
    job_record = tmp_path / "training_job.json"
    job_record.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="trained_model_endpoint"):
        save_post_training_eval_spec(
            tmp_path / "post_training_eval.json",
            training_job_path=job_record,
            trained_model_id="kai-analyst-v1",
            trained_model_endpoint="   ",
        )
