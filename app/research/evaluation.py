"""Evaluation module for comparing models against a baseline.

[EXPERIMENTAL — NO ACTIVE MODEL]
Two evaluation surfaces:
- compare_outputs(): live CanonicalDocument comparison (Sprint 5 / evaluate CLI)
- compare_datasets(): offline JSONL export comparison (Sprint 6 / distillation readiness)

Part of the companion model pipeline. No companion model is currently deployed.
Voraussetzungen für echte Aktivierung: trainiertes Modell + konfigurierter Endpoint.

Contract reference: docs/contracts.md §16
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.domain.document import AnalysisResult, CanonicalDocument

# ---------------------------------------------------------------------------
# Sprint 5 / evaluate CLI — live CanonicalDocument comparison (unchanged)
# ---------------------------------------------------------------------------


@dataclass
class EvaluationResult:
    document_count: int
    matched_sentiments: int
    matched_actionable: int
    sentiment_accuracy: float
    actionable_accuracy: float
    priority_mse: float
    relevance_mse: float
    impact_mse: float
    novelty_mse: float


def compare_outputs(
    teacher_docs: list[CanonicalDocument],
    companion_docs: list[CanonicalDocument],
) -> EvaluationResult:
    """Compare companion model analysis results against teacher outputs.

    Both lists must contain the same documents in the same order (matched by ID).
    """
    if not teacher_docs or len(teacher_docs) != len(companion_docs):
        raise ValueError("Teacher and Companion doc lists must be non-empty and equally sized.")

    count = len(teacher_docs)
    matched_sents = 0
    matched_acts = 0

    p_err_sq = 0.0
    r_err_sq = 0.0
    i_err_sq = 0.0
    n_err_sq = 0.0

    for t_doc, c_doc in zip(teacher_docs, companion_docs, strict=True):
        if t_doc.id != c_doc.id:
            raise ValueError(f"Document ID mismatch at index: {t_doc.id} != {c_doc.id}")

        t_sent = t_doc.sentiment_label
        c_sent = c_doc.sentiment_label
        if t_sent == c_sent:
            matched_sents += 1

        # Actionable isn't explicitly on doc, but priority >= 7 is the threshold (see alerts app)
        t_act = (t_doc.priority_score or 0) >= 7
        c_act = (c_doc.priority_score or 0) >= 7
        if t_act == c_act:
            matched_acts += 1

        p_err = float(t_doc.priority_score or 1) - float(c_doc.priority_score or 1)
        p_err_sq += p_err * p_err

        r_err = (t_doc.relevance_score or 0.0) - (c_doc.relevance_score or 0.0)
        r_err_sq += r_err * r_err

        i_err = (t_doc.impact_score or 0.0) - (c_doc.impact_score or 0.0)
        i_err_sq += i_err * i_err

        n_err = (t_doc.novelty_score or 0.0) - (c_doc.novelty_score or 0.0)
        n_err_sq += n_err * n_err

    return EvaluationResult(
        document_count=count,
        matched_sentiments=matched_sents,
        matched_actionable=matched_acts,
        sentiment_accuracy=matched_sents / count,
        actionable_accuracy=matched_acts / count,
        priority_mse=p_err_sq / count,
        relevance_mse=r_err_sq / count,
        impact_mse=i_err_sq / count,
        novelty_mse=n_err_sq / count,
    )


# ---------------------------------------------------------------------------
# Sprint 6 — offline JSONL dataset comparison
# ---------------------------------------------------------------------------


@dataclass
class EvaluationMetrics:
    """Aggregate metrics from an offline JSONL dataset comparison.

    All error metrics use MAE (mean absolute error), not MSE, for interpretability.
    """

    sentiment_agreement: float  # fraction of rows where sentiment_label matches (0.0–1.0)
    priority_mae: float  # mean absolute error on priority_score (1–10 scale)
    relevance_mae: float  # mean absolute error on relevance_score (0.0–1.0)
    impact_mae: float  # mean absolute error on impact_score (0.0–1.0)
    tag_overlap_mean: float  # average Jaccard similarity of tags lists (0.0–1.0)
    sample_count: int  # number of rows successfully paired and evaluated
    missing_pairs: int  # baseline rows with no matching document_id in teacher set
    actionable_accuracy: float = 0.0  # fraction where actionable status matches (backward-compat)
    false_actionable_rate: float = 0.0  # candidate fires but teacher does not (G6 gate)

    def to_json_dict(self) -> dict[str, float | int]:
        return {
            "sentiment_agreement": self.sentiment_agreement,
            "priority_mae": self.priority_mae,
            "relevance_mae": self.relevance_mae,
            "impact_mae": self.impact_mae,
            "tag_overlap_mean": self.tag_overlap_mean,
            "actionable_accuracy": self.actionable_accuracy,
            "false_actionable_rate": self.false_actionable_rate,
            "sample_count": self.sample_count,
            "missing_pairs": self.missing_pairs,
        }


@dataclass
class PromotionValidation:
    """Evaluates whether metrics meet the Sprint 7 companion promotion thresholds."""

    sentiment_pass: bool
    priority_pass: bool
    relevance_pass: bool
    impact_pass: bool
    tag_overlap_pass: bool
    false_actionable_pass: bool

    @property
    def is_promotable(self) -> bool:
        return all(
            [
                self.sentiment_pass,
                self.priority_pass,
                self.relevance_pass,
                self.impact_pass,
                self.tag_overlap_pass,
                self.false_actionable_pass,
            ]
        )


def validate_promotion(metrics: EvaluationMetrics) -> PromotionValidation:
    """Check metrics against strict promotion thresholds defined in contracts."""
    return PromotionValidation(
        sentiment_pass=metrics.sentiment_agreement >= 0.85,
        priority_pass=metrics.priority_mae <= 1.5,
        relevance_pass=metrics.relevance_mae <= 0.15,
        impact_pass=metrics.impact_mae <= 0.20,
        tag_overlap_pass=metrics.tag_overlap_mean >= 0.30,
        false_actionable_pass=metrics.false_actionable_rate <= 0.05,
    )


@dataclass
class EvaluationReport:
    """Full report from compare_datasets().

    dataset_type identifies the baseline tier being evaluated:
    - "rule_baseline"       — comparing teacher vs Tier 1 (rule-based) outputs
    - "internal_benchmark"  — comparing teacher vs Tier 2 (internal/companion) outputs
    - "custom"              — any other comparison
    """

    metrics: EvaluationMetrics
    dataset_type: str
    teacher_count: int
    baseline_count: int
    paired_count: int
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "dataset_type": self.dataset_type,
            "teacher_count": self.teacher_count,
            "baseline_count": self.baseline_count,
            "paired_count": self.paired_count,
            "metrics": self.metrics.to_json_dict(),
            "notes": list(self.notes),
        }


@dataclass
class CountComparison:
    """Structured baseline-vs-candidate comparison for integer counts."""

    baseline: int
    candidate: int
    delta: int

    def to_json_dict(self) -> dict[str, int]:
        return {
            "baseline": self.baseline,
            "candidate": self.candidate,
            "delta": self.delta,
        }


@dataclass
class MetricComparison:
    """Structured baseline-vs-candidate comparison for float metrics."""

    baseline: float
    candidate: float
    delta: float

    def to_json_dict(self) -> dict[str, float]:
        return {
            "baseline": self.baseline,
            "candidate": self.candidate,
            "delta": self.delta,
        }


@dataclass
class EvaluationMetricDeltas:
    """Delta bundle for evaluation metrics."""

    sentiment_agreement: MetricComparison
    priority_mae: MetricComparison
    relevance_mae: MetricComparison
    impact_mae: MetricComparison
    tag_overlap_mean: MetricComparison
    actionable_accuracy: MetricComparison
    false_actionable_rate: MetricComparison

    def to_json_dict(self) -> dict[str, dict[str, float]]:
        return {
            "sentiment_agreement": self.sentiment_agreement.to_json_dict(),
            "priority_mae": self.priority_mae.to_json_dict(),
            "relevance_mae": self.relevance_mae.to_json_dict(),
            "impact_mae": self.impact_mae.to_json_dict(),
            "tag_overlap_mean": self.tag_overlap_mean.to_json_dict(),
            "actionable_accuracy": self.actionable_accuracy.to_json_dict(),
            "false_actionable_rate": self.false_actionable_rate.to_json_dict(),
        }


@dataclass
class GateChange:
    """Pass/fail transition of one promotion gate."""

    baseline_pass: bool
    candidate_pass: bool
    changed: bool
    regressed: bool
    improved: bool

    def to_json_dict(self) -> dict[str, bool]:
        return {
            "baseline_pass": self.baseline_pass,
            "candidate_pass": self.candidate_pass,
            "changed": self.changed,
            "regressed": self.regressed,
            "improved": self.improved,
        }


@dataclass
class PromotionGateChanges:
    """Gate-by-gate promotion transition summary."""

    baseline_promotable: bool
    candidate_promotable: bool
    sentiment: GateChange
    priority: GateChange
    relevance: GateChange
    impact: GateChange
    tag_overlap: GateChange
    false_actionable: GateChange

    def to_json_dict(self) -> dict[str, object]:
        return {
            "baseline_promotable": self.baseline_promotable,
            "candidate_promotable": self.candidate_promotable,
            "sentiment": self.sentiment.to_json_dict(),
            "priority": self.priority.to_json_dict(),
            "relevance": self.relevance.to_json_dict(),
            "impact": self.impact.to_json_dict(),
            "tag_overlap": self.tag_overlap.to_json_dict(),
            "false_actionable": self.false_actionable.to_json_dict(),
        }


@dataclass
class RegressionSummary:
    """High-level regression/improvement classification for a report comparison."""

    has_regression: bool
    regressed_metrics: list[str] = field(default_factory=list)
    improved_metrics: list[str] = field(default_factory=list)
    regressed_gates: list[str] = field(default_factory=list)
    improved_gates: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "has_regression": self.has_regression,
            "regressed_metrics": list(self.regressed_metrics),
            "improved_metrics": list(self.improved_metrics),
            "regressed_gates": list(self.regressed_gates),
            "improved_gates": list(self.improved_gates),
        }


@dataclass
class EvaluationComparisonReport:
    """Structured comparison between two persisted evaluation reports."""

    baseline_dataset_type: str
    candidate_dataset_type: str
    paired_count: CountComparison
    metric_deltas: EvaluationMetricDeltas
    pass_fail_changes: PromotionGateChanges
    regression_summary: RegressionSummary
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "baseline_dataset_type": self.baseline_dataset_type,
            "candidate_dataset_type": self.candidate_dataset_type,
            "paired_count": self.paired_count.to_json_dict(),
            "metric_deltas": self.metric_deltas.to_json_dict(),
            "pass_fail_changes": self.pass_fail_changes.to_json_dict(),
            "regression_summary": self.regression_summary.to_json_dict(),
            "notes": list(self.notes),
        }


def load_jsonl(path: Path | str) -> list[dict[str, Any]]:
    """Load a JSONL export file into a list of row dicts.

    Compatible with the format produced by export_training_data().
    """
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def save_jsonl_rows(rows: list[dict[str, Any]], output_path: Path | str) -> Path:
    """Persist rows to JSONL for reproducible offline benchmarks."""
    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    with resolved_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    return resolved_path


def load_saved_evaluation_report(path: Path | str) -> EvaluationReport:
    """Load a persisted evaluation_report.json and validate its required structure."""
    report_path = Path(path)
    if not report_path.exists():
        raise FileNotFoundError(f"Evaluation report not found: {report_path}")

    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"Evaluation report is not valid JSON: {report_path}") from err

    if not isinstance(payload, dict):
        raise ValueError("Evaluation report must be a JSON object.")

    if payload.get("report_type") != "dataset_evaluation":
        raise ValueError("Evaluation report must have report_type='dataset_evaluation'.")

    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("Evaluation report is missing required inputs metadata.")

    teacher_dataset = inputs.get("teacher_dataset")
    if not isinstance(teacher_dataset, str) or not teacher_dataset.strip():
        raise ValueError("Evaluation report inputs.teacher_dataset must be a non-empty string.")

    candidate_dataset = inputs.get("candidate_dataset")
    if not isinstance(candidate_dataset, str) or not candidate_dataset.strip():
        raise ValueError("Evaluation report inputs.candidate_dataset must be a non-empty string.")

    dataset_type = payload.get("dataset_type")
    if not isinstance(dataset_type, str) or not dataset_type.strip():
        raise ValueError("Evaluation report dataset_type must be a non-empty string.")

    notes_raw = payload.get("notes", [])
    if not isinstance(notes_raw, list) or any(not isinstance(item, str) for item in notes_raw):
        raise ValueError("Evaluation report notes must be a list[str].")

    metrics = _parse_metrics_payload(payload.get("metrics"))
    teacher_count = _parse_non_negative_int(payload.get("teacher_count"), "teacher_count")
    baseline_count = _parse_non_negative_int(payload.get("baseline_count"), "baseline_count")
    paired_count = _parse_non_negative_int(payload.get("paired_count"), "paired_count")

    return EvaluationReport(
        metrics=metrics,
        dataset_type=dataset_type,
        teacher_count=teacher_count,
        baseline_count=baseline_count,
        paired_count=paired_count,
        notes=list(notes_raw),
    )


def compare_evaluation_reports(
    baseline_report: EvaluationReport,
    candidate_report: EvaluationReport,
) -> EvaluationComparisonReport:
    """Compare two persisted evaluation reports without rerunning evaluation."""
    raw_deltas = compare_metrics(baseline_report.metrics, candidate_report.metrics)
    metric_deltas = EvaluationMetricDeltas(
        sentiment_agreement=_build_metric_comparison(
            baseline_report.metrics.sentiment_agreement,
            candidate_report.metrics.sentiment_agreement,
            delta=raw_deltas.sentiment_agreement_delta,
        ),
        priority_mae=_build_metric_comparison(
            baseline_report.metrics.priority_mae,
            candidate_report.metrics.priority_mae,
            delta=raw_deltas.priority_mae_delta,
        ),
        relevance_mae=_build_metric_comparison(
            baseline_report.metrics.relevance_mae,
            candidate_report.metrics.relevance_mae,
            delta=raw_deltas.relevance_mae_delta,
        ),
        impact_mae=_build_metric_comparison(
            baseline_report.metrics.impact_mae,
            candidate_report.metrics.impact_mae,
            delta=raw_deltas.impact_mae_delta,
        ),
        tag_overlap_mean=_build_metric_comparison(
            baseline_report.metrics.tag_overlap_mean,
            candidate_report.metrics.tag_overlap_mean,
            delta=raw_deltas.tag_overlap_delta,
        ),
        actionable_accuracy=_build_metric_comparison(
            baseline_report.metrics.actionable_accuracy,
            candidate_report.metrics.actionable_accuracy,
            delta=raw_deltas.actionable_accuracy_delta,
        ),
        false_actionable_rate=_build_metric_comparison(
            baseline_report.metrics.false_actionable_rate,
            candidate_report.metrics.false_actionable_rate,
            delta=raw_deltas.false_actionable_rate_delta,
        ),
    )

    baseline_validation = validate_promotion(baseline_report.metrics)
    candidate_validation = validate_promotion(candidate_report.metrics)
    pass_fail_changes = PromotionGateChanges(
        baseline_promotable=baseline_validation.is_promotable,
        candidate_promotable=candidate_validation.is_promotable,
        sentiment=_build_gate_change(
            baseline_validation.sentiment_pass,
            candidate_validation.sentiment_pass,
        ),
        priority=_build_gate_change(
            baseline_validation.priority_pass,
            candidate_validation.priority_pass,
        ),
        relevance=_build_gate_change(
            baseline_validation.relevance_pass,
            candidate_validation.relevance_pass,
        ),
        impact=_build_gate_change(
            baseline_validation.impact_pass,
            candidate_validation.impact_pass,
        ),
        tag_overlap=_build_gate_change(
            baseline_validation.tag_overlap_pass,
            candidate_validation.tag_overlap_pass,
        ),
        false_actionable=_build_gate_change(
            baseline_validation.false_actionable_pass,
            candidate_validation.false_actionable_pass,
        ),
    )

    regressed_metrics: list[str] = []
    improved_metrics: list[str] = []
    _track_metric_direction(
        "paired_count",
        float(baseline_report.paired_count),
        float(candidate_report.paired_count),
        higher_is_better=True,
        regressed_metrics=regressed_metrics,
        improved_metrics=improved_metrics,
    )
    _track_metric_direction(
        "sentiment_agreement",
        baseline_report.metrics.sentiment_agreement,
        candidate_report.metrics.sentiment_agreement,
        higher_is_better=True,
        regressed_metrics=regressed_metrics,
        improved_metrics=improved_metrics,
    )
    _track_metric_direction(
        "priority_mae",
        baseline_report.metrics.priority_mae,
        candidate_report.metrics.priority_mae,
        higher_is_better=False,
        regressed_metrics=regressed_metrics,
        improved_metrics=improved_metrics,
    )
    _track_metric_direction(
        "relevance_mae",
        baseline_report.metrics.relevance_mae,
        candidate_report.metrics.relevance_mae,
        higher_is_better=False,
        regressed_metrics=regressed_metrics,
        improved_metrics=improved_metrics,
    )
    _track_metric_direction(
        "impact_mae",
        baseline_report.metrics.impact_mae,
        candidate_report.metrics.impact_mae,
        higher_is_better=False,
        regressed_metrics=regressed_metrics,
        improved_metrics=improved_metrics,
    )
    _track_metric_direction(
        "tag_overlap_mean",
        baseline_report.metrics.tag_overlap_mean,
        candidate_report.metrics.tag_overlap_mean,
        higher_is_better=True,
        regressed_metrics=regressed_metrics,
        improved_metrics=improved_metrics,
    )
    _track_metric_direction(
        "actionable_accuracy",
        baseline_report.metrics.actionable_accuracy,
        candidate_report.metrics.actionable_accuracy,
        higher_is_better=True,
        regressed_metrics=regressed_metrics,
        improved_metrics=improved_metrics,
    )
    _track_metric_direction(
        "false_actionable_rate",
        baseline_report.metrics.false_actionable_rate,
        candidate_report.metrics.false_actionable_rate,
        higher_is_better=False,
        regressed_metrics=regressed_metrics,
        improved_metrics=improved_metrics,
    )

    regressed_gates, improved_gates = _collect_gate_changes(pass_fail_changes)
    notes: list[str] = []
    if baseline_report.dataset_type != candidate_report.dataset_type:
        notes.append(
            "dataset_type changed: "
            f"{baseline_report.dataset_type} -> {candidate_report.dataset_type}"
        )

    return EvaluationComparisonReport(
        baseline_dataset_type=baseline_report.dataset_type,
        candidate_dataset_type=candidate_report.dataset_type,
        paired_count=CountComparison(
            baseline=baseline_report.paired_count,
            candidate=candidate_report.paired_count,
            delta=candidate_report.paired_count - baseline_report.paired_count,
        ),
        metric_deltas=metric_deltas,
        pass_fail_changes=pass_fail_changes,
        regression_summary=RegressionSummary(
            has_regression=bool(regressed_metrics or regressed_gates),
            regressed_metrics=regressed_metrics,
            improved_metrics=improved_metrics,
            regressed_gates=regressed_gates,
            improved_gates=improved_gates,
        ),
        notes=notes,
    )


def save_evaluation_comparison_report(
    report: EvaluationComparisonReport,
    output_path: Path | str,
    *,
    baseline_report: Path | str,
    candidate_report: Path | str,
) -> Path:
    """Persist a structured baseline-vs-candidate comparison report."""
    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "report_type": "evaluation_report_comparison",
        "generated_at": datetime.now(UTC).isoformat(),
        "inputs": {
            "baseline_report": str(Path(baseline_report).resolve()),
            "candidate_report": str(Path(candidate_report).resolve()),
        },
        **report.to_json_dict(),
    }
    resolved_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return resolved_path


def _parse_non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Evaluation report field '{field_name}' must be an integer.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"Evaluation report field '{field_name}' must be an integer.")
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as err:
            raise ValueError(f"Evaluation report field '{field_name}' must be an integer.") from err
    else:
        raise ValueError(f"Evaluation report field '{field_name}' must be an integer.")

    if parsed < 0:
        raise ValueError(f"Evaluation report field '{field_name}' must be >= 0.")
    return parsed


def _parse_float(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Evaluation report field '{field_name}' must be numeric.")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as err:
            raise ValueError(f"Evaluation report field '{field_name}' must be numeric.") from err
    raise ValueError(f"Evaluation report field '{field_name}' must be numeric.")


def _parse_metrics_payload(raw_metrics: object) -> EvaluationMetrics:
    if not isinstance(raw_metrics, dict):
        raise ValueError("Evaluation report is missing required metrics object.")

    return EvaluationMetrics(
        sentiment_agreement=_parse_float(
            raw_metrics.get("sentiment_agreement"),
            "metrics.sentiment_agreement",
        ),
        priority_mae=_parse_float(raw_metrics.get("priority_mae"), "metrics.priority_mae"),
        relevance_mae=_parse_float(raw_metrics.get("relevance_mae"), "metrics.relevance_mae"),
        impact_mae=_parse_float(raw_metrics.get("impact_mae"), "metrics.impact_mae"),
        tag_overlap_mean=_parse_float(
            raw_metrics.get("tag_overlap_mean"),
            "metrics.tag_overlap_mean",
        ),
        actionable_accuracy=_parse_float(
            raw_metrics.get("actionable_accuracy"),
            "metrics.actionable_accuracy",
        ),
        false_actionable_rate=_parse_float(
            raw_metrics.get("false_actionable_rate"),
            "metrics.false_actionable_rate",
        ),
        sample_count=_parse_non_negative_int(
            raw_metrics.get("sample_count"),
            "metrics.sample_count",
        ),
        missing_pairs=_parse_non_negative_int(
            raw_metrics.get("missing_pairs"),
            "metrics.missing_pairs",
        ),
    )


def _build_metric_comparison(
    baseline: float,
    candidate: float,
    *,
    delta: float | None = None,
) -> MetricComparison:
    return MetricComparison(
        baseline=baseline,
        candidate=candidate,
        delta=(candidate - baseline) if delta is None else delta,
    )


def _build_gate_change(baseline_pass: bool, candidate_pass: bool) -> GateChange:
    return GateChange(
        baseline_pass=baseline_pass,
        candidate_pass=candidate_pass,
        changed=baseline_pass != candidate_pass,
        regressed=baseline_pass and not candidate_pass,
        improved=(not baseline_pass) and candidate_pass,
    )


def _track_metric_direction(
    name: str,
    baseline: float,
    candidate: float,
    *,
    higher_is_better: bool,
    regressed_metrics: list[str],
    improved_metrics: list[str],
) -> None:
    if candidate == baseline:
        return

    if higher_is_better:
        if candidate < baseline:
            regressed_metrics.append(name)
        else:
            improved_metrics.append(name)
        return

    if candidate > baseline:
        regressed_metrics.append(name)
    else:
        improved_metrics.append(name)


def _collect_gate_changes(
    changes: PromotionGateChanges,
) -> tuple[list[str], list[str]]:
    regressed: list[str] = []
    improved: list[str] = []
    gate_mapping = {
        "sentiment": changes.sentiment,
        "priority": changes.priority,
        "relevance": changes.relevance,
        "impact": changes.impact,
        "tag_overlap": changes.tag_overlap,
        "false_actionable": changes.false_actionable,
    }
    for gate_name, gate_change in gate_mapping.items():
        if gate_change.regressed:
            regressed.append(gate_name)
        if gate_change.improved:
            improved.append(gate_name)
    return regressed, improved


def _extract_target(row: dict[str, Any]) -> dict[str, Any]:
    """Extract the assistant target dict from a JSONL row."""
    content = row["messages"][2]["content"]
    if isinstance(content, str):
        return json.loads(content)  # type: ignore[no-any-return]
    return dict(content)


def extract_source_document(row: dict[str, Any]) -> tuple[str, str]:
    """Extract title and content text from an exported dataset row."""
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError("Dataset row is missing the required user message.")

    user_message = messages[1]
    if not isinstance(user_message, dict):
        raise ValueError("Dataset row user message is malformed.")

    content = user_message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Dataset row user content is empty.")

    normalized = content.strip()
    title = "Untitled"
    for line in normalized.splitlines():
        if line.startswith("Title:"):
            extracted = line.split(":", 1)[1].strip()
            if extracted:
                title = extracted
            break

    if "Content:\n" in normalized:
        text = normalized.split("Content:\n", 1)[1].strip()
    elif "Content:" in normalized:
        text = normalized.split("Content:", 1)[1].strip()
    else:
        text = normalized

    return title, text or normalized


def llm_output_to_dataset_target(output: LLMAnalysisOutput) -> dict[str, Any]:
    """Convert provider output into the existing JSONL assistant target schema."""
    return {
        "affected_assets": [asset.strip() for asset in output.affected_assets if asset.strip()],
        "impact_score": output.impact_score,
        "market_scope": output.market_scope.value,
        "novelty_score": output.novelty_score,
        "priority_score": output.recommended_priority,
        "relevance_score": output.relevance_score,
        "sentiment_label": output.sentiment_label.value,
        "sentiment_score": output.sentiment_score,
        "spam_probability": output.spam_probability,
        "summary": output.short_reasoning or output.long_reasoning or "",
        "tags": [tag.strip() for tag in output.tags if tag.strip()],
    }


def build_candidate_dataset_row(
    source_row: dict[str, Any],
    output: LLMAnalysisOutput,
    *,
    provider_name: str,
    analysis_source: str = "internal",
) -> dict[str, Any]:
    """Build an internal candidate row while preserving the existing dataset schema."""
    messages = source_row.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError("Dataset row is missing required messages.")

    system_message = messages[0]
    user_message = messages[1]
    if not isinstance(system_message, dict) or not isinstance(user_message, dict):
        raise ValueError("Dataset row messages must be mapping objects.")

    metadata_raw = source_row.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    metadata["provider"] = provider_name.strip() or "companion"
    metadata["analysis_source"] = analysis_source

    return {
        "messages": [
            dict(system_message),
            dict(user_message),
            {
                "role": "assistant",
                "content": json.dumps(llm_output_to_dataset_target(output), sort_keys=True),
            },
        ],
        "metadata": metadata,
    }


async def build_candidate_dataset_rows(
    teacher_rows: list[dict[str, Any]],
    analyze: Callable[[str, str, dict[str, Any] | None], Awaitable[LLMAnalysisOutput]],
    *,
    provider_name: str,
    analysis_source: str = "internal",
) -> list[dict[str, Any]]:
    """Generate internal candidate rows from teacher rows via a provider analyze callable."""
    rows: list[dict[str, Any]] = []
    for row in teacher_rows:
        title, text = extract_source_document(row)
        metadata = row.get("metadata")
        context = dict(metadata) if isinstance(metadata, dict) else None
        output = await analyze(title, text, context)
        rows.append(
            build_candidate_dataset_row(
                row,
                output,
                provider_name=provider_name,
                analysis_source=analysis_source,
            )
        )
    return rows


def build_shadow_run_record(
    document: CanonicalDocument,
    primary_result: AnalysisResult,
    *,
    primary_provider: str | None,
    shadow_output: LLMAnalysisOutput | None,
    shadow_provider: str | None,
    shadow_error: str | None = None,
) -> dict[str, Any]:
    """Build a compact shadow-run audit record without changing the primary schema."""
    primary_priority = int(primary_result.recommended_priority or document.priority_score or 1)
    primary_summary = primary_result.explanation_short or primary_result.explanation_long or ""
    primary_tags = [tag.strip() for tag in primary_result.tags if tag.strip()]

    payload: dict[str, Any] = {
        "record_type": "companion_shadow_run",
        "generated_at": datetime.now(UTC).isoformat(),
        "document_id": str(document.id),
        "primary_provider": (
            (primary_provider or document.provider or "fallback").strip() or "fallback"
        ),
        "primary_analysis_source": (
            primary_result.analysis_source.value
            if primary_result.analysis_source is not None
            else document.effective_analysis_source.value
        ),
        "primary": {
            "summary": primary_summary,
            "priority_score": primary_priority,
            "relevance_score": primary_result.relevance_score,
            "impact_score": primary_result.impact_score,
            "sentiment_label": primary_result.sentiment_label.value,
            "actionable": primary_result.actionable,
            "tags": primary_tags,
        },
        "shadow_provider": (shadow_provider or "").strip() or None,
        "shadow_analysis_source": "internal" if shadow_provider else None,
        "shadow": None,
        "deviations": None,
        "shadow_error": shadow_error,
    }

    if shadow_output is None:
        return payload

    shadow_tags = [tag.strip() for tag in shadow_output.tags if tag.strip()]
    shadow_summary = shadow_output.short_reasoning or shadow_output.long_reasoning or ""
    shadow_priority = int(shadow_output.recommended_priority)

    payload["shadow"] = {
        "summary": shadow_summary,
        "priority_score": shadow_priority,
        "relevance_score": shadow_output.relevance_score,
        "impact_score": shadow_output.impact_score,
        "sentiment_label": shadow_output.sentiment_label.value,
        "actionable": shadow_output.actionable,
        "tags": shadow_tags,
    }
    payload["deviations"] = {
        "priority_delta": abs(primary_priority - shadow_priority),
        "relevance_delta": abs(primary_result.relevance_score - shadow_output.relevance_score),
        "impact_delta": abs(primary_result.impact_score - shadow_output.impact_score),
        "sentiment_match": primary_result.sentiment_label == shadow_output.sentiment_label,
        "actionable_match": primary_result.actionable == shadow_output.actionable,
        "tag_overlap": _jaccard(primary_tags, shadow_tags),
    }
    return payload


def save_shadow_run_records(records: list[dict[str, Any]], output_path: Path | str) -> Path:
    """Persist sidecar shadow-run audit rows as JSONL."""
    return save_jsonl_rows(records, output_path)


def _jaccard(a: list[str], b: list[str]) -> float:
    """Jaccard similarity between two tag lists. Both empty → 1.0."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _is_actionable(target: dict[str, Any]) -> bool:
    """Resolve the actionable label from a structured target without fuzzy inference.

    Preferred source is an explicit boolean `actionable` field when present.
    If absent, fall back to the documented deterministic threshold `priority_score >= 7`.
    """
    explicit = target.get("actionable")
    if isinstance(explicit, bool):
        return explicit

    try:
        priority = float(target.get("priority_score", 1))
    except (TypeError, ValueError):
        return False
    return priority >= 7.0


