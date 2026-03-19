"""Tuning artifact and promotion record management.

Sprint 8 — companion tuning flow and manual promotion gate.
Contract reference: docs/tuning_promotion_contract.md
Invariants: docs/contracts.md §19, I-40–I-45.

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

    This is a record only — it does NOT train a model.
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

    def to_json_dict(self) -> dict[str, object]:
        return {
            "record_type": "companion_promotion",
            "generated_at": datetime.now(UTC).isoformat(),
            "promoted_model": self.promoted_model,
            "promoted_endpoint": self.promoted_endpoint,
            "evaluation_report": self.evaluation_report,
            "tuning_artifact": self.tuning_artifact,
            "operator_note": self.operator_note,
            "reversal_instructions": (
                "Set APP_LLM_PROVIDER to previous value to revert companion"
            ),
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
        evaluation_report=(
            str(Path(evaluation_report).resolve()) if evaluation_report else None
        ),
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
) -> Path:
    """Write an immutable promotion record for audit trail.

    Does NOT change provider routing. Does NOT modify any system state.
    The operator must change APP_LLM_PROVIDER separately.

    Raises:
        ValueError: if operator_note is blank (I-43)
        FileNotFoundError: if evaluation_report path does not exist (I-45)

    Contract reference: docs/tuning_promotion_contract.md §PromotionRecord
    Invariants: I-40–I-45
    """
    if not operator_note.strip():
        raise ValueError(
            "operator_note must not be blank — explicit acknowledgement required (I-43)"
        )
    eval_path = Path(evaluation_report)
    if not eval_path.exists():
        raise FileNotFoundError(
            f"Evaluation report not found: {eval_path} "
            "— promotion requires a valid report (I-45)"
        )
    record = PromotionRecord(
        promoted_model=promoted_model,
        promoted_endpoint=promoted_endpoint,
        evaluation_report=str(eval_path.resolve()),
        tuning_artifact=(
            str(Path(tuning_artifact).resolve()) if tuning_artifact else None
        ),
        operator_note=operator_note.strip(),
    )
    resolved = Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(record.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved
