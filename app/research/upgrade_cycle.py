"""Companion upgrade cycle report orchestration.

[EXPERIMENTAL — NO ACTIVE MODEL]
Part of the companion model training pipeline. Summarizes upgrade cycle
status when a new companion model version is being evaluated for promotion.
NOT in the default operator workflow — requires an active companion model
and promotion artifacts to be meaningful.

Invariants: docs/contracts.md §25, I-75-I-79.
Pure read/summarize helpers only — no training, no inference, no routing changes,
no external API calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.research.evaluation import load_saved_evaluation_report, validate_promotion

UpgradeCycleStatus = Literal[
    "prepared",
    "training_recorded",
    "evaluated",
    "compared",
    "promotable",
    "promoted_manual",
]


@dataclass
class UpgradeCycleReport:
    """Operator-facing status summary of one companion upgrade cycle."""

    teacher_dataset_path: str
    training_job_record_path: str | None = None
    evaluation_report_path: str | None = None
    comparison_report_path: str | None = None
    promotion_readiness: bool = False
    promotion_record_path: str | None = None
    status: UpgradeCycleStatus = "prepared"
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "upgrade_cycle_report",
            "generated_at": datetime.now(UTC).isoformat(),
            "status": self.status,
            "teacher_dataset_path": self.teacher_dataset_path,
            "training_job_record_path": self.training_job_record_path,
            "evaluation_report_path": self.evaluation_report_path,
            "comparison_report_path": self.comparison_report_path,
            "promotion_readiness": self.promotion_readiness,
            "promotion_record_path": self.promotion_record_path,
            "notes": list(self.notes),
        }


def derive_cycle_status(
    teacher_dataset_path: str,
    training_job_record_path: str | None,
    evaluation_report_path: str | None,
    comparison_report_path: str | None,
    promotion_record_path: str | None,
    promotion_readiness: bool,
) -> UpgradeCycleStatus:
    """Derive lifecycle status from explicit artifact presence only."""
    del teacher_dataset_path

    if promotion_record_path is not None and Path(promotion_record_path).exists():
        return "promoted_manual"
    if promotion_readiness:
        return "promotable"
    if comparison_report_path is not None and Path(comparison_report_path).exists():
        return "compared"
    if evaluation_report_path is not None and Path(evaluation_report_path).exists():
        return "evaluated"
    if training_job_record_path is not None and Path(training_job_record_path).exists():
        return "training_recorded"
    return "prepared"


def build_upgrade_cycle_report(
    teacher_dataset_path: str | Path,
    *,
    training_job_record_path: str | Path | None = None,
    evaluation_report_path: str | Path | None = None,
    comparison_report_path: str | Path | None = None,
    promotion_record_path: str | Path | None = None,
    notes: list[str] | None = None,
) -> UpgradeCycleReport:
    """Build an upgrade cycle report from existing artifact paths."""
    teacher_path = _resolve_existing_path(
        teacher_dataset_path,
        label="Teacher dataset",
        required=True,
    )
    if teacher_path is None:
        raise FileNotFoundError("Teacher dataset path is required.")
    training_job_path = _resolve_existing_path(
        training_job_record_path,
        label="Training job record",
    )
    evaluation_path = _resolve_existing_path(
        evaluation_report_path,
        label="Evaluation report",
    )
    comparison_path = _resolve_existing_path(
        comparison_report_path,
        label="Comparison report",
    )
    promotion_path = _resolve_existing_path(
        promotion_record_path,
        label="Promotion record",
    )

    if training_job_path is not None:
        _validate_record_type(
            training_job_path,
            expected_types={"training_job"},
            label="Training job record",
        )

    promotion_payload: dict[str, object] | None = None
    if promotion_path is not None:
        promotion_payload = _validate_record_type(
            promotion_path,
            expected_types={"companion_promotion"},
            label="Promotion record",
        )
        linked_comparison_path = _resolve_linked_comparison_path(
            promotion_payload,
            label="Promotion record",
        )
        if linked_comparison_path is not None:
            if comparison_path is None:
                comparison_path = linked_comparison_path
            elif comparison_path.resolve() != linked_comparison_path.resolve():
                raise ValueError(
                    "Promotion record comparison_report_path mismatch: "
                    f"{linked_comparison_path.resolve()} != {comparison_path.resolve()}"
                )

    comparison_note: str | None = None
    if comparison_path is not None:
        comparison_note = _build_comparison_note(comparison_path)

    promotion_readiness = False
    if evaluation_path is not None:
        evaluation_report = load_saved_evaluation_report(evaluation_path)
        promotion_readiness = validate_promotion(evaluation_report.metrics).is_promotable

    status = derive_cycle_status(
        str(teacher_path.resolve()),
        str(training_job_path.resolve()) if training_job_path is not None else None,
        str(evaluation_path.resolve()) if evaluation_path is not None else None,
        str(comparison_path.resolve()) if comparison_path is not None else None,
        str(promotion_path.resolve()) if promotion_path is not None else None,
        promotion_readiness,
    )

    generated_notes = [_build_status_note(status, promotion_readiness)]
    if comparison_note is not None:
        generated_notes.append(comparison_note)
    if notes:
        generated_notes.extend(notes)

    return UpgradeCycleReport(
        teacher_dataset_path=str(teacher_path.resolve()),
        training_job_record_path=(
            str(training_job_path.resolve()) if training_job_path is not None else None
        ),
        evaluation_report_path=(
            str(evaluation_path.resolve()) if evaluation_path is not None else None
        ),
        comparison_report_path=(
            str(comparison_path.resolve()) if comparison_path is not None else None
        ),
        promotion_readiness=promotion_readiness,
        promotion_record_path=(
            str(promotion_path.resolve()) if promotion_path is not None else None
        ),
        status=status,
        notes=generated_notes,
    )


def save_upgrade_cycle_report(
    report: UpgradeCycleReport,
    output_path: Path | str,
) -> Path:
    """Persist an upgrade cycle report as structured JSON."""
    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(report.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved_path


def _resolve_existing_path(
    path_value: str | Path | None,
    *,
    label: str,
    required: bool = False,
) -> Path | None:
    if path_value is None:
        if required:
            raise FileNotFoundError(f"{label} path is required.")
        return None

    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def _load_json_object(path: Path, *, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"{label} is not valid JSON: {path}") from err

    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _validate_record_type(
    path: Path,
    *,
    expected_types: set[str],
    label: str,
) -> dict[str, object]:
    payload = _load_json_object(path, label=label)
    record_type = payload.get("record_type")
    if record_type not in expected_types:
        allowed = ", ".join(sorted(expected_types))
        raise ValueError(f"{label} must have record_type in {{{allowed}}}: {path}")
    return payload


def _resolve_linked_comparison_path(
    payload: dict[str, object],
    *,
    label: str,
) -> Path | None:
    raw_path = payload.get("comparison_report_path")
    if raw_path is None:
        return None

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{label} comparison_report_path must be a non-empty string.")

    return _resolve_existing_path(
        raw_path,
        label="Comparison report linked from promotion record",
    )


def _build_comparison_note(path: Path) -> str:
    payload = _load_json_object(path, label="Comparison report")
    report_type = payload.get("report_type")
    if report_type not in {"evaluation_report_comparison", "evaluation_comparison"}:
        raise ValueError(
            "Comparison report must have report_type "
            f"'evaluation_report_comparison': {path}"
        )

    regression_summary = payload.get("regression_summary")
    if not isinstance(regression_summary, dict):
        raise ValueError(f"Comparison report missing regression_summary: {path}")

    has_regression = regression_summary.get("has_regression")
    regressed_metrics = regression_summary.get("regressed_metrics", [])
    improved_metrics = regression_summary.get("improved_metrics", [])
    regressed_gates = regression_summary.get("regressed_gates", [])
    improved_gates = regression_summary.get("improved_gates", [])

    if not isinstance(has_regression, bool):
        raise ValueError(f"Comparison report regression_summary.has_regression invalid: {path}")
    if not isinstance(regressed_metrics, list) or any(
        not isinstance(item, str) for item in regressed_metrics
    ):
        raise ValueError(
            "Comparison report regression_summary.regressed_metrics must be list[str]: "
            f"{path}"
        )
    if not isinstance(improved_metrics, list) or any(
        not isinstance(item, str) for item in improved_metrics
    ):
        raise ValueError(
            "Comparison report regression_summary.improved_metrics must be list[str]: "
            f"{path}"
        )
    if not isinstance(regressed_gates, list) or any(
        not isinstance(item, str) for item in regressed_gates
    ):
        raise ValueError(
            "Comparison report regression_summary.regressed_gates must be list[str]: "
            f"{path}"
        )
    if not isinstance(improved_gates, list) or any(
        not isinstance(item, str) for item in improved_gates
    ):
        raise ValueError(
            "Comparison report regression_summary.improved_gates must be list[str]: "
            f"{path}"
        )

    metric_fragment = ", ".join(regressed_metrics) if regressed_metrics else "none"
    regression_label = "detected" if has_regression else "not detected"
    return (
        "Comparison summary: regression "
        f"{regression_label}; regressed_metrics={len(regressed_metrics)}, "
        f"improved_metrics={len(improved_metrics)}, regressed_gates={len(regressed_gates)}, "
        f"improved_gates={len(improved_gates)}, details={metric_fragment}."
    )


def _build_status_note(
    status: UpgradeCycleStatus,
    promotion_readiness: bool,
) -> str:
    readiness = "passes G1-G6" if promotion_readiness else "does not yet pass G1-G6"
    if status == "promoted_manual":
        return (
            "Result summary: promotion record present; the cycle is manually promoted. "
            "Provider routing remains a separate operator action."
        )
    if status == "promotable":
        return (
            "Result summary: candidate evaluation is present and passes G1-G6. "
            "The cycle is ready for manual promotion only."
        )
    if status == "compared":
        return (
            "Result summary: comparison artifact is present and the candidate "
            f"{readiness}. Manual gate review remains required."
        )
    if status == "evaluated":
        return (
            "Result summary: evaluation artifact is present and the candidate "
            f"{readiness}. Comparison and promotion audit steps remain open."
        )
    if status == "training_recorded":
        return (
            "Result summary: training job record is present. "
            "A post-training evaluation artifact has not been supplied yet."
        )
    return (
        "Result summary: teacher dataset is present. "
        "Training intent has not been recorded yet."
    )
