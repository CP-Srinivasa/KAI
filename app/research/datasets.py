"""Dataset export module for Companion Model ML tuning."""

import json
from pathlib import Path

from app.core.domain.document import CanonicalDocument

_RULE_BASED_PROVIDERS: frozenset[str] = frozenset({"fallback", "rule"})
_INTERNAL_PROVIDERS: frozenset[str] = frozenset({"internal", "companion"})


def _analysis_source(doc: CanonicalDocument) -> str:
    """Derive dataset analysis provenance from the persisted provider tag."""
    provider = (doc.provider or "").strip().lower()
    if not provider or provider in _RULE_BASED_PROVIDERS:
        return "rule"
    if provider in _INTERNAL_PROVIDERS:
        return "internal"
    return "external_llm"


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _build_target_data(doc: CanonicalDocument) -> dict[str, object]:
    return {
        "sentiment_label": doc.sentiment_label.value if doc.sentiment_label else "neutral",
        "sentiment_score": doc.sentiment_score or 0.0,
        "relevance_score": doc.relevance_score or 0.0,
        "impact_score": doc.impact_score or 0.0,
        "novelty_score": doc.novelty_score or 0.0,
        "priority_score": doc.priority_score or 1,
        "spam_probability": doc.spam_probability or 0.0,
        "market_scope": doc.market_scope.value if doc.market_scope else "unknown",
        "summary": doc.summary or "",
        "tags": list(doc.ai_tags),
        "affected_assets": _unique_strings(doc.tickers + doc.crypto_assets),
    }


def _build_export_metadata(doc: CanonicalDocument) -> dict[str, str]:
    return {
        "document_id": str(doc.id),
        "provider": (doc.provider or "unknown").strip() or "unknown",
        "analysis_source": _analysis_source(doc),
    }


def export_training_data(documents: list[CanonicalDocument], output_path: Path) -> int:
    """Export analyzed documents to JSONL with structured training targets.

    The export stays aligned to the current intelligence contract:
    - only analyzed documents are exported
    - only persisted structured labels are exported as assistant targets
    - free-form reasoning fields like chain-of-thought are not part of the format
    - dataset metadata carries provenance (`provider`, `analysis_source`)
    """
    count = 0
    with output_path.open("w", encoding="utf-8") as f:
        for doc in documents:
            if not doc.is_analyzed:
                continue

            text_block = (doc.cleaned_text or doc.raw_text or "").strip()
            if not text_block:
                continue

            user_content = (
                "Analyze the following financial document and determine sentiment, "
                "relevance, impact, novelty, and priority score (1-10).\n\n"
                f"Title: {doc.title}\n"
                f"Source: {doc.source_name or 'Unknown'}\n\n"
                f"Content:\n{text_block}"
            )

            row = {
                "messages": [
                    {"role": "system", "content": "You are a highly precise financial AI analyst."},
                    {"role": "user", "content": user_content},
                    {
                        "role": "assistant",
                        "content": json.dumps(_build_target_data(doc), sort_keys=True),
                    },
                ],
                "metadata": _build_export_metadata(doc),
            }

            f.write(json.dumps(row) + "\n")
            count += 1

    return count
