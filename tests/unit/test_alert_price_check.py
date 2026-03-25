"""Tests for app.alerts.price_check — outcome suggestion logic."""

from __future__ import annotations

from app.alerts.price_check import PriceCheckResult, _suggest_outcome


class TestSuggestOutcome:

    """Unit tests for the _suggest_outcome deterministic logic."""

    def test_bullish_hit(self) -> None:
        outcome, reason = _suggest_outcome("bullish", 7.5, 5.0)
        assert outcome == "hit"
        assert "bullish confirmed" in reason

    def test_bullish_miss(self) -> None:
        outcome, reason = _suggest_outcome("bullish", -6.0, 5.0)
        assert outcome == "miss"
        assert "bullish missed" in reason

    def test_bearish_hit(self) -> None:
        outcome, reason = _suggest_outcome("bearish", -8.0, 5.0)
        assert outcome == "hit"
        assert "bearish confirmed" in reason

    def test_bearish_miss(self) -> None:
        outcome, reason = _suggest_outcome("bearish", 6.0, 5.0)
        assert outcome == "miss"
        assert "bearish missed" in reason

    def test_inconclusive_within_threshold(self) -> None:
        outcome, reason = _suggest_outcome("bullish", 2.0, 5.0)
        assert outcome == "inconclusive"
        assert "threshold" in reason

    def test_inconclusive_neutral_sentiment(self) -> None:
        outcome, reason = _suggest_outcome("neutral", 10.0, 5.0)
        assert outcome == "inconclusive"
        assert "non-directional" in reason

    def test_bearish_exactly_at_threshold(self) -> None:
        outcome, _ = _suggest_outcome("bearish", -5.0, 5.0)
        assert outcome == "hit"

    def test_bullish_exactly_at_threshold(self) -> None:
        outcome, _ = _suggest_outcome("bullish", 5.0, 5.0)
        assert outcome == "hit"

    def test_zero_change_is_inconclusive(self) -> None:
        outcome, _ = _suggest_outcome("bullish", 0.0, 5.0)
        assert outcome == "inconclusive"

    def test_custom_threshold(self) -> None:
        # 3% move with 2% threshold -> hit for bullish
        outcome, _ = _suggest_outcome("bullish", 3.0, 2.0)
        assert outcome == "hit"

    def test_custom_threshold_inconclusive(self) -> None:
        # 1% move with 2% threshold -> inconclusive
        outcome, _ = _suggest_outcome("bullish", 1.0, 2.0)
        assert outcome == "inconclusive"


class TestPriceCheckResult:
    """Verify PriceCheckResult dataclass."""

    def test_creation(self) -> None:
        r = PriceCheckResult(
            document_id="doc-1",
            asset="BTC",
            sentiment_label="bullish",
            current_price=67000.0,
            change_pct_24h=6.5,
            suggested_outcome="hit",
            reason="bullish confirmed: +6.5%",
        )
        assert r.document_id == "doc-1"
        assert r.suggested_outcome == "hit"
        assert r.current_price == 67000.0

    def test_none_price(self) -> None:
        r = PriceCheckResult(
            document_id="doc-2",
            asset="DOGE",
            sentiment_label="bearish",
            current_price=None,
            change_pct_24h=None,
            suggested_outcome="inconclusive",
            reason="price unavailable for DOGE",
        )
        assert r.current_price is None
        assert r.suggested_outcome == "inconclusive"
