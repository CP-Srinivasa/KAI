"""Priority scoring for AnalysisResult.

Computes a single priority integer (1–10) from the scored fields
of an AnalysisResult. Used to rank documents for alerts and research packs.

Formula (weighted sum → mapped to [1, 10]):
  relevance  × 0.30   — is this even about our topics?
  impact     × 0.30   — what's the potential market effect?
  novelty    × 0.20   — is this new information?
  actionable × 0.15   — does it require a decision?
  (1-spam)   × 0.05   — quality signal

Actionability bonus: +1 to final priority if result.actionable is True.
Sentiment-clarity penalty: -2 to final priority if sentiment_label is
NEUTRAL or MIXED. Resolves DS-20260520-NEW-1: empirical Cross-Tab over
1826 alert_audit entries showed p≥10 was only 43.5% directional vs
p=8/9 87.8% — the prior formula had no clarity term, so asset-relevant
neutral regulatory news could reach p=10. Applied AFTER actionable bonus
but BEFORE spam cap, so spam cap still binds as final floor for spam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel

# Score weights (must sum to 1.0)
_W_RELEVANCE: float = 0.30
_W_IMPACT: float = 0.30
_W_NOVELTY: float = 0.20
_W_ACTIONABLE: float = 0.15
_W_QUALITY: float = 0.05

_SPAM_CAP_THRESHOLD: float = 0.70
_SPAM_PRIORITY_CAP: int = 3

_SENTIMENT_CLARITY_PENALTY: int = 2
_SENTIMENT_PENALTY_LABELS: frozenset[SentimentLabel] = frozenset(
    {SentimentLabel.NEUTRAL, SentimentLabel.MIXED}
)


@dataclass(frozen=True)
class PriorityScore:
    priority: int  # 1–10, 10 = most urgent
    raw_score: float  # 0.0–1.0 before rounding
    is_spam_capped: bool
    actionable_bonus_applied: bool
    is_sentiment_penalized: bool


def compute_priority(
    result: AnalysisResult,
    *,
    spam_probability: float = 0.0,
) -> PriorityScore:
    """Compute a priority score (1–10) from an AnalysisResult.

    spam_probability MUST be passed as an explicit parameter — even though
    AnalysisResult carries a spam_probability field, callers must supply it
    separately to make the scoring input auditable and independent of result
    mutation order (apply_to_document() may update result fields in-place).
    Returns a PriorityScore with the integer priority and audit info.
    """
    actionable_value = 1.0 if result.actionable else 0.0
    quality = 1.0 - spam_probability

    raw = (
        result.relevance_score * _W_RELEVANCE
        + result.impact_score * _W_IMPACT
        + result.novelty_score * _W_NOVELTY
        + actionable_value * _W_ACTIONABLE
        + quality * _W_QUALITY
    )

    # Map [0.0, 1.0] → [1, 10]
    priority = max(1, min(10, round(raw * 9) + 1))

    # Actionability bonus
    bonus_applied = result.actionable and priority < 10
    if bonus_applied:
        priority = min(10, priority + 1)

    # Sentiment-clarity penalty (DS-20260520-NEW-1): neutral/mixed labels
    # carry no directional information, so they should not reach high
    # priorities on the back of topic/impact alone.
    sentiment_penalized = result.sentiment_label in _SENTIMENT_PENALTY_LABELS
    if sentiment_penalized:
        priority = max(1, priority - _SENTIMENT_CLARITY_PENALTY)

    # Spam cap — runs last so it binds as the final floor for spam regardless
    # of prior bonus/penalty interaction.
    spam_capped = spam_probability > _SPAM_CAP_THRESHOLD
    if spam_capped:
        priority = min(priority, _SPAM_PRIORITY_CAP)

    return PriorityScore(
        priority=priority,
        raw_score=round(raw, 4),
        is_spam_capped=spam_capped,
        actionable_bonus_applied=bonus_applied,
        is_sentiment_penalized=sentiment_penalized,
    )


def is_alert_worthy(
    result: AnalysisResult,
    min_priority: int = 7,
    *,
    spam_probability: float = 0.0,
) -> bool:
    """Return True if document meets the minimum priority threshold for alerts.

    spam_probability must be passed separately — it is not stored on AnalysisResult.
    Spam is always excluded regardless of min_priority.
    """
    if spam_probability > _SPAM_CAP_THRESHOLD:
        return False
    score = compute_priority(result, spam_probability=spam_probability)
    return score.priority >= min_priority


def calculate_final_relevance(llm_relevance: float, keyword_hits: list[Any]) -> float:
    """Blend LLM relevance score with keyword hit multipliers.

    If document has strong keyword hits, it boosts the LLM base score.
    """
    if not keyword_hits:
        return llm_relevance

    # Calculate keyword density/weight
    # Simple approach: each hit adds 0.05, max +0.3 boost
    boost = min(0.3, len(keyword_hits) * 0.05)

    final_score = llm_relevance + boost
    return min(1.0, final_score)
