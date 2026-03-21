"""Training job and post-training evaluation artifacts.

Sprint 12 - controlled training intent and evaluation linkage.
Contract reference: docs/sprint12_training_job_contract.md
Invariants: docs/contracts.md §23, I-63-I-69.

No training, no model inference, no external API calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


@dataclass
class TrainingJobRecord:
    """Immutable pre-training manifest.

    Records training intent only. Does NOT train a model.
    """

    teacher_dataset: str
    model_base: str
    target_model_id: str
    training_format: str = "openai_chat"
    row_count: int = 0
    job_id: str = field(default_factory=lambda: str(uuid4()))
    tuning_artifact_path: str | None = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.training_format = "openai_chat"

    def to_json_dict(self) -> dict[str, object]:
        return {
            "record_type": "training_job",
            "generated_at": datetime.now(UTC).isoformat(),
            "job_id": self.job_id,
            "teacher_dataset": self.teacher_dataset,
            "model_base": self.model_base,
            "target_model_id": self.target_model_id,
            "training_format": self.training_format,
            "row_count": self.row_count,
            "tuning_artifact_path": self.tuning_artifact_path,
            "status": "pending",
            "notes": list(self.notes),
        }


@dataclass
class PostTrainingEvaluationSpec:
    """Link a training job record to its post-training evaluation output."""

    training_job_path: str
    trained_model_id: str
    trained_model_endpoint: str
    eval_report_path: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "record_type": "post_training_eval",
            "generated_at": datetime.now(UTC).isoformat(),
            "training_job_path": self.training_job_path,
            "trained_model_id": self.trained_model_id,
            "trained_model_endpoint": self.trained_model_endpoint,
            "eval_report_path": self.eval_report_path,
            "notes": list(self.notes),
        }


def save_training_job_record(
    output_path: Path | str,
    *,
    teacher_dataset: Path | str,
    model_base: str,
    target_model_id: str,
    row_count: int,
    tuning_artifact_path: Path | str | None = None,
    notes: list[str] | None = None,
) -> Path:
    """Write an immutable training job manifest."""
    teacher_path = Path(teacher_dataset)
    if not teacher_path.exists():
        raise FileNotFoundError(f"Teacher dataset not found: {teacher_path}")

    if row_count < 1:
        raise ValueError("row_count must be >= 1 for a training job record")

    normalized_target_model_id = target_model_id.strip()
    if not normalized_target_model_id:
        raise ValueError("target_model_id must not be blank")

    normalized_model_base = model_base.strip()
    if not normalized_model_base:
        raise ValueError("model_base must not be blank")

    resolved_tuning_artifact = None
    if tuning_artifact_path is not None:
        tuning_path = Path(tuning_artifact_path)
        if not tuning_path.exists():
            raise FileNotFoundError(f"Tuning artifact not found: {tuning_path}")
        resolved_tuning_artifact = str(tuning_path.resolve())

    record = TrainingJobRecord(
        teacher_dataset=str(teacher_path.resolve()),
        model_base=normalized_model_base,
        target_model_id=normalized_target_model_id,
        row_count=row_count,
        tuning_artifact_path=resolved_tuning_artifact,
        notes=list(notes or []),
    )

    resolved_output = Path(output_path)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(record.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved_output


def save_post_training_eval_spec(
    output_path: Path | str,
    *,
    training_job_path: Path | str,
    trained_model_id: str,
    trained_model_endpoint: str,
    eval_report_path: Path | str | None = None,
    notes: list[str] | None = None,
) -> Path:
    """Write a post-training evaluation linkage artifact."""
    resolved_training_job = Path(training_job_path)
    if not resolved_training_job.exists():
        raise FileNotFoundError(f"Training job record not found: {resolved_training_job}")

    normalized_trained_model_id = trained_model_id.strip()
    if not normalized_trained_model_id:
        raise ValueError("trained_model_id must not be blank")

    normalized_endpoint = trained_model_endpoint.strip()
    if not normalized_endpoint:
        raise ValueError("trained_model_endpoint must not be blank")

    resolved_eval_report = None
    if eval_report_path is not None:
        eval_path = Path(eval_report_path)
        if not eval_path.exists():
            raise FileNotFoundError(f"Evaluation report not found: {eval_path}")
        resolved_eval_report = str(eval_path.resolve())

    record = PostTrainingEvaluationSpec(
        training_job_path=str(resolved_training_job.resolve()),
        trained_model_id=normalized_trained_model_id,
        trained_model_endpoint=normalized_endpoint,
        eval_report_path=resolved_eval_report,
        notes=list(notes or []),
    )

    resolved_output = Path(output_path)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(record.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved_output
