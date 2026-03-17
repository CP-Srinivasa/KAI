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
Spam penalty: if spam_probability > 0.7 → priority capped at 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.domain.document import AnalysisResult

# Score weights (must sum to 1.0)
_W_RELEVANCE: float = 0.30
_W_IMPACT: float = 0.30
_W_NOVELTY: float = 0.20
_W_ACTIONABLE: float = 0.15
_W_QUALITY: float = 0.05

_SPAM_CAP_THRESHOLD: float = 0.70
_SPAM_PRIORITY_CAP: int = 3


@dataclass(frozen=True)
class PriorityScore:
    priority: int  # 1–10, 10 = most urgent
    raw_score: float  # 0.0–1.0 before rounding
    is_spam_capped: bool
    actionable_bonus_applied: bool


def compute_priority(result: AnalysisResult) -> PriorityScore:
    """Compute a priority score (1–10) from an AnalysisResult.

    Returns a PriorityScore with the integer priority and audit info.
    """
    actionable_value = 1.0 if result.actionable else 0.0
    quality = 1.0 - result.spam_probability

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

    # Spam cap
    spam_capped = result.spam_probability > _SPAM_CAP_THRESHOLD
    if spam_capped:
        priority = min(priority, _SPAM_PRIORITY_CAP)

    return PriorityScore(
        priority=priority,
        raw_score=round(raw, 4),
        is_spam_capped=spam_capped,
        actionable_bonus_applied=bonus_applied,
    )


def is_alert_worthy(result: AnalysisResult, min_priority: int = 7) -> bool:
    """Return True if document meets the minimum priority threshold for alerts.

    Spam is always excluded regardless of min_priority.
    """
    if result.spam_probability > _SPAM_CAP_THRESHOLD:
        return False
    score = compute_priority(result)
    return score.priority >= min_priority


def calculate_final_relevance(
    llm_relevance: float,
    keyword_hits: list[Any]
) -> float:
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

