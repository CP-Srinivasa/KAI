"""Tests for D-119: Pipeline → Paper-Trade Bridge.

Coverage:
- _maybe_trigger_paper_trade() — happy path, non-directional skip,
  no assets, unresolvable assets, auto-run disabled, fail-open on error
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel
from app.pipeline.service import _maybe_trigger_paper_trade


def _make_doc(**overrides) -> CanonicalDocument:
    defaults = {
        "url": f"https://example.com/{uuid.uuid4()}",
        "title": "Institutional BTC accumulation accelerates",
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
        "explanation_short": "Strong buy signal",
        "explanation_long": "Detailed explanation",
        "affected_assets": ["BTC"],
    }
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def _mock_settings(auto_run_enabled: bool = True, mode: str = "paper"):
    """Build a mock settings object for _maybe_trigger_paper_trade."""
    settings = MagicMock()
    settings.operator.signal_auto_run_enabled = auto_run_enabled
    settings.operator.signal_auto_run_mode = mode
    return settings


def _mock_cycle(status: str = "completed"):
    """Build a mock LoopCycle return value."""
    cycle = MagicMock()
    cycle.cycle_id = "cycle-test-001"
    cycle.status = MagicMock()
    cycle.status.value = status
    return cycle


# ── Happy path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_paper_trade_bullish_btc() -> None:
    """Bullish sentiment + BTC asset = paper trade triggered."""
    doc = _make_doc()
    result = _make_result(str(doc.id))

    with (
        patch(
            "app.pipeline.service.get_settings",
            return_value=_mock_settings(),
        ),
        patch(
            "app.market_data.coingecko_adapter._resolve_symbol",
            return_value=("BTC/USDT", "bitcoin"),
        ),
        patch(
            "app.orchestrator.trading_loop.run_trading_loop_once",
            new=AsyncMock(return_value=_mock_cycle()),
        ) as mock_run,
    ):
        triggered = await _maybe_trigger_paper_trade(doc, result)

    assert triggered is True
    mock_run.assert_awaited_once()
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["symbol"] == "BTC/USDT"
    assert call_kwargs["mode"] == "paper"


@pytest.mark.asyncio
async def test_trigger_paper_trade_bearish() -> None:
    """Bearish sentiment also triggers paper trade."""
    doc = _make_doc()
    result = _make_result(
        str(doc.id),
        sentiment_label=SentimentLabel.BEARISH,
        sentiment_score=-0.80,
        affected_assets=["ETH"],
    )

    with (
        patch(
            "app.pipeline.service.get_settings",
            return_value=_mock_settings(),
        ),
        patch(
            "app.market_data.coingecko_adapter._resolve_symbol",
            return_value=("ETH/USDT", "ethereum"),
        ),
        patch(
            "app.orchestrator.trading_loop.run_trading_loop_once",
            new=AsyncMock(return_value=_mock_cycle()),
        ) as mock_run,
    ):
        triggered = await _maybe_trigger_paper_trade(doc, result)

    assert triggered is True
    assert mock_run.call_args.kwargs["symbol"] == "ETH/USDT"


# ── Skip conditions ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_when_auto_run_disabled() -> None:
    """Auto-run disabled in settings = no trade."""
    doc = _make_doc()
    result = _make_result(str(doc.id))

    with patch(
        "app.pipeline.service.get_settings",
        return_value=_mock_settings(auto_run_enabled=False),
    ):
        triggered = await _maybe_trigger_paper_trade(doc, result)

    assert triggered is False


@pytest.mark.asyncio
async def test_skip_neutral_sentiment() -> None:
    """Neutral sentiment is not directional = no trade."""
    doc = _make_doc()
    result = _make_result(
        str(doc.id),
        sentiment_label=SentimentLabel.NEUTRAL,
        sentiment_score=0.0,
    )

    with patch(
        "app.pipeline.service.get_settings",
        return_value=_mock_settings(),
    ):
        triggered = await _maybe_trigger_paper_trade(doc, result)

    assert triggered is False


@pytest.mark.asyncio
async def test_skip_mixed_sentiment() -> None:
    """Mixed sentiment is not directional = no trade."""
    doc = _make_doc()
    result = _make_result(
        str(doc.id),
        sentiment_label=SentimentLabel.MIXED,
        sentiment_score=0.1,
    )

    with patch(
        "app.pipeline.service.get_settings",
        return_value=_mock_settings(),
    ):
        triggered = await _maybe_trigger_paper_trade(doc, result)

    assert triggered is False


@pytest.mark.asyncio
async def test_skip_empty_affected_assets() -> None:
    """No affected assets = can't determine which symbol to trade."""
    doc = _make_doc()
    result = _make_result(str(doc.id), affected_assets=[])

    with patch(
        "app.pipeline.service.get_settings",
        return_value=_mock_settings(),
    ):
        triggered = await _maybe_trigger_paper_trade(doc, result)

    assert triggered is False


@pytest.mark.asyncio
async def test_skip_unresolvable_assets() -> None:
    """Assets that don't resolve to CoinGecko symbols = no trade."""
    doc = _make_doc()
    result = _make_result(str(doc.id), affected_assets=["FAKE_STOCK", "XYZ_CORP"])

    with (
        patch(
            "app.pipeline.service.get_settings",
            return_value=_mock_settings(),
        ),
        patch(
            "app.market_data.coingecko_adapter._resolve_symbol",
            return_value=None,
        ),
    ):
        triggered = await _maybe_trigger_paper_trade(doc, result)

    assert triggered is False


# ── Fail-open ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fail_open_on_trading_loop_error() -> None:
    """Trading loop exception = False (no crash), pipeline continues."""
    doc = _make_doc()
    result = _make_result(str(doc.id))

    with (
        patch(
            "app.pipeline.service.get_settings",
            return_value=_mock_settings(),
        ),
        patch(
            "app.market_data.coingecko_adapter._resolve_symbol",
            return_value=("BTC/USDT", "bitcoin"),
        ),
        patch(
            "app.orchestrator.trading_loop.run_trading_loop_once",
            new=AsyncMock(side_effect=RuntimeError("DB connection lost")),
        ),
    ):
        triggered = await _maybe_trigger_paper_trade(doc, result)

    assert triggered is False  # fail-open: no crash, just returns False


@pytest.mark.asyncio
async def test_resolves_first_tradeable_asset() -> None:
    """If first asset is non-crypto, tries second one."""
    doc = _make_doc()
    result = _make_result(str(doc.id), affected_assets=["AAPL", "ETH"])

    def mock_resolve(symbol: str):
        if symbol == "AAPL":
            return None
        return ("ETH/USDT", "ethereum")

    with (
        patch(
            "app.pipeline.service.get_settings",
            return_value=_mock_settings(),
        ),
        patch(
            "app.market_data.coingecko_adapter._resolve_symbol",
            side_effect=mock_resolve,
        ),
        patch(
            "app.orchestrator.trading_loop.run_trading_loop_once",
            new=AsyncMock(return_value=_mock_cycle()),
        ) as mock_run,
    ):
        triggered = await _maybe_trigger_paper_trade(doc, result)

    assert triggered is True
    assert mock_run.call_args.kwargs["symbol"] == "ETH/USDT"
