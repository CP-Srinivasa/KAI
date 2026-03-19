"""Evaluation module for comparing models against a baseline.

Two evaluation surfaces:
- compare_outputs(): live CanonicalDocument comparison (Sprint 5 / evaluate CLI)
- compare_datasets(): offline JSONL export comparison (Sprint 6 / distillation readiness)

Contract reference: docs/contracts.md §16
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.domain.document import CanonicalDocument

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
    priority_mae: float         # mean absolute error on priority_score (1–10 scale)
    relevance_mae: float        # mean absolute error on relevance_score (0.0–1.0)
    impact_mae: float           # mean absolute error on impact_score (0.0–1.0)
    tag_overlap_mean: float     # average Jaccard similarity of tags lists (0.0–1.0)
    sample_count: int           # number of rows successfully paired and evaluated
    missing_pairs: int          # baseline rows with no matching document_id in teacher set

    def to_json_dict(self) -> dict[str, float | int]:
        return {
            "sentiment_agreement": self.sentiment_agreement,
            "priority_mae": self.priority_mae,
            "relevance_mae": self.relevance_mae,
            "impact_mae": self.impact_mae,
            "tag_overlap_mean": self.tag_overlap_mean,
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

    @property
    def is_promotable(self) -> bool:
        return all([
            self.sentiment_pass,
            self.priority_pass,
            self.relevance_pass,
            self.impact_pass,
            self.tag_overlap_pass,
        ])


def validate_promotion(metrics: EvaluationMetrics) -> PromotionValidation:
    """Check metrics against strict promotion thresholds defined in contracts."""
    return PromotionValidation(
        sentiment_pass=metrics.sentiment_agreement >= 0.85,
        priority_pass=metrics.priority_mae <= 1.5,
        relevance_pass=metrics.relevance_mae <= 0.15,
        impact_pass=metrics.impact_mae <= 0.20,
        tag_overlap_pass=metrics.tag_overlap_mean >= 0.30,
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


def _jaccard(a: list[str], b: list[str]) -> float:
    """Jaccard similarity between two tag lists. Both empty → 1.0."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


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
            abs(
                float(teacher.get("priority_score", 1))
                - float(baseline.get("priority_score", 1))
            )
        )
        relevance_errors.append(
            abs(
                float(teacher.get("relevance_score", 0.0))
                - float(baseline.get("relevance_score", 0.0))
            )
        )
        impact_errors.append(
            abs(
                float(teacher.get("impact_score", 0.0))
                - float(baseline.get("impact_score", 0.0))
            )
        )
        tag_overlaps.append(
            _jaccard(teacher.get("tags", []), baseline.get("tags", []))
        )

    metrics = EvaluationMetrics(
        sentiment_agreement=sentiment_matches / paired if paired else 0.0,
        priority_mae=_mean(priority_errors),
        relevance_mae=_mean(relevance_errors),
        impact_mae=_mean(impact_errors),
        tag_overlap_mean=_mean(tag_overlaps),
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
