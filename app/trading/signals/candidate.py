"""
Signal Candidate Model
=======================
A SignalCandidate is NOT a live trade order.
It is a structured, research-grade output that summarises the evidence for
a potential trading opportunity and recommends a next research step.

Fields map directly to Phase 5 spec requirements:
  - asset, direction_hint, confidence
  - supporting_evidence, contradicting_evidence
  - risk_notes, source_quality
  - historical_context, narrative_label
  - urgency, recommended_next_step
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.enums import (
    DirectionHint,
    DocumentPriority,
    NarrativeLabel,
    SignalUrgency,
)


@dataclass
class SignalCandidate:
    """
    A research-grade signal candidate.

    This is purely informational — no orders are ever placed based solely
    on a SignalCandidate. It feeds into the Research Pack and alert system.
    """

    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str = ""
    source_id: str = ""
    generated_at: datetime = field(default_factory=datetime.utcnow)

    # Core signal fields
    asset: str = ""                                          # e.g. "BTC", "NVDA"
    direction_hint: DirectionHint = DirectionHint.NEUTRAL
    confidence: float = 0.0                                  # 0.0–1.0

    # Evidence
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)

    # Context
    risk_notes: list[str] = field(default_factory=list)
    source_quality: float = 0.5                              # 0.0–1.0 credibility
    historical_context: str = ""                             # e.g. "Similar to FTX collapse 2022"
    narrative_label: NarrativeLabel = NarrativeLabel.UNKNOWN

    # Priority / urgency
    urgency: SignalUrgency = SignalUrgency.MONITOR
    severity: DocumentPriority = DocumentPriority.MEDIUM

    # Action guidance (research only)
    recommended_next_step: str = ""

    # Source metadata
    title: str = ""
    url: str = ""
    sentiment_label: str = "neutral"
    sentiment_score: float = 0.0
    impact_score: float = 0.0
    relevance_score: float = 0.0
    matched_entities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "source_id": self.source_id,
            "generated_at": self.generated_at.isoformat(),
            "asset": self.asset,
            "direction_hint": self.direction_hint.value,
            "confidence": round(self.confidence, 3),
            "supporting_evidence": self.supporting_evidence,
            "contradicting_evidence": self.contradicting_evidence,
            "risk_notes": self.risk_notes,
            "source_quality": round(self.source_quality, 3),
            "historical_context": self.historical_context,
            "narrative_label": self.narrative_label.value,
            "urgency": self.urgency.value,
            "severity": self.severity.value,
            "recommended_next_step": self.recommended_next_step,
            "title": self.title,
            "url": self.url,
            "sentiment_label": self.sentiment_label,
            "sentiment_score": round(self.sentiment_score, 3),
            "impact_score": round(self.impact_score, 3),
            "relevance_score": round(self.relevance_score, 3),
            "matched_entities": self.matched_entities,
        }

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= 0.70

    @property
    def is_actionable(self) -> bool:
        """Research-actionable means confidence ≥ 0.55 and not MONITOR urgency."""
        return self.confidence >= 0.55 and self.urgency != SignalUrgency.MONITOR
