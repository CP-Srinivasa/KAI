"""Research Briefs module.

Aggregates multiple CanonicalDocument models into a structured report
representing a specific Research Cluster (e.g. a Watchlist like 'DeFi' or asset 'BTC').
"""

from __future__ import annotations

import collections
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.domain.document import CanonicalDocument
from app.core.enums import SentimentLabel


class BriefDocument(BaseModel):
    """A condensed view of a CanonicalDocument optimized for reading and export."""

    document_id: str
    title: str
    url: str
    priority_score: int
    sentiment_label: str
    summary: str
    impact_score: float
    actionable: bool
    published_at: datetime | None
    source_name: str | None


class ResearchBrief(BaseModel):
    """Aggregated research snapshot for a specific cluster."""

    cluster_name: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    document_count: int
    average_priority: float
    overall_sentiment: str
    top_actionable_signals: list[BriefDocument]
    key_documents: list[BriefDocument]

    def to_markdown(self) -> str:
        """Render the brief as a Markdown document."""
        lines = [
            f"# Research Brief: {self.cluster_name}",
            f"**Generated:** {self.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "## Summary",
            f"- **Documents Analyzed:** {self.document_count}",
            f"- **Average Priority:** {self.average_priority:.2f} / 10",
            f"- **Overall Sentiment:** {self.overall_sentiment.capitalize()}",
            "",
            "## Actionable Signals",
            "*(High priority alerts requiring attention)*",
            "",
        ]

        if not self.top_actionable_signals:
            lines.append("*No highly actionable signals in this cluster currently.*")
        else:
            for doc in self.top_actionable_signals:
                lines.extend(self._render_brief_doc_md(doc))

        lines.extend(
            [
                "",
                "## Key Documents",
                "*(Ranked by priority)*",
                "",
            ]
        )

        if not self.key_documents:
            lines.append("*No relevant documents found.*")
        else:
            for doc in self.key_documents:
                lines.extend(self._render_brief_doc_md(doc))

        return "\n".join(lines)

    def _render_brief_doc_md(self, doc: BriefDocument) -> list[str]:
        """Render a single brief document as a markdown list item with details."""
        src = doc.source_name or "Unknown Source"
        date_str = doc.published_at.strftime("%Y-%m-%d") if doc.published_at else "---"

        emoji = "🟡"
        if doc.sentiment_label == "bullish":
            emoji = "🟢"
        elif doc.sentiment_label == "bearish":
            emoji = "🔴"

        return [
            f"### [{doc.title}]({doc.url})",
            f"**Source:** {src} | **Date:** {date_str} | **Priority:** {doc.priority_score}"
            f" | **Sentiment:** {emoji} {doc.sentiment_label.capitalize()}",
            "",
            f"> {doc.summary}",
            "",
        ]

    def to_json_dict(self) -> dict[str, Any]:
        """Return dict representation, safe for JSON serialization (by fastapi/pydantic)."""
        return self.model_dump(mode="json")


class ResearchBriefBuilder:
    """Builds a ResearchBrief from a list of CanonicalDocuments."""

    def __init__(self, cluster_name: str) -> None:
        self.cluster_name = cluster_name

    def build(self, documents: list[CanonicalDocument]) -> ResearchBrief:
        # Filter for documents that have actually been analyzed (have priority scores)
        valid_docs = [
            d for d in documents if d.is_analyzed and getattr(d, "priority_score", None) is not None
        ]

        if not valid_docs:
            return ResearchBrief(
                cluster_name=self.cluster_name,
                document_count=0,
                average_priority=0.0,
                overall_sentiment=SentimentLabel.NEUTRAL.value,
                top_actionable_signals=[],
                key_documents=[],
            )

        # Calculate metrics
        total_priority = sum(d.priority_score or 0 for d in valid_docs)
        avg_priority = total_priority / len(valid_docs)

        # Calculate dominant sentiment
        sentiments = [d.sentiment_label.value for d in valid_docs if d.sentiment_label]
        if sentiments:
            counter = collections.Counter(sentiments)
            dominant = counter.most_common(1)[0][0]
        else:
            dominant = SentimentLabel.NEUTRAL.value

        # Map to brief documents
        briefs: list[BriefDocument] = []
        for d in valid_docs:
            briefs.append(
                BriefDocument(
                    document_id=str(d.id),
                    title=d.title or "(No Title)",
                    url=d.url,
                    priority_score=d.priority_score or 0,
                    sentiment_label=d.sentiment_label.value if d.sentiment_label else "neutral",
                    summary=d.summary or d.title[:100] or "",
                    impact_score=d.impact_score or 0.0,
                    actionable=bool(d.priority_score and d.priority_score >= 8),
                    published_at=d.published_at,
                    source_name=d.source_name,
                )
            )

        # Sort by priority
        briefs.sort(key=lambda b: b.priority_score, reverse=True)

        actionable = [b for b in briefs if b.actionable]
        non_actionable = [b for b in briefs if not b.actionable]

        return ResearchBrief(
            cluster_name=self.cluster_name,
            document_count=len(valid_docs),
            average_priority=avg_priority,
            overall_sentiment=dominant,
            top_actionable_signals=actionable,
            key_documents=non_actionable[:20],
        )
