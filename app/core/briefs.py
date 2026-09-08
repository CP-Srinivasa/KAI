"""Structured Research Brief generation from analyzed documents."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

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
    analysis_source: str


class ResearchBrief(BaseModel):
    """Aggregated research snapshot for a specific cluster."""

    cluster_name: str
    title: str
    summary: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    window_start: datetime | None = None
    window_end: datetime | None = None
    newest_source_timestamp: datetime | None = None
    oldest_source_timestamp: datetime | None = None
    data_state: Literal["current", "no_current_data"] = "no_current_data"
    source_timestamp_policy: str = "published_at; timezone-naive SQLite values interpreted as UTC"
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
            f"**Report window:** {self.window_start} to {self.window_end}",
            f"**Data state:** {self.data_state}",
            f"**Source timestamps:** {self.oldest_source_timestamp}"
            f" to {self.newest_source_timestamp}",
            f"**Timestamp policy:** {self.source_timestamp_policy}",
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

    def build(
        self,
        documents: list[CanonicalDocument],
        *,
        window_hours: int = 24,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> ResearchBrief:
        generated_at = now or datetime.now(UTC)
        if generated_at.tzinfo is None or not 1 <= window_hours <= 720:
            raise ValueError("An aware clock and window_hours between 1 and 720 are required")
        generated_at = generated_at.astimezone(UTC)
        start = generated_at - timedelta(hours=window_hours)
        # SQLite drops timezone information on read. Interpret stored naive
        # publication times as UTC explicitly; never substitute fetched_at.
        documents = [
            d.model_copy(update={"published_at": d.published_at.replace(tzinfo=UTC)})
            if d.published_at is not None and d.published_at.tzinfo is None
            else d
            for d in documents
        ]
        valid_docs = [
            document
            for document in documents
            if document.is_analyzed
            and document.published_at is not None
            and document.published_at.tzinfo is not None
            and start <= document.published_at <= generated_at
        ]
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive")
            valid_docs = valid_docs[:limit]

        if not valid_docs:
            return ResearchBrief(
                cluster_name=self.cluster_name,
                title=f"Research Brief: {self.cluster_name}",
                summary="No current analyzed documents in the report window.",
                generated_at=generated_at,
                window_start=start,
                window_end=generated_at,
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
            generated_at=generated_at,
            window_start=start,
            window_end=generated_at,
            newest_source_timestamp=max(b.published_at for b in briefs if b.published_at),
            oldest_source_timestamp=min(b.published_at for b in briefs if b.published_at),
            data_state="current",
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
            analysis_source=document.effective_analysis_source.value,
        )

    def _rank_terms(self, values: Iterable[object], *, limit: int = 5) -> list[BriefFacet]:
        counter: Counter[str] = Counter()
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
