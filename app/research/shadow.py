"""Shadow run module — offline companion audit (Sprint 10, I-51–I-55).

Runs InternalCompanionProvider in parallel audit mode against already-analyzed
documents. NEVER calls apply_to_document(), update_analysis(), or any score
mutation. Output is a standalone JSONL file only.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.core.domain.document import CanonicalDocument

logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────


@dataclasses.dataclass
class DivergenceSummary:
    """Computed diff between primary stored scores and companion output."""

    sentiment_match: bool
    priority_diff: int
    relevance_diff: float
    impact_diff: float
    actionable_match: bool
    tag_overlap: float  # Jaccard coefficient; 0.0 if both tag sets are empty

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class ShadowRunRecord:
    """One audit record per document — written as a single JSONL line."""

    document_id: str
    run_at: str                    # ISO 8601 UTC
    primary_provider: str
    primary_analysis_source: str
    companion_endpoint: str
    companion_model: str
    primary_result: dict[str, object]
    companion_result: dict[str, object] | None  # None on companion error
    divergence: dict[str, object] | None        # None on companion error

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


# ── Core functions ────────────────────────────────────────────────────────────


def compute_divergence(
    doc: CanonicalDocument,
    companion_output: LLMAnalysisOutput,
) -> DivergenceSummary:
    """Compute divergence between stored primary scores and companion output."""
    primary_sentiment = doc.sentiment_label.value if doc.sentiment_label else "unknown"
    companion_sentiment = companion_output.sentiment_label.value

    primary_tags = set(doc.tags or [])
    companion_tags = set(companion_output.tags or [])
    if not primary_tags and not companion_tags:
        tag_overlap = 0.0
    else:
        intersection = len(primary_tags & companion_tags)
        union = len(primary_tags | companion_tags)
        tag_overlap = intersection / union if union > 0 else 0.0

    # actionable is stored in doc.metadata (written by apply_to_document),
    # not as a direct CanonicalDocument field.
    primary_actionable: bool = doc.metadata.get("actionable", False)

    return DivergenceSummary(
        sentiment_match=primary_sentiment == companion_sentiment,
        priority_diff=abs((doc.priority_score or 0) - companion_output.recommended_priority),
        relevance_diff=abs((doc.relevance_score or 0.0) - companion_output.relevance_score),
        impact_diff=abs((doc.impact_score or 0.0) - companion_output.impact_score),
        actionable_match=primary_actionable == companion_output.actionable,
        tag_overlap=tag_overlap,
    )


def write_shadow_record(record: ShadowRunRecord, path: Path) -> None:
    """Append one ShadowRunRecord as a JSON line to the given file.

    INVARIANT (I-69): Writes canonical ``deviations`` field (priority_delta,
    relevance_delta, impact_delta) matching evaluation.py schema.  The legacy
    ``divergence`` key is retained as a deprecated backward-compat alias so that
    compute_shadow_coverage() can still read old files.
    """
    data = record.to_dict()
    data["record_type"] = "companion_shadow_run"

    # I-69: canonical deviations field alongside deprecated divergence alias
    div = data.get("divergence")
    if isinstance(div, dict):
        data["deviations"] = {
            "priority_delta": div.get("priority_diff", 0),
            "relevance_delta": div.get("relevance_diff", 0.0),
            "impact_delta": div.get("impact_diff", 0.0),
            "sentiment_match": div.get("sentiment_match", False),
            "actionable_match": div.get("actionable_match", False),
            "tag_overlap": div.get("tag_overlap", 0.0),
        }

    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data) + "\n")


async def run_shadow_batch(
    documents: list[CanonicalDocument],
    companion: BaseAnalysisProvider,
    output_path: Path,
) -> list[ShadowRunRecord]:
    """Run companion on each document, write records to JSONL, return all records.

    Companion errors are non-fatal: the record is written with companion_result=None
    and divergence=None. The batch always completes.

    INVARIANT (I-51): Never calls apply_to_document() or update_analysis().
    INVARIANT (I-52): Always calls companion explicitly, regardless of APP_LLM_PROVIDER.
    """
    companion_endpoint = getattr(companion, "endpoint", "unknown")
    companion_model = getattr(companion, "model", "unknown") or "unknown"

    records: list[ShadowRunRecord] = []

    for doc in documents:
        run_at = datetime.now(UTC).isoformat()
        primary_result = {
            "sentiment_label": doc.sentiment_label.value if doc.sentiment_label else "unknown",
            "sentiment_score": doc.sentiment_score or 0.0,
            "relevance_score": doc.relevance_score or 0.0,
            "impact_score": doc.impact_score or 0.0,
            "actionable": doc.metadata.get("actionable", False),
            "priority_score": doc.priority_score or 0,
            "tags": list(doc.tags or []),
        }

        companion_result: dict[str, object] | None = None
        divergence: dict[str, object] | None = None

        try:
            output = await companion.analyze(doc.title, doc.raw_text or "")
            companion_result = {
                "sentiment_label": output.sentiment_label.value,
                "sentiment_score": output.sentiment_score,
                "relevance_score": output.relevance_score,
                "impact_score": output.impact_score,
                "actionable": output.actionable,
                "recommended_priority": output.recommended_priority,
                "tags": list(output.tags or []),
            }
            divergence = compute_divergence(doc, output).to_dict()
        except Exception as exc:
            logger.warning("Shadow companion error for doc %s: %s", doc.id, exc)

        record = ShadowRunRecord(
            document_id=str(doc.id),
            run_at=run_at,
            primary_provider=doc.provider or "unknown",
            primary_analysis_source=doc.analysis_source.value if doc.analysis_source else "unknown",
            companion_endpoint=companion_endpoint,
            companion_model=companion_model,
            primary_result=primary_result,
            companion_result=companion_result,
            divergence=divergence,
        )
        write_shadow_record(record, output_path)
        records.append(record)

    return records
