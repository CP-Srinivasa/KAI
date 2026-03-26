"""Tests for app.alerts.price_check — outcome suggestion logic."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.alerts.audit import AlertAuditRecord
from app.alerts.price_check import PriceCheckResult, _suggest_outcome, check_alert_price_moves
from app.market_data.models import Ticker


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


@pytest.mark.asyncio
async def test_check_alert_price_moves_prefers_historical_window() -> None:
    rec = AlertAuditRecord(
        document_id="doc-1",
        channel="telegram",
        message_id="dry_run",
        is_digest=False,
        dispatched_at="2026-03-20T12:00:00+00:00",
        sentiment_label="bullish",
        affected_assets=["BTC"],
    )

    with (
        patch(
            "app.alerts.price_check.CoinGeckoAdapter.get_price_change_between",
            new=AsyncMock(return_value=(100.0, 108.0, 8.0)),
        ) as mock_hist,
        patch(
            "app.alerts.price_check.CoinGeckoAdapter.get_ticker",
            new=AsyncMock(),
        ) as mock_ticker,
    ):
        out = await check_alert_price_moves([rec], threshold_pct=5.0, horizon_hours=24)

    assert len(out) == 1
    assert out[0].suggested_outcome == "hit"
    assert out[0].evaluation_mode == "historical_window"
    assert out[0].observed_move_pct == 8.0
    assert out[0].price_at_alert == 100.0
    assert out[0].price_at_horizon == 108.0
    mock_hist.assert_called_once()
    mock_ticker.assert_not_called()


@pytest.mark.asyncio
async def test_check_alert_price_moves_falls_back_to_ticker() -> None:
    rec = AlertAuditRecord(
        document_id="doc-2",
        channel="telegram",
        message_id="dry_run",
        is_digest=False,
        dispatched_at="2026-03-20T12:00:00+00:00",
        sentiment_label="bearish",
        affected_assets=["ETH"],
    )
    ticker = Ticker(
        symbol="ETH/USDT",
        timestamp_utc="2026-03-21T12:00:00+00:00",
        bid=3000.0,
        ask=3000.0,
        last=3000.0,
        volume_24h=1.0,
        change_pct_24h=-6.5,
    )

    with (
        patch(
            "app.alerts.price_check.CoinGeckoAdapter.get_price_change_between",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.alerts.price_check.CoinGeckoAdapter.get_ticker",
            new=AsyncMock(return_value=ticker),
        ),
    ):
        out = await check_alert_price_moves([rec], threshold_pct=5.0, horizon_hours=24)

    assert len(out) == 1
    assert out[0].suggested_outcome == "hit"
    assert out[0].evaluation_mode == "ticker_24h_fallback"
    assert out[0].observed_move_pct == -6.5
    assert "fallback=ticker_24h" in out[0].reason


@pytest.mark.asyncio
async def test_check_alert_price_moves_inconclusive_when_horizon_not_elapsed() -> None:
    rec = AlertAuditRecord(
        document_id="doc-3",
        channel="telegram",
        message_id="dry_run",
        is_digest=False,
        dispatched_at=datetime.now(UTC).isoformat(),
        sentiment_label="bullish",
        affected_assets=["BTC"],
    )

    with (
        patch(
            "app.alerts.price_check.CoinGeckoAdapter.get_price_change_between",
            new=AsyncMock(),
        ) as mock_hist,
        patch(
            "app.alerts.price_check.CoinGeckoAdapter.get_ticker",
            new=AsyncMock(),
        ) as mock_ticker,
    ):
        out = await check_alert_price_moves([rec], threshold_pct=5.0, horizon_hours=24)

    assert len(out) == 1
    assert out[0].suggested_outcome == "inconclusive"
    assert "horizon not elapsed" in out[0].reason
    mock_hist.assert_not_called()
    mock_ticker.assert_not_called()
