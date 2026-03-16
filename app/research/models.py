"""
Research Pack Models
=====================
Structured research summaries — NOT trade orders.

Four pack types:
  - AssetResearchPack   — all signals and evidence for a single asset
  - NarrativePack       — signals grouped by narrative/theme
  - BreakingNewsPack    — cluster of related breaking news with key facts
  - DailyResearchBrief  — full daily summary across all assets and narratives

All packs are Pydantic models for easy serialisation to JSON/API responses.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import DirectionHint, NarrativeLabel, SignalUrgency


class SignalSummary(BaseModel):
    """Lightweight representation of a SignalCandidate for embedding in packs."""

    signal_id: str
    asset: str
    direction_hint: str
    confidence: float
    urgency: str
    narrative_label: str
    title: str
    recommended_next_step: str
    risk_notes: list[str] = Field(default_factory=list)

    class Config:
        use_enum_values = True


class AssetResearchPack(BaseModel):
    """
    All available signals and evidence for a single tradeable asset.

    Built by ResearchPackBuilder.for_asset(symbol, candidates).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    asset: str
    direction_consensus: DirectionHint = DirectionHint.NEUTRAL
    overall_confidence: float = 0.0
    signals: list[SignalSummary] = Field(default_factory=list)
    top_supporting_evidence: list[str] = Field(default_factory=list)
    top_contradicting_evidence: list[str] = Field(default_factory=list)
    key_risk_notes: list[str] = Field(default_factory=list)
    narrative_labels: list[str] = Field(default_factory=list)
    urgency: SignalUrgency = SignalUrgency.MONITOR
    total_documents: int = 0
    sources: list[str] = Field(default_factory=list)

    class Config:
        use_enum_values = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "generated_at": self.generated_at.isoformat(),
            "asset": self.asset,
            "direction_consensus": self.direction_consensus,
            "overall_confidence": round(self.overall_confidence, 3),
            "urgency": self.urgency,
            "signals": [s.model_dump() for s in self.signals],
            "top_supporting_evidence": self.top_supporting_evidence,
            "top_contradicting_evidence": self.top_contradicting_evidence,
            "key_risk_notes": self.key_risk_notes,
            "narrative_labels": self.narrative_labels,
            "total_documents": self.total_documents,
            "sources": self.sources,
        }


class NarrativePack(BaseModel):
    """
    Signals grouped by a common narrative/theme (e.g. REGULATORY_RISK).

    Built by ResearchPackBuilder.for_narrative(label, candidates).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    narrative_label: NarrativeLabel
    title: str = ""
    summary: str = ""
    affected_assets: list[str] = Field(default_factory=list)
    signals: list[SignalSummary] = Field(default_factory=list)
    overall_confidence: float = 0.0
    dominant_direction: DirectionHint = DirectionHint.NEUTRAL
    urgency: SignalUrgency = SignalUrgency.MONITOR
    risk_notes: list[str] = Field(default_factory=list)

    class Config:
        use_enum_values = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "generated_at": self.generated_at.isoformat(),
            "narrative_label": self.narrative_label,
            "title": self.title,
            "summary": self.summary,
            "affected_assets": self.affected_assets,
            "signals": [s.model_dump() for s in self.signals],
            "overall_confidence": round(self.overall_confidence, 3),
            "dominant_direction": self.dominant_direction,
            "urgency": self.urgency,
            "risk_notes": self.risk_notes,
        }


class BreakingNewsPack(BaseModel):
    """
    Cluster of related breaking news items around a single event.

    Built by ResearchPackBuilder.for_breaking_cluster(documents).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    cluster_title: str
    key_facts: list[str] = Field(default_factory=list)
    affected_assets: list[str] = Field(default_factory=list)
    signals: list[SignalSummary] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    max_impact_score: float = 0.0
    urgency: SignalUrgency = SignalUrgency.SHORT_TERM
    direction_hint: DirectionHint = DirectionHint.NEUTRAL
    risk_notes: list[str] = Field(default_factory=list)
    document_count: int = 0

    class Config:
        use_enum_values = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "generated_at": self.generated_at.isoformat(),
            "cluster_title": self.cluster_title,
            "key_facts": self.key_facts,
            "affected_assets": self.affected_assets,
            "signals": [s.model_dump() for s in self.signals],
            "sources": self.sources,
            "max_impact_score": round(self.max_impact_score, 3),
            "urgency": self.urgency,
            "direction_hint": self.direction_hint,
            "risk_notes": self.risk_notes,
            "document_count": self.document_count,
        }


class DailyResearchBrief(BaseModel):
    """
    Full daily research summary — top signals, narratives, assets.

    Built by ResearchPackBuilder.daily_brief(all_candidates).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    date: str = ""                              # "2024-01-15"
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    total_signals: int = 0
    top_assets: list[AssetResearchPack] = Field(default_factory=list)
    active_narratives: list[NarrativePack] = Field(default_factory=list)
    breaking_clusters: list[BreakingNewsPack] = Field(default_factory=list)
    market_sentiment: str = "neutral"           # positive / neutral / negative
    overall_urgency: SignalUrgency = SignalUrgency.MONITOR
    key_themes: list[str] = Field(default_factory=list)
    risk_summary: list[str] = Field(default_factory=list)
    watchlist_hits: list[str] = Field(default_factory=list)   # assets that had hits

    class Config:
        use_enum_values = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "generated_at": self.generated_at.isoformat(),
            "total_signals": self.total_signals,
            "market_sentiment": self.market_sentiment,
            "overall_urgency": self.overall_urgency,
            "key_themes": self.key_themes,
            "risk_summary": self.risk_summary,
            "watchlist_hits": self.watchlist_hits,
            "top_assets": [a.to_dict() for a in self.top_assets],
            "active_narratives": [n.to_dict() for n in self.active_narratives],
            "breaking_clusters": [b.to_dict() for b in self.breaking_clusters],
        }
