"""Structured LLM output validation and sanitization.

Provides post-parse validation helpers that enforce business rules
beyond Pydantic's field-level constraints.
"""

from __future__ import annotations

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.enums import SentimentLabel


def validate_llm_output(output: LLMAnalysisOutput) -> list[str]:
    """Return a list of validation warnings for an LLMAnalysisOutput.

    Does not raise — callers decide how to handle warnings.
    An empty list means the output is fully consistent.
    """
    warnings: list[str] = []

    # Sentiment consistency: label vs score direction
    if output.sentiment_label == SentimentLabel.BULLISH and output.sentiment_score < 0:
        warnings.append(
            f"sentiment_label=BULLISH but sentiment_score={output.sentiment_score:.2f} is negative"
        )
    if output.sentiment_label == SentimentLabel.BEARISH and output.sentiment_score > 0:
        warnings.append(
            f"sentiment_label=BEARISH but sentiment_score={output.sentiment_score:.2f} is positive"
        )

    # High spam but high priority is suspicious
    if output.spam_probability > 0.7 and output.recommended_priority > 5:
        warnings.append(
            f"spam_probability={output.spam_probability:.2f} is high "
            f"but recommended_priority={output.recommended_priority}"
        )

    # Actionable with low relevance is inconsistent
    if output.actionable and output.relevance_score < 0.3:
        warnings.append(
            f"actionable=True but relevance_score={output.relevance_score:.2f} is low"
        )

    # High priority without reasoning
    if output.recommended_priority >= 8 and not output.short_reasoning:
        warnings.append("recommended_priority >= 8 but short_reasoning is missing")

    return warnings


def sanitize_scores(output: LLMAnalysisOutput) -> LLMAnalysisOutput:
    """Return a copy of output with scores clamped to valid ranges.

    Handles edge cases where the LLM exceeds declared bounds despite
    response_format constraints (e.g. floating point near-misses).
    """

    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    return output.model_copy(
        update={
            "sentiment_score": _clamp(output.sentiment_score, -1.0, 1.0),
            "relevance_score": _clamp(output.relevance_score, 0.0, 1.0),
            "impact_score": _clamp(output.impact_score, 0.0, 1.0),
            "confidence_score": _clamp(output.confidence_score, 0.0, 1.0),
            "novelty_score": _clamp(output.novelty_score, 0.0, 1.0),
            "spam_probability": _clamp(output.spam_probability, 0.0, 1.0),
            "recommended_priority": max(1, min(10, output.recommended_priority)),
        }
    )