def compare_datasets(
    teacher_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    *,
    dataset_type: str = "rule_baseline",
) -> EvaluationReport:
    """Compare exported baseline rows against teacher rows, matched by document_id.

    Contract reference: docs/contracts.md §16b

    Args:
        teacher_rows:  JSONL rows produced by export_training_data(teacher_only=True).
                       Must have metadata.analysis_source == "external_llm".
        baseline_rows: JSONL rows from rule or internal tier export.
                       dataset_type distinguishes the comparison surface.
        dataset_type:  "rule_baseline" | "internal_benchmark" | "custom"

    Returns:
        EvaluationReport with per-metric MAE and Jaccard tag overlap.

    Notes:
        - Matching is by metadata.document_id (not list position).
        - Baseline rows without a matching teacher row are counted in missing_pairs.
        - No LLM calls, no model inference — pure score comparison.
    """
    teacher_index: dict[str, dict[str, Any]] = {
        row["metadata"]["document_id"]: _extract_target(row) for row in teacher_rows
    }

    sentiment_matches = 0
    priority_errors: list[float] = []
    relevance_errors: list[float] = []
    impact_errors: list[float] = []
    tag_overlaps: list[float] = []
    actionable_matches = 0
    false_actionables = 0
    missing = 0
    paired = 0

    for row in baseline_rows:
        doc_id = row["metadata"]["document_id"]
        if doc_id not in teacher_index:
            missing += 1
            continue

        teacher = teacher_index[doc_id]
        baseline = _extract_target(row)
        paired += 1

        if teacher.get("sentiment_label") == baseline.get("sentiment_label"):
            sentiment_matches += 1

        priority_errors.append(
            abs(float(teacher.get("priority_score", 1)) - float(baseline.get("priority_score", 1)))
        )
        relevance_errors.append(
            abs(
                float(teacher.get("relevance_score", 0.0))
                - float(baseline.get("relevance_score", 0.0))
            )
        )
        impact_errors.append(
            abs(float(teacher.get("impact_score", 0.0)) - float(baseline.get("impact_score", 0.0)))
        )
        tag_overlaps.append(_jaccard(teacher.get("tags", []), baseline.get("tags", [])))

        t_act = _is_actionable(teacher)
        b_act = _is_actionable(baseline)
        if t_act == b_act:
            actionable_matches += 1
        elif b_act and not t_act:
            false_actionables += 1

    metrics = EvaluationMetrics(
        sentiment_agreement=sentiment_matches / paired if paired else 0.0,
        priority_mae=_mean(priority_errors),
        relevance_mae=_mean(relevance_errors),
        impact_mae=_mean(impact_errors),
        tag_overlap_mean=_mean(tag_overlaps),
        actionable_accuracy=actionable_matches / paired if paired else 0.0,
        false_actionable_rate=false_actionables / paired if paired else 0.0,
        sample_count=paired,
        missing_pairs=missing,
    )

    return EvaluationReport(
        metrics=metrics,
        dataset_type=dataset_type,
        teacher_count=len(teacher_rows),
        baseline_count=len(baseline_rows),
        paired_count=paired,
    )


