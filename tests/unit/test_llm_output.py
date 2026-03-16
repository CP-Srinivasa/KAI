"""Tests for LLM output schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.analysis.llm.base import LLMAnalysisOutput
from app.core.enums import DocumentPriority, EventType, MarketScope, SentimentLabel


def valid_output() -> dict:
    return {
        "sentiment_label": "positive", "sentiment_score": 0.75,
        "relevance_score": 0.8, "impact_score": 0.6,
        "confidence_score": 0.9, "novelty_score": 0.5, "spam_probability": 0.02,
        "market_scope": "crypto", "affected_assets": ["BTC", "ETH"],
        "affected_sectors": ["DeFi"], "event_type": "regulatory",
        "bull_case": "Regulation brings clarity", "bear_case": "Overregulation stifles",
        "neutral_case": "Market awaits details", "historical_analogs": ["2017 China ban"],
        "recommended_priority": "high", "actionable": True,
        "tags": ["regulation", "crypto"],
        "explanation_short": "Positive regulatory news for crypto market",
        "explanation_long": "The SEC announced a new framework...",
    }


class TestLLMAnalysisOutput:
    def test_valid_output(self) -> None:
        out = LLMAnalysisOutput.model_validate(valid_output())
        assert out.sentiment_label == SentimentLabel.POSITIVE
        assert out.actionable is True

    def test_sentiment_score_out_of_range(self) -> None:
        data = {**valid_output(), "sentiment_score": 1.5}
        with pytest.raises(ValidationError):
            LLMAnalysisOutput.model_validate(data)

    def test_negative_sentiment_score_valid(self) -> None:
        data = {**valid_output(), "sentiment_score": -0.9, "sentiment_label": "negative"}
        assert LLMAnalysisOutput.model_validate(data).sentiment_score == -0.9

    def test_empty_explanation_short_fails(self) -> None:
        with pytest.raises(ValidationError):
            LLMAnalysisOutput.model_validate({**valid_output(), "explanation_short": "   "})

    def test_invalid_sentiment_label(self) -> None:
        with pytest.raises(ValidationError):
            LLMAnalysisOutput.model_validate({**valid_output(), "sentiment_label": "very_positive"})

    def test_score_boundary_values(self) -> None:
        out = LLMAnalysisOutput.model_validate({**valid_output(), "relevance_score": 0.0, "impact_score": 1.0})
        assert out.relevance_score == 0.0
        assert out.impact_score == 1.0
