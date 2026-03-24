"""Tuning artifact and promotion record management.

[EXPERIMENTAL — NO ACTIVE MODEL]
Part of the companion model training pipeline. Manages tuning artifacts and
promotion audit trail for a future fine-tuning cycle. NOT in the default
operator workflow. Activation requires an active companion model endpoint
and explicit CLI invocation (prepare-tuning-artifact, record-promotion).

Invariants: docs/contracts.md §19, I-40-I-49.
No imports from evaluation.py. No circular dependencies.
No training, no model inference, no external API calls.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class TuningArtifact:
    """Manifest describing a dataset ready for external fine-tuning.

    This is a record only - it does NOT train a model.
    """

    teacher_dataset: str
    model_base: str
    training_format: str  # always "openai_chat"
    row_count: int
    evaluation_report: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "artifact_type": "tuning_manifest",
            "generated_at": datetime.now(UTC).isoformat(),
            "teacher_dataset": self.teacher_dataset,
            "model_base": self.model_base,
            "training_format": self.training_format,
            "row_count": self.row_count,
            "evaluation_report": self.evaluation_report,
            "notes": list(self.notes),
        }


@dataclass
class PromotionRecord:
    """Immutable audit record of a manual companion promotion decision.

    Writing this record does NOT change provider routing.
    Routing is controlled exclusively by env vars (I-42).
    """

    promoted_model: str
    promoted_endpoint: str
    evaluation_report: str
    operator_note: str
    tuning_artifact: str | None = None
    gates_summary: dict[str, bool] | None = None
    training_job_record: str | None = None
    comparison_report_path: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "record_type": "companion_promotion",
            "generated_at": datetime.now(UTC).isoformat(),
            "promoted_model": self.promoted_model,
            "promoted_endpoint": self.promoted_endpoint,
            "evaluation_report": self.evaluation_report,
            "tuning_artifact": self.tuning_artifact,
            "operator_note": self.operator_note,
            "gates_summary": (
                self.gates_summary.copy() if self.gates_summary is not None else None
            ),
            "training_job_record": self.training_job_record,
            "comparison_report_path": self.comparison_report_path,
            "reversal_instructions": ("Set APP_LLM_PROVIDER to previous value to revert companion"),
        }


def save_tuning_artifact(
    output_path: Path | str,
    *,
    teacher_dataset: Path | str,
    model_base: str,
    row_count: int,
    evaluation_report: Path | str | None = None,
    notes: list[str] | None = None,
) -> Path:
    """Write a training-ready manifest for an external fine-tuning process.

    Does NOT train a model. Does NOT call any training API.
    This is a pre-training record only.

    Contract reference: docs/tuning_promotion_contract.md §TuningArtifact
    """
    artifact = TuningArtifact(
        teacher_dataset=str(Path(teacher_dataset).resolve()),
        model_base=model_base,
        training_format="openai_chat",
        row_count=row_count,
        evaluation_report=(str(Path(evaluation_report).resolve()) if evaluation_report else None),
        notes=list(notes or []),
    )
    resolved = Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(artifact.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved


def save_promotion_record(
    output_path: Path | str,
    *,
    promoted_model: str,
    promoted_endpoint: str,
    evaluation_report: Path | str,
    tuning_artifact: Path | str | None = None,
    operator_note: str,
    gates_summary: dict[str, bool] | None = None,
    training_job_record: Path | str | None = None,
    comparison_report: Path | str | None = None,
) -> Path:
    """Write an immutable promotion record for audit trail.

    Does NOT change provider routing. Does NOT modify any system state.
    The operator must change APP_LLM_PROVIDER separately.

    Raises:
        ValueError: if operator_note is blank or if tuning artifact linkage fails
        FileNotFoundError: if evaluation_report path does not exist (I-45)

    Contract reference: docs/tuning_promotion_contract.md §PromotionRecord
    Invariants: I-40-I-49
    """
    if not operator_note.strip():
        raise ValueError(
            "operator_note must not be blank - explicit acknowledgement required (I-43)"
        )

    eval_path = Path(evaluation_report)
    if not eval_path.exists():
        raise FileNotFoundError(
            f"Evaluation report not found: {eval_path} - promotion requires a valid report (I-45)"
        )

    resolved_eval_path = eval_path.resolve()
    resolved_tuning_artifact = _resolve_tuning_artifact_linkage(
        resolved_eval_path,
        tuning_artifact,
    )
    resolved_training_job_record = _resolve_training_job_record(training_job_record)

    resolved_comparison_report = _resolve_comparison_report(comparison_report)

    record = PromotionRecord(
        promoted_model=promoted_model,
        promoted_endpoint=promoted_endpoint,
        evaluation_report=str(resolved_eval_path),
        tuning_artifact=resolved_tuning_artifact,
        operator_note=operator_note.strip(),
        gates_summary=gates_summary.copy() if gates_summary is not None else None,
        training_job_record=resolved_training_job_record,
        comparison_report_path=resolved_comparison_report,
    )
    resolved = Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(record.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved


def _resolve_tuning_artifact_linkage(
    evaluation_report: Path,
    tuning_artifact: Path | str | None,
) -> str | None:
    """Validate that a tuning artifact links to the same evaluation report."""
    if tuning_artifact is None:
        return None

    artifact_path = Path(tuning_artifact)
    if not artifact_path.exists():
        raise ValueError(
            f"Tuning artifact not found: {artifact_path} "
            "- promotion requires a valid artifact linkage (I-49)"
        )

    try:
        artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"Tuning artifact is not valid JSON: {artifact_path} (I-49)") from err

    if not isinstance(artifact_payload, dict):
        raise ValueError(f"Tuning artifact must be a JSON object: {artifact_path} (I-49)")

    artifact_report = artifact_payload.get("evaluation_report")
    if not isinstance(artifact_report, str) or not artifact_report.strip():
        raise ValueError(
            f"Tuning artifact missing evaluation_report linkage: {artifact_path} (I-49)"
        )

    resolved_artifact_report = Path(artifact_report).resolve()
    if resolved_artifact_report != evaluation_report:
        raise ValueError(
            "Tuning artifact evaluation_report mismatch: "
            f"{resolved_artifact_report} != {evaluation_report} (I-49)"
        )

    return str(artifact_path.resolve())


def _resolve_training_job_record(training_job_record: Path | str | None) -> str | None:
    """Resolve an optional training job record path for promotion audit linkage."""
    if training_job_record is None:
        return None

    training_job_path = Path(training_job_record)
    if not training_job_path.exists():
        raise FileNotFoundError(
            "Training job record not found: "
            f"{training_job_path} - promotion requires a valid audit link"
        )

    return str(training_job_path.resolve())


def _resolve_comparison_report(comparison_report: Path | str | None) -> str | None:
    """Resolve and validate an optional comparison report audit link."""
    if comparison_report is None:
        return None

    comparison_path = Path(comparison_report)
    if not comparison_path.exists():
        raise FileNotFoundError(
            "Comparison report not found: "
            f"{comparison_path} - promotion requires a valid comparison audit link"
        )

    try:
        payload = json.loads(comparison_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"Comparison report is not valid JSON: {comparison_path}") from err

    if not isinstance(payload, dict):
        raise ValueError(f"Comparison report must be a JSON object: {comparison_path}")

    report_type = payload.get("report_type")
    if report_type not in {"evaluation_report_comparison", "evaluation_comparison"}:
        raise ValueError(
            "Comparison report must have report_type "
            "'evaluation_report_comparison' or 'evaluation_comparison': "
            f"{comparison_path}"
        )

    regression_summary = payload.get("regression_summary")
    if not isinstance(regression_summary, dict):
        raise ValueError(f"Comparison report missing regression_summary: {comparison_path}")

    return str(comparison_path.resolve())
