"""Dataset export module for Companion Model ML tuning."""

import json
from pathlib import Path

from app.core.domain.document import CanonicalDocument


def export_training_data(documents: list[CanonicalDocument], output_path: Path) -> int:
    """Export fully analyzed documents to JSONL instruction format for fine-tuning.

    Format: {"messages": [{"role": "user", "content": "..."}, {"role": "assistant"}]}
    """
    count = 0
    with output_path.open("w", encoding="utf-8") as f:
        for doc in documents:
            if not doc.is_analyzed:
                continue

            text_block = doc.cleaned_text or doc.raw_text or ""
            if not text_block:
                continue

            # Build the instruction payload
            user_content = (
                f"Analyze the following financial document and determine sentiment, "
                f"relevance, impact, novelty, and priority score (1-10).\n\n"
                f"Title: {doc.title}\n"
                f"Source: {doc.source_name or 'Unknown'}\n\n"
                f"Content:\n{text_block}"
            )

            # Build the expected target outputs from the saved LLM scores (Teacher Output)
            target_data = {}
            if "explanation_short" in doc.metadata:
                target_data["co_thought"] = doc.metadata["explanation_short"]
                
            target_data.update({
                "sentiment_label": doc.sentiment_label.value if doc.sentiment_label else "neutral",
                "sentiment_score": doc.sentiment_score or 0.0,
                "relevance_score": doc.relevance_score or 0.0,
                "impact_score": doc.impact_score or 0.0,
                "novelty_score": doc.novelty_score or 0.0,
                "priority_score": doc.priority_score or 1,
                "spam_probability": doc.spam_probability or 0.0,
                "market_scope": doc.market_scope.value if doc.market_scope else "unknown",
            })

            row = {
                "messages": [
                    {"role": "system", "content": "You are a highly precise financial AI analyst."},
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": json.dumps(target_data)},
                ],
                "metadata": {
                    "document_id": str(doc.id),
                    "provider": doc.provider or "unknown",
                }
            }

            f.write(json.dumps(row) + "\n")
            count += 1

    return count
