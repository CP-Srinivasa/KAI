"""
Tests for LLMAnalysisOutput schema validation and mock OpenAI provider.
Extends the existing test_llm_output.py — focuses on edge cases and mock integration.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.analysis.llm.base import LLMAnalysisOutput, UsageRecord
from app.core.enums import (
    DocumentPriority,
    EventType,
    MarketScope,
    SentimentLabel,
)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _valid_output() -> dict:
    return {
        "sentiment_label": "positive",
        "sentiment_score": 0.75,
        "relevance_score": 0.8,
        "impact_score": 0.6,
        "confidence_score": 0.7,
        "novelty_score": 0.5,
        "spam_probability": 0.05,
        "market_scope": "crypto",
        "affected_assets": ["BTC", "ETH"],
        "affected_sectors": ["DeFi"],
        "event_type": "regulatory",
        "bull_case": "Regulatory clarity boosts adoption",
        "bear_case": "Overly strict rules could stifle growth",
        "neutral_case": "Market absorbs news within days",
        "historical_analogs": ["Bitcoin ETF approval 2024"],
        "recommended_priority": "high",
        "actionable": True,
        "tags": ["bitcoin", "regulation"],
        "explanation_short": "SEC approves Bitcoin ETF application",
        "explanation_long": "The Securities and Exchange Commission has approved...",
    }


# ──────────────────────────────────────────────
# Schema Validation
# ──────────────────────────────────────────────

class TestLLMAnalysisOutputValidation:
    def test_valid_output_parses(self) -> None:
        output = LLMAnalysisOutput(**_valid_output())
        assert output.sentiment_label == SentimentLabel.POSITIVE
        assert output.sentiment_score == pytest.approx(0.75)

    def test_missing_required_field_fails(self) -> None:
        data = _valid_output()
        del data["sentiment_label"]
        with pytest.raises(ValidationError):
            LLMAnalysisOutput(**data)

    def test_sentiment_score_out_of_range_fails(self) -> None:
        data = _valid_output()
        data["sentiment_score"] = 1.5  # > 1.0
        with pytest.raises(ValidationError):
            LLMAnalysisOutput(**data)

    def test_sentiment_score_negative_boundary(self) -> None:
        data = _valid_output()
        data["sentiment_score"] = -1.0
        output = LLMAnalysisOutput(**data)
        assert output.sentiment_score == -1.0

    def test_impact_score_zero_valid(self) -> None:
        data = _valid_output()
        data["impact_score"] = 0.0
        output = LLMAnalysisOutput(**data)
        assert output.impact_score == 0.0

    def test_empty_explanation_short_fails(self) -> None:
        data = _valid_output()
        data["explanation_short"] = "   "  # Whitespace only
        with pytest.raises(ValidationError):
            LLMAnalysisOutput(**data)

    def test_explanation_short_stripped(self) -> None:
        data = _valid_output()
        data["explanation_short"] = "  SEC approves ETF  "
        output = LLMAnalysisOutput(**data)
        assert output.explanation_short == "SEC approves ETF"

    def test_sentiment_score_rounded(self) -> None:
        data = _valid_output()
        data["sentiment_score"] = 0.123456789
        output = LLMAnalysisOutput(**data)
        assert output.sentiment_score == pytest.approx(0.1235, abs=1e-4)

    def test_all_scores_boundary_zero(self) -> None:
        data = _valid_output()
        for field in ["relevance_score", "impact_score", "confidence_score", "novelty_score", "spam_probability"]:
            data[field] = 0.0
        output = LLMAnalysisOutput(**data)
        assert output.relevance_score == 0.0

    def test_all_scores_boundary_one(self) -> None:
        data = _valid_output()
        for field in ["relevance_score", "impact_score", "confidence_score", "novelty_score", "spam_probability"]:
            data[field] = 1.0
        output = LLMAnalysisOutput(**data)
        assert output.spam_probability == 1.0

    def test_invalid_market_scope_fails(self) -> None:
        data = _valid_output()
        data["market_scope"] = "invalid_scope"
        with pytest.raises(ValidationError):
            LLMAnalysisOutput(**data)

    def test_invalid_event_type_fails(self) -> None:
        data = _valid_output()
        data["event_type"] = "completely_made_up"
        with pytest.raises(ValidationError):
            LLMAnalysisOutput(**data)

    def test_invalid_priority_fails(self) -> None:
        data = _valid_output()
        data["recommended_priority"] = "ultra_critical"
        with pytest.raises(ValidationError):
            LLMAnalysisOutput(**data)

    def test_empty_lists_valid(self) -> None:
        data = _valid_output()
        data["affected_assets"] = []
        data["tags"] = []
        data["historical_analogs"] = []
        output = LLMAnalysisOutput(**data)
        assert output.affected_assets == []

    def test_json_roundtrip(self) -> None:
        output = LLMAnalysisOutput(**_valid_output())
        serialized = output.model_dump_json()
        restored = LLMAnalysisOutput.model_validate_json(serialized)
        assert restored.sentiment_label == output.sentiment_label


# ──────────────────────────────────────────────
# UsageRecord
# ──────────────────────────────────────────────

class TestUsageRecord:
    def test_defaults(self) -> None:
        usage = UsageRecord(provider="openai", model="gpt-4o")
        assert usage.prompt_tokens == 0
        assert usage.cost_usd == 0.0
        assert usage.success is True

    def test_error_record(self) -> None:
        usage = UsageRecord(
            provider="openai", model="gpt-4o",
            success=False, error="timeout"
        )
        assert usage.success is False
        assert usage.error == "timeout"

    def test_timestamp_set(self) -> None:
        from datetime import datetime
        usage = UsageRecord(provider="openai", model="gpt-4o")
        assert isinstance(usage.timestamp, datetime)


# ──────────────────────────────────────────────
# Mock OpenAI Provider Integration
# ──────────────────────────────────────────────

class TestOpenAIProviderMock:
    """
    Tests OpenAIProvider with mocked API calls.
    No real API keys required.
    """

    def _make_mock_response(self, content: dict | None = None) -> MagicMock:
        """Build a mock OpenAI response object."""
        raw_content = json.dumps(content or _valid_output())
        mock_choice = MagicMock()
        mock_choice.message.content = raw_content

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 100
        mock_usage.completion_tokens = 200

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        return mock_response

    def _make_doc(self) -> MagicMock:
        from datetime import datetime
        doc = MagicMock()
        doc.id = "test-doc-id"
        doc.title = "Bitcoin ETF Approved"
        doc.source_name = "CoinDesk"
        doc.source_type.value = "rss_feed"
        doc.published_at = datetime(2024, 1, 10)
        doc.cleaned_text = "The SEC has approved the Bitcoin ETF application..."
        doc.raw_text = doc.cleaned_text
        doc.summary = ""
        return doc

    @pytest.mark.asyncio
    async def test_analyze_document_returns_analysis_result(self) -> None:
        from app.integrations.openai.provider import OpenAIProvider
        from app.core.domain.document import AnalysisResult

        provider = OpenAIProvider(api_key="sk-fake-key-for-testing")
        mock_response = self._make_mock_response()

        with patch.object(
            provider._client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await provider.analyze_document(self._make_doc())

        assert isinstance(result, AnalysisResult)
        assert result.sentiment_label == SentimentLabel.POSITIVE
        assert result.sentiment_score == pytest.approx(0.75)
        assert result.analyzed_by == "openai"

    @pytest.mark.asyncio
    async def test_analyze_document_records_usage(self) -> None:
        from app.integrations.openai.provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-fake-key")
        mock_response = self._make_mock_response()

        with patch.object(
            provider._client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            await provider.analyze_document(self._make_doc())

        stats = provider.get_usage_stats()
        assert stats["total_calls"] == 1
        assert stats["successful_calls"] == 1
        assert stats["daily_cost_usd"] > 0.0

    @pytest.mark.asyncio
    async def test_invalid_json_raises_validation_error(self) -> None:
        from app.integrations.openai.provider import OpenAIProvider
        from app.core.errors import LLMOutputValidationError

        provider = OpenAIProvider(api_key="sk-fake-key")

        bad_choice = MagicMock()
        bad_choice.message.content = "not valid json at all {{"
        bad_usage = MagicMock()
        bad_usage.prompt_tokens = 10
        bad_usage.completion_tokens = 5
        bad_response = MagicMock()
        bad_response.choices = [bad_choice]
        bad_response.usage = bad_usage

        with patch.object(
            provider._client.chat.completions,
            "create",
            new=AsyncMock(return_value=bad_response),
        ):
            with pytest.raises(LLMOutputValidationError):
                await provider.analyze_document(self._make_doc())

    @pytest.mark.asyncio
    async def test_cost_limit_exceeded_raises(self) -> None:
        from app.integrations.openai.provider import OpenAIProvider
        from app.core.errors import LLMCostLimitError

        provider = OpenAIProvider(api_key="sk-fake-key", cost_limit_usd_per_day=0.0)
        provider._daily_cost_usd = 0.01  # Already over limit

        with pytest.raises(LLMCostLimitError):
            await provider.analyze_document(self._make_doc())

    @pytest.mark.asyncio
    async def test_summarize_document_returns_string(self) -> None:
        from app.integrations.openai.provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-fake-key")

        mock_choice = MagicMock()
        mock_choice.message.content = "Brief summary of the document."
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        with patch.object(
            provider._client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            summary = await provider.summarize_document(self._make_doc())

        assert isinstance(summary, str)
        assert len(summary) > 0
