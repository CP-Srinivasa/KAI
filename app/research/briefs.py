"""Structured Research Brief generation from analyzed documents."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.domain.document import CanonicalDocument
from app.core.enums import SentimentLabel


class BriefFacet(BaseModel):
    """Simple ranked facet used for top assets/entities in a brief."""

    name: str
    count: int


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
    title: str
    summary: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    document_count: int
    average_priority: float
    overall_sentiment: str
    top_documents: list[BriefDocument]
    top_assets: list[BriefFacet]
    top_entities: list[BriefFacet]
    top_actionable_signals: list[BriefDocument]
    key_documents: list[BriefDocument]

    def to_markdown(self) -> str:
        """Render the brief as a Markdown document."""
        lines = [
            f"# {self.title}",
            f"**Generated:** {self.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "## Summary",
            self.summary,
            "",
            f"- **Documents Analyzed:** {self.document_count}",
            f"- **Average Priority:** {self.average_priority:.2f} / 10",
            f"- **Overall Sentiment:** {self.overall_sentiment.capitalize()}",
            "",
            "## Top Assets",
        ]

        if not self.top_assets:
            lines.append("*No top assets detected.*")
        else:
            lines.extend(f"- **{facet.name}** ({facet.count})" for facet in self.top_assets)

        lines.extend(["", "## Top Entities"])

        if not self.top_entities:
            lines.append("*No top entities detected.*")
        else:
            lines.extend(f"- **{facet.name}** ({facet.count})" for facet in self.top_entities)

        lines.extend(
            [
                "",
                "## Actionable Signals",
                "*(High priority alerts requiring attention)*",
                "",
            ]
        )

        if not self.top_actionable_signals:
            lines.append("*No highly actionable signals in this cluster currently.*")
        else:
            for doc in self.top_actionable_signals:
                lines.extend(self._render_brief_doc_md(doc))

        lines.extend(["", "## Top Documents", "*(Ranked by priority)*", ""])

        if not self.top_documents:
            lines.append("*No relevant documents found.*")
        else:
            for doc in self.top_documents:
                lines.extend(self._render_brief_doc_md(doc))

        return "\n".join(lines)

    def _render_brief_doc_md(self, doc: BriefDocument) -> list[str]:
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
        return self.model_dump(mode="json")


class ResearchBriefBuilder:
    """Builds a ResearchBrief from a list of CanonicalDocuments."""

    def __init__(self, cluster_name: str) -> None:
        self.cluster_name = cluster_name

    def build(self, documents: list[CanonicalDocument]) -> ResearchBrief:
        valid_docs = [document for document in documents if document.is_analyzed]

        if not valid_docs:
            return ResearchBrief(
                cluster_name=self.cluster_name,
                title=f"Research Brief: {self.cluster_name}",
                summary="No analyzed documents available for this brief.",
                document_count=0,
                average_priority=0.0,
                overall_sentiment=SentimentLabel.NEUTRAL.value,
                top_documents=[],
                top_assets=[],
                top_entities=[],
                top_actionable_signals=[],
                key_documents=[],
            )

        briefs = [self._to_brief_document(document) for document in valid_docs]
        briefs.sort(
            key=lambda brief: (
                brief.priority_score,
                brief.impact_score,
                brief.published_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )

        average_priority = sum(brief.priority_score for brief in briefs) / len(briefs)
        sentiments = [brief.sentiment_label for brief in briefs if brief.sentiment_label]
        dominant_sentiment = (
            Counter(sentiments).most_common(1)[0][0] if sentiments else SentimentLabel.NEUTRAL.value
        )

        top_assets = self._rank_terms(
            value
            for document in valid_docs
            for value in (document.tickers + document.crypto_assets)
        )
        top_entities = self._rank_terms(
            value
            for document in valid_docs
            for value in (document.entities + document.people + document.organizations)
        )

        actionable = [brief for brief in briefs if brief.actionable]
        non_actionable = [brief for brief in briefs if not brief.actionable]

        return ResearchBrief(
            cluster_name=self.cluster_name,
            title=f"Research Brief: {self.cluster_name}",
            summary=self._build_summary(
                document_count=len(briefs),
                average_priority=average_priority,
                overall_sentiment=dominant_sentiment,
                top_assets=top_assets,
                top_entities=top_entities,
            ),
            document_count=len(briefs),
            average_priority=average_priority,
            overall_sentiment=dominant_sentiment,
            top_documents=briefs[:10],
            top_assets=top_assets,
            top_entities=top_entities,
            top_actionable_signals=actionable[:10],
            key_documents=non_actionable[:20],
        )

    def _to_brief_document(self, document: CanonicalDocument) -> BriefDocument:
        summary = (document.summary or document.title or "").strip()
        if not summary:
            summary = "No summary available."
        return BriefDocument(
            document_id=str(document.id),
            title=document.title or "(No Title)",
            url=document.url,
            priority_score=document.priority_score or 0,
            sentiment_label=(
                document.sentiment_label.value
                if document.sentiment_label
                else SentimentLabel.NEUTRAL.value
            ),
            summary=summary,
            impact_score=document.impact_score or 0.0,
            actionable=bool((document.priority_score or 0) >= 8),
            published_at=document.published_at,
            source_name=document.source_name,
        )

    def _rank_terms(self, values: Any, *, limit: int = 5) -> list[BriefFacet]:
        counter = Counter()
        for value in values:
            normalized = str(value).strip()
            if normalized:
                counter[normalized] += 1
        ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0].lower()))
        return [BriefFacet(name=name, count=count) for name, count in ranked[:limit]]

    def _build_summary(
        self,
        *,
        document_count: int,
        average_priority: float,
        overall_sentiment: str,
        top_assets: list[BriefFacet],
        top_entities: list[BriefFacet],
    ) -> str:
        parts = [
            f"{document_count} analyzed documents",
            f"average priority {average_priority:.1f}/10",
            f"overall sentiment {overall_sentiment}",
        ]
        if top_assets:
            parts.append(f"top asset {top_assets[0].name}")
        if top_entities:
            parts.append(f"top entity {top_entities[0].name}")
        return ", ".join(parts) + "."
