"""Distillation harness — companion model readiness assessment.

[EXPERIMENTAL — NO ACTIVE MODEL]
Part of the companion model training pipeline. No companion model is currently
trained or deployed. This module is research infrastructure only and is NOT
in the default operator workflow. Activation requires COMPANION_MODEL_ENDPOINT
to be set and explicit CLI invocation (benchmark-companion, check-promotion).

Invariants (I-58–I-62):
- No DB reads, no LLM calls, no network I/O (I-62).
- Shadow data is NEVER used as teacher or candidate input (I-59, I-60).
- DistillationReadinessReport is informational only — not a promotion trigger (I-58).
- Shadow coverage is optional — its absence does not block readiness (I-61).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.research.evaluation import (
    EvaluationReport,
    PromotionValidation,
    compare_datasets,
    load_jsonl,
    validate_promotion,
)

logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────


@dataclasses.dataclass
class DistillationInputs:
    """Paths to inputs for a distillation readiness assessment."""

    teacher_path: str | None = None
    candidate_path: str | None = None
    eval_report_path: str | None = None
    shadow_path: str | None = None
    dataset_type: str = "internal_benchmark"


@dataclasses.dataclass
class ShadowCoverageReport:
    """Aggregate divergence statistics from a shadow JSONL file."""

    total_records: int
    error_records: int
    valid_records: int
    sentiment_agreement_rate: float
    actionable_agreement_rate: float
    avg_priority_diff: float
    avg_relevance_diff: float
    avg_impact_diff: float
    avg_tag_overlap: float

    def to_json_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class DistillationReadinessReport:
    """Full distillation readiness snapshot — audit artifact only (I-58)."""

    generated_at: str
    inputs: DistillationInputs
    evaluation: EvaluationReport
    promotion_validation: PromotionValidation
    shadow_coverage: ShadowCoverageReport | None = None
    notes: list[str] = dataclasses.field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "inputs": dataclasses.asdict(self.inputs),
            "evaluation": self.evaluation.to_json_dict(),
            "promotion_validation": {
                "sentiment_pass": self.promotion_validation.sentiment_pass,
                "priority_pass": self.promotion_validation.priority_pass,
                "relevance_pass": self.promotion_validation.relevance_pass,
                "impact_pass": self.promotion_validation.impact_pass,
                "tag_overlap_pass": self.promotion_validation.tag_overlap_pass,
                "false_actionable_pass": self.promotion_validation.false_actionable_pass,
                "is_promotable": self.promotion_validation.is_promotable,
            },
            "shadow_coverage": (
                self.shadow_coverage.to_json_dict() if self.shadow_coverage else None
            ),
            "notes": list(self.notes),
        }


# ── Core functions ────────────────────────────────────────────────────────────


def compute_shadow_coverage(shadow_path: Path | str) -> ShadowCoverageReport:
    """Read shadow JSONL and compute aggregate divergence statistics (I-60).

    Accepts both shadow formats:
    - Batch format (shadow.py): record["divergence"]["priority_diff"]
    - Live format (evaluation.py): record["deviations"]["priority_delta"]

    Records with missing/null deviation data are counted as error_records.

    Raises:
        FileNotFoundError: if shadow_path does not exist.
    """
    path = Path(shadow_path)
    if not path.exists():
        raise FileNotFoundError(f"Shadow file not found: {path}")

    total = 0
    errors = 0
    sentiment_matches: list[bool] = []
    actionable_matches: list[bool] = []
    priority_diffs: list[float] = []
    relevance_diffs: list[float] = []
    impact_diffs: list[float] = []
    tag_overlaps: list[float] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            errors += 1
            continue

        # Detect format and extract deviation fields
        if record.get("record_type") == "companion_shadow_run":
            # Live format from evaluation.py build_shadow_run_record()
            dev = record.get("deviations")
            if not dev:
                errors += 1
                continue
            priority_diff = abs(dev.get("priority_delta", 0))
            relevance_diff = abs(dev.get("relevance_delta", 0.0))
            impact_diff = abs(dev.get("impact_delta", 0.0))
            sentiment_match = dev.get("sentiment_match", False)
            actionable_match = dev.get("actionable_match", False)
            tag_overlap = dev.get("tag_overlap", 0.0)
        else:
            # Batch format from shadow.py write_shadow_record()
            div = record.get("divergence")
            if not div:
                errors += 1
                continue
            priority_diff = abs(div.get("priority_diff", 0))
            relevance_diff = abs(div.get("relevance_diff", 0.0))
            impact_diff = abs(div.get("impact_diff", 0.0))
            sentiment_match = div.get("sentiment_match", False)
            actionable_match = div.get("actionable_match", False)
            tag_overlap = div.get("tag_overlap", 0.0)

        sentiment_matches.append(bool(sentiment_match))
        actionable_matches.append(bool(actionable_match))
        priority_diffs.append(float(priority_diff))
        relevance_diffs.append(float(relevance_diff))
        impact_diffs.append(float(impact_diff))
        tag_overlaps.append(float(tag_overlap))

    valid = total - errors

    def _mean(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    def _rate(bools: list[bool]) -> float:
        return sum(bools) / len(bools) if bools else 0.0

    return ShadowCoverageReport(
        total_records=total,
        error_records=errors,
        valid_records=valid,
        sentiment_agreement_rate=_rate(sentiment_matches),
        actionable_agreement_rate=_rate(actionable_matches),
        avg_priority_diff=_mean(priority_diffs),
        avg_relevance_diff=_mean(relevance_diffs),
        avg_impact_diff=_mean(impact_diffs),
        avg_tag_overlap=_mean(tag_overlaps),
    )


def build_distillation_report(inputs: DistillationInputs) -> DistillationReadinessReport:
    """Build a distillation readiness report from available inputs (I-62).

    Steps:
        1. Load EvaluationReport (from eval_report_path or via compare_datasets).
        2. Run validate_promotion() on metrics.
        3. Optionally compute_shadow_coverage().
        4. Assemble DistillationReadinessReport.

    Raises:
        ValueError: if neither eval_report_path nor (teacher_path + candidate_path) set.
        FileNotFoundError: if any specified path does not exist.
    """
    # Step 1: get EvaluationReport
    if inputs.eval_report_path:
        report_path = Path(inputs.eval_report_path)
        if not report_path.exists():
            raise FileNotFoundError(f"Eval report not found: {report_path}")
        raw = json.loads(report_path.read_text(encoding="utf-8"))
        from app.research.evaluation import EvaluationMetrics

        metrics_raw = raw.get("metrics", {})
        metrics = EvaluationMetrics(
            sentiment_agreement=metrics_raw.get("sentiment_agreement", 0.0),
            priority_mae=metrics_raw.get("priority_mae", 0.0),
            relevance_mae=metrics_raw.get("relevance_mae", 0.0),
            impact_mae=metrics_raw.get("impact_mae", 0.0),
            tag_overlap_mean=metrics_raw.get("tag_overlap_mean", 0.0),
            sample_count=metrics_raw.get("sample_count", 0),
            missing_pairs=metrics_raw.get("missing_pairs", 0),
            actionable_accuracy=metrics_raw.get("actionable_accuracy", 0.0),
            false_actionable_rate=metrics_raw.get("false_actionable_rate", 0.0),
        )
        evaluation = EvaluationReport(
            metrics=metrics,
            dataset_type=raw.get("dataset_type", inputs.dataset_type),
            teacher_count=raw.get("teacher_count", 0),
            baseline_count=raw.get("baseline_count", 0),
            paired_count=raw.get("paired_count", 0),
            notes=raw.get("notes", []),
        )
    elif inputs.teacher_path and inputs.candidate_path:
        teacher_path = Path(inputs.teacher_path)
        candidate_path = Path(inputs.candidate_path)
        if not teacher_path.exists():
            raise FileNotFoundError(f"Teacher JSONL not found: {teacher_path}")
        if not candidate_path.exists():
            raise FileNotFoundError(f"Candidate JSONL not found: {candidate_path}")
        teacher_rows = load_jsonl(teacher_path)
        candidate_rows = load_jsonl(candidate_path)
        evaluation = compare_datasets(
            teacher_rows, candidate_rows, dataset_type=inputs.dataset_type
        )
    else:
        raise ValueError(
            "DistillationInputs requires either eval_report_path or "
            "both teacher_path and candidate_path."
        )

    # Step 2: gate validation
    promotion_validation = validate_promotion(evaluation.metrics)

    # Step 3: optional shadow coverage (I-61)
    shadow_coverage: ShadowCoverageReport | None = None
    if inputs.shadow_path:
        shadow_coverage = compute_shadow_coverage(inputs.shadow_path)

    return DistillationReadinessReport(
        generated_at=datetime.now(UTC).isoformat(),
        inputs=inputs,
        evaluation=evaluation,
        promotion_validation=promotion_validation,
        shadow_coverage=shadow_coverage,
    )


def save_distillation_manifest(
    report: DistillationReadinessReport,
    path: Path | str,
) -> Path:
    """Persist DistillationReadinessReport as JSON. Creates parent dirs. Overwrites if exists."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_json_dict(), indent=2), encoding="utf-8")
    return out.resolve()
