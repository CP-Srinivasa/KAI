"""Tests for D-118: Price Trend Divergence Gate.

Coverage:
- check_price_trend_alignment() — pure function, all sentiment/trend combos
- AlertService.process_document() — trend divergence blocks alert dispatch
- AlertService._check_price_trend_divergence() — fail-open on API error
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.alerts.base.interfaces import AlertDeliveryResult, AlertMessage
from app.alerts.eligibility import check_price_trend_alignment
from app.alerts.service import AlertService
from app.alerts.threshold import ThresholdEngine
from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel
from app.market_data.models import Ticker

# ── check_price_trend_alignment (pure function) ─────────────────────────────


class TestCheckPriceTrendAlignment:
    """D-118: Sentiment must match 24h price direction."""

    def test_bullish_price_rising_aligned(self) -> None:
        assert check_price_trend_alignment("bullish", 3.5) is True

    def test_bullish_price_falling_divergent(self) -> None:
        assert check_price_trend_alignment("bullish", -2.0) is False

    def test_bearish_price_falling_aligned(self) -> None:
        assert check_price_trend_alignment("bearish", -4.1) is True

    def test_bearish_price_rising_divergent(self) -> None:
        assert check_price_trend_alignment("bearish", 1.5) is False

    def test_bullish_zero_change_divergent(self) -> None:
        """Zero change = not rising, so bullish diverges."""
        assert check_price_trend_alignment("bullish", 0.0) is False

    def test_bearish_zero_change_divergent(self) -> None:
        """Zero change = not falling, so bearish diverges."""
        assert check_price_trend_alignment("bearish", 0.0) is False

    def test_neutral_always_passes(self) -> None:
        assert check_price_trend_alignment("neutral", -99.0) is True

    def test_mixed_always_passes(self) -> None:
        assert check_price_trend_alignment("mixed", 50.0) is True

    def test_empty_string_passes(self) -> None:
        assert check_price_trend_alignment("", 10.0) is True

    def test_whitespace_bullish_trimmed(self) -> None:
        assert check_price_trend_alignment("  Bullish  ", 5.0) is True

    def test_case_insensitive(self) -> None:
        assert check_price_trend_alignment("BEARISH", -1.0) is True
        assert check_price_trend_alignment("Bullish", 1.0) is True


# ── D-120: 7d regime gate ────────────────────────────────────────────────────


class TestCheckPriceTrendAlignment7dRegime:
    """D-120: 7d regime overrides 24h alignment when strongly counter-trend."""

    def test_bearish_blocked_in_7d_bull_regime(self) -> None:
        """Bearish + 24h falling BUT 7d strongly bullish -> block."""
        assert check_price_trend_alignment("bearish", -1.0, change_pct_7d=5.0) is False

    def test_bullish_blocked_in_7d_bear_regime(self) -> None:
        """Bullish + 24h rising BUT 7d strongly bearish -> block."""
        assert check_price_trend_alignment("bullish", 2.0, change_pct_7d=-5.0) is False

    def test_bearish_passes_when_7d_below_threshold(self) -> None:
        """Bearish + 24h falling + 7d mildly bullish (below 1.5%) -> pass."""
        assert check_price_trend_alignment("bearish", -2.0, change_pct_7d=1.0) is True

    def test_bullish_passes_when_7d_above_negative_threshold(self) -> None:
        """Bullish + 24h rising + 7d mildly bearish (above -3%) -> pass."""
        assert check_price_trend_alignment("bullish", 1.0, change_pct_7d=-2.0) is True

    def test_bearish_blocked_at_exact_threshold(self) -> None:
        """D-121: Bearish + 7d at exactly +1.5% -> NOT blocked (> required, not >=)."""
        assert check_price_trend_alignment("bearish", -1.0, change_pct_7d=1.5) is True

    def test_bearish_blocked_just_above_threshold(self) -> None:
        """D-121: Bearish + 7d at +1.51% -> blocked."""
        assert check_price_trend_alignment("bearish", -1.0, change_pct_7d=1.51) is False

    def test_neutral_ignores_7d(self) -> None:
        """Neutral sentiment is never blocked by 7d regime."""
        assert check_price_trend_alignment("neutral", 0.0, change_pct_7d=10.0) is True

    def test_zero_7d_no_regime_block(self) -> None:
        """Default 7d=0.0 triggers no regime block (backward compatible)."""
        assert check_price_trend_alignment("bearish", -1.0, change_pct_7d=0.0) is True
        assert check_price_trend_alignment("bullish", 1.0, change_pct_7d=0.0) is True

    def test_custom_regime_threshold(self) -> None:
        """Custom threshold overrides the defaults."""
        # 5% 7d change, but bearish threshold is 10% -> no regime block
        assert check_price_trend_alignment(
            "bearish", -1.0, change_pct_7d=5.0, regime_threshold_7d_bearish=10.0
        ) is True
        # Same change, bearish threshold lowered to 2% -> blocked
        assert check_price_trend_alignment(
            "bearish", -1.0, change_pct_7d=5.0, regime_threshold_7d_bearish=2.0
        ) is False

    def test_asymmetric_bearish_regime_default(self) -> None:
        """D-121: Bearish default threshold (1.5%) is tighter than bullish (3.0%)."""
        # 2% 7d rise: blocks bearish (> 1.5%) but not bullish
        assert check_price_trend_alignment("bearish", -1.0, change_pct_7d=2.0) is False
        # 2% 7d drop: does NOT block bullish (threshold is 3.0%)
        assert check_price_trend_alignment("bullish", 1.0, change_pct_7d=-2.0) is True


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_doc(**overrides) -> CanonicalDocument:
    defaults = {
        "url": f"https://example.com/{uuid.uuid4()}",
        "title": "BTC price analysis: institutional buying accelerates",
    }
    defaults.update(overrides)
    return CanonicalDocument(**defaults)


def _make_result(doc_id: str, **overrides) -> AnalysisResult:
    defaults = {
        "document_id": doc_id,
        "sentiment_label": SentimentLabel.BULLISH,
        "sentiment_score": 0.85,
        "relevance_score": 1.0,
        "impact_score": 0.90,
        "confidence_score": 0.9,
        "novelty_score": 0.8,
        "market_scope": MarketScope.CRYPTO,
        "actionable": True,
        "directional_confidence": 0.9,
        "explanation_short": "Strong buy signal",
        "explanation_long": "Detailed explanation",
        "affected_assets": ["BTC"],
    }
    defaults.update(overrides)
    return AnalysisResult(**defaults)


class _DryRunChannel:
    """Minimal channel stub for testing."""

    channel_name = "dry_run"
    is_enabled = True

    async def send(self, message: AlertMessage) -> AlertDeliveryResult:
        return AlertDeliveryResult(channel="dry_run", success=True, message_id="dry_run")

    async def send_digest(self, messages: list[AlertMessage], period: str) -> AlertDeliveryResult:
        return AlertDeliveryResult(channel="dry_run", success=True, message_id="digest")


# ── AlertService integration: D-118 gate in process_document ────────────────


@pytest.mark.asyncio
async def test_bullish_alert_blocked_when_price_falling(tmp_path: Path) -> None:
    """Bullish sentiment + falling price = divergence = no dispatch."""
    doc = _make_doc()
    result = _make_result(str(doc.id), sentiment_label=SentimentLabel.BULLISH)

    ticker = Ticker(
        symbol="BTC/USDT",
        timestamp_utc="2026-04-04T12:00:00+00:00",
        bid=65000.0,
        ask=65000.0,
        last=65000.0,
        volume_24h=1000.0,
        change_pct_24h=-3.5,  # falling = diverges from bullish
    )

    service = AlertService(
        channels=[_DryRunChannel()],  # type: ignore[list-item]
        threshold=ThresholdEngine(min_priority=1),
        audit_dir=tmp_path,
    )

    with (
        patch(
            "app.market_data.coingecko_adapter._resolve_symbol",
            return_value=("BTC/USDT", "bitcoin"),
        ),
        patch(
            "app.market_data.coingecko_adapter.CoinGeckoAdapter.get_ticker",
            new=AsyncMock(return_value=ticker),
        ),
    ):
        deliveries = await service.process_document(doc, result)

    assert deliveries == [], "Divergent trend should block alert dispatch"


@pytest.mark.asyncio
async def test_bearish_alert_blocked_by_eligibility_gate(tmp_path: Path) -> None:
    """D-146/D-142: Bearish directional alerts are blocked pre-dispatch
    regardless of price alignment while BEARISH_DIRECTIONAL_DISABLED=True."""
    doc = _make_doc(title="BTC selloff deepens as macro fears mount")
    result = _make_result(
        str(doc.id),
        sentiment_label=SentimentLabel.BEARISH,
        sentiment_score=-0.85,
    )

    service = AlertService(
        channels=[_DryRunChannel()],  # type: ignore[list-item]
        threshold=ThresholdEngine(min_priority=1),
        audit_dir=tmp_path,
    )

    deliveries = await service.process_document(doc, result)

    assert deliveries == [], "Bearish alerts blocked by D-146 eligibility gate"


@pytest.mark.asyncio
async def test_price_check_fail_open_on_api_error(tmp_path: Path) -> None:
    """D-118 fail-open: if CoinGecko is unreachable, alert goes through."""
    doc = _make_doc()
    result = _make_result(str(doc.id), sentiment_label=SentimentLabel.BULLISH)

    service = AlertService(
        channels=[_DryRunChannel()],  # type: ignore[list-item]
        threshold=ThresholdEngine(min_priority=1),
        audit_dir=tmp_path,
    )

    with (
        patch(
            "app.market_data.coingecko_adapter._resolve_symbol",
            return_value=("BTC/USDT", "bitcoin"),
        ),
        patch(
            "app.market_data.coingecko_adapter.CoinGeckoAdapter.get_ticker",
            new=AsyncMock(side_effect=Exception("CoinGecko 429")),
        ),
    ):
        deliveries = await service.process_document(doc, result)

    assert len(deliveries) >= 1, "API failure must fail-open (alert dispatched)"


@pytest.mark.asyncio
async def test_price_check_fail_open_on_none_ticker(tmp_path: Path) -> None:
    """If get_ticker returns None, fail-open = dispatch."""
    doc = _make_doc()
    result = _make_result(str(doc.id), sentiment_label=SentimentLabel.BULLISH)

    service = AlertService(
        channels=[_DryRunChannel()],  # type: ignore[list-item]
        threshold=ThresholdEngine(min_priority=1),
        audit_dir=tmp_path,
    )

    with (
        patch(
            "app.market_data.coingecko_adapter._resolve_symbol",
            return_value=("BTC/USDT", "bitcoin"),
        ),
        patch(
            "app.market_data.coingecko_adapter.CoinGeckoAdapter.get_ticker",
            new=AsyncMock(return_value=None),
        ),
    ):
        deliveries = await service.process_document(doc, result)

    assert len(deliveries) >= 1, "None ticker must fail-open"


@pytest.mark.asyncio
async def test_neutral_sentiment_skips_price_check(tmp_path: Path) -> None:
    """Neutral sentiment never triggers the price trend gate."""
    doc = _make_doc(title="Market overview: mixed signals")
    result = _make_result(
        str(doc.id),
        sentiment_label=SentimentLabel.NEUTRAL,
        sentiment_score=0.0,
        actionable=True,
    )

    service = AlertService(
        channels=[_DryRunChannel()],  # type: ignore[list-item]
        threshold=ThresholdEngine(min_priority=1),
        audit_dir=tmp_path,
    )

    with patch(
        "app.market_data.coingecko_adapter.CoinGeckoAdapter.get_ticker",
        new=AsyncMock(),
    ) as mock_ticker:
        deliveries = await service.process_document(doc, result)

    mock_ticker.assert_not_called()
    assert len(deliveries) >= 1


@pytest.mark.asyncio
async def test_no_affected_assets_blocked_by_eligibility_gate(tmp_path: Path) -> None:
    """D-146: Bullish alert without affected_assets is blocked by eligibility gate."""
    doc = _make_doc()
    result = _make_result(
        str(doc.id),
        sentiment_label=SentimentLabel.BULLISH,
        affected_assets=[],  # empty
    )

    service = AlertService(
        channels=[_DryRunChannel()],  # type: ignore[list-item]
        threshold=ThresholdEngine(min_priority=1),
        audit_dir=tmp_path,
    )

    deliveries = await service.process_document(doc, result)
    assert len(deliveries) == 0, "missing_affected_assets → blocked by D-146"


# ── D-120: 7d regime in service integration ──────────────────────────────────


@pytest.mark.asyncio
async def test_bearish_blocked_by_7d_bull_regime(tmp_path: Path) -> None:
    """D-120: Bearish alert + 24h falling + 7d strongly bullish = block."""
    doc = _make_doc(title="Whale dumps 10k BTC in OTC deal")
    result = _make_result(
        str(doc.id),
        sentiment_label=SentimentLabel.BEARISH,
        sentiment_score=-0.85,
    )

    # 24h is falling (-2%), but 7d is strongly bullish (+5%) → regime block
    ticker = Ticker(
        symbol="BTC/USDT",
        timestamp_utc="2026-04-06T12:00:00+00:00",
        bid=68000.0,
        ask=68000.0,
        last=68000.0,
        volume_24h=1000.0,
        change_pct_24h=-2.0,
        change_pct_7d=5.0,
    )

    service = AlertService(
        channels=[_DryRunChannel()],  # type: ignore[list-item]
        threshold=ThresholdEngine(min_priority=1),
        audit_dir=tmp_path,
    )

    with (
        patch(
            "app.market_data.coingecko_adapter._resolve_symbol",
            return_value=("BTC/USDT", "bitcoin"),
        ),
        patch(
            "app.market_data.coingecko_adapter.CoinGeckoAdapter.get_ticker",
            new=AsyncMock(return_value=ticker),
        ),
    ):
        deliveries = await service.process_document(doc, result)

    assert deliveries == [], "7d bull regime should block bearish alert"


@pytest.mark.asyncio
async def test_bearish_blocked_by_eligibility_gate_even_with_aligned_7d(tmp_path: Path) -> None:
    """D-146: Bearish alerts are blocked by eligibility gate regardless of 7d regime."""
    doc = _make_doc(title="Market crash deepens amid regulation fears")
    result = _make_result(
        str(doc.id),
        sentiment_label=SentimentLabel.BEARISH,
        sentiment_score=-0.85,
    )

    service = AlertService(
        channels=[_DryRunChannel()],  # type: ignore[list-item]
        threshold=ThresholdEngine(min_priority=1),
        audit_dir=tmp_path,
    )

    deliveries = await service.process_document(doc, result)
    assert len(deliveries) == 0, "bearish_directional_disabled → blocked by D-146"