def save_evaluation_report(
    report: EvaluationReport,
    output_path: Path | str,
    *,
    teacher_dataset: Path | str,
    candidate_dataset: Path | str,
) -> Path:
    """Persist an EvaluationReport as structured JSON for reproducible offline review."""
    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "report_type": "dataset_evaluation",
        "generated_at": datetime.now(UTC).isoformat(),
        "inputs": {
            "teacher_dataset": str(Path(teacher_dataset).resolve()),
            "candidate_dataset": str(Path(candidate_dataset).resolve()),
        },
        **report.to_json_dict(),
    }
    resolved_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return resolved_path


def save_benchmark_artifact(
    output_path: Path | str,
    *,
    teacher_dataset: Path | str,
    candidate_dataset: Path | str,
    report: EvaluationReport,
    report_path: Path | str | None = None,
) -> Path:
    """Write a small benchmark manifest that can serve as a future tuning artifact hook."""
    resolved_path = Path(output_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "artifact_type": "companion_benchmark",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "benchmark_ready" if report.paired_count > 0 else "needs_more_data",
        "dataset_type": report.dataset_type,
        "teacher_dataset": str(Path(teacher_dataset).resolve()),
        "candidate_dataset": str(Path(candidate_dataset).resolve()),
        "evaluation_report": str(Path(report_path).resolve()) if report_path else None,
        "metrics": report.metrics.to_json_dict(),
        "paired_count": report.paired_count,
    }
    resolved_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return resolved_path


@dataclass
class ComparisonMetrics:
    """Stores the deltas between a candidate and baseline EvaluationMetrics."""

    sentiment_agreement_delta: float
    priority_mae_delta: float
    relevance_mae_delta: float
    impact_mae_delta: float
    tag_overlap_delta: float
    actionable_accuracy_delta: float
    false_actionable_rate_delta: float


def compare_metrics(baseline: EvaluationMetrics, candidate: EvaluationMetrics) -> ComparisonMetrics:
    """Calculate the deltas. For MAE tracking, a negative delta is an improvement."""
    return ComparisonMetrics(
        sentiment_agreement_delta=candidate.sentiment_agreement - baseline.sentiment_agreement,
        priority_mae_delta=candidate.priority_mae - baseline.priority_mae,
        relevance_mae_delta=candidate.relevance_mae - baseline.relevance_mae,
        impact_mae_delta=candidate.impact_mae - baseline.impact_mae,
        tag_overlap_delta=candidate.tag_overlap_mean - baseline.tag_overlap_mean,
        actionable_accuracy_delta=candidate.actionable_accuracy - baseline.actionable_accuracy,
        false_actionable_rate_delta=(
            candidate.false_actionable_rate - baseline.false_actionable_rate
        ),
    )
