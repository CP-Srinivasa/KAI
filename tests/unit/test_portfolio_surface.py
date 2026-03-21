"""Tests for portfolio and exposure read-only surfaces.

Covers:
- PortfolioSummary from empty portfolio
- PortfolioSummary with positions and prices
- ExposureSummary from empty and active portfolios
- Staleness detection in snapshots
- Telegram text formatting
- Frozen model immutability
- No write/trade/execute methods
"""

from __future__ import annotations

import pytest

from app.execution.models import PaperPortfolio, PaperPosition
from app.execution.portfolio_surface import (
    PositionSnapshot,
    build_exposure_summary,
    build_portfolio_summary,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _empty_portfolio() -> PaperPortfolio:
    return PaperPortfolio(initial_equity=10000.0, cash=10000.0)


def _portfolio_with_position() -> PaperPortfolio:
    p = PaperPortfolio(initial_equity=10000.0, cash=5000.0)
    p.positions["BTC/USDT"] = PaperPosition(
        symbol="BTC/USDT",
        quantity=0.1,
        avg_entry_price=50000.0,
        stop_loss=48000.0,
        take_profit=55000.0,
        opened_at="2026-03-21T10:00:00Z",
    )
    return p


def _portfolio_multi() -> PaperPortfolio:
    p = PaperPortfolio(
        initial_equity=10000.0, cash=3000.0,
        realized_pnl_usd=100.0, total_fees_usd=5.0,
        trade_count=3,
    )
    p.positions["BTC/USDT"] = PaperPosition(
        symbol="BTC/USDT", quantity=0.1,
        avg_entry_price=50000.0,
        stop_loss=48000.0, take_profit=55000.0,
        opened_at="2026-03-21T10:00:00Z",
    )
    p.positions["ETH/USDT"] = PaperPosition(
        symbol="ETH/USDT", quantity=1.0,
        avg_entry_price=3000.0,
        stop_loss=2800.0, take_profit=3500.0,
        opened_at="2026-03-21T10:00:00Z",
    )
    return p


# ------------------------------------------------------------------
# build_portfolio_summary
# ------------------------------------------------------------------


def test_empty_portfolio_summary() -> None:
    s = build_portfolio_summary(_empty_portfolio())
    assert s.open_position_count == 0
    assert s.cash == 10000.0
    assert s.total_equity == 10000.0
    assert s.unrealized_pnl == 0.0
    assert s.positions == ()


def test_portfolio_summary_with_position() -> None:
    prices = {"BTC/USDT": 55000.0}
    s = build_portfolio_summary(
        _portfolio_with_position(), prices=prices,
    )
    assert s.open_position_count == 1
    assert s.cash == 5000.0
    assert len(s.positions) == 1
    pos = s.positions[0]
    assert pos.symbol == "BTC/USDT"
    assert pos.current_price == 55000.0
    assert pos.unrealized_pnl == 500.0  # 0.1 * (55000 - 50000)
    assert pos.is_stale_price is False


def test_portfolio_summary_stale_symbol() -> None:
    prices = {"BTC/USDT": 55000.0}
    s = build_portfolio_summary(
        _portfolio_with_position(),
        prices=prices,
        stale_symbols={"BTC/USDT"},
    )
    assert s.positions[0].is_stale_price is True


def test_portfolio_summary_no_price() -> None:
    """Without price data, unrealized PnL is 0."""
    s = build_portfolio_summary(_portfolio_with_position())
    assert s.positions[0].current_price is None
    assert s.positions[0].unrealized_pnl == 0.0


def test_portfolio_summary_multi_positions() -> None:
    prices = {"BTC/USDT": 52000.0, "ETH/USDT": 3200.0}
    s = build_portfolio_summary(_portfolio_multi(), prices=prices)
    assert s.open_position_count == 2
    assert s.trade_count == 3
    assert s.total_fees == 5.0
    assert s.realized_pnl == 100.0


def test_portfolio_summary_to_dict() -> None:
    s = build_portfolio_summary(_empty_portfolio())
    d = s.to_dict()
    assert d["open_position_count"] == 0
    assert d["cash"] == 10000.0
    assert isinstance(d["positions"], list)


# ------------------------------------------------------------------
# build_exposure_summary
# ------------------------------------------------------------------


def test_empty_exposure() -> None:
    e = build_exposure_summary(_empty_portfolio())
    assert e.position_count == 0
    assert e.cash_pct == 100.0
    assert e.invested_pct == 0.0


def test_exposure_with_position() -> None:
    prices = {"BTC/USDT": 55000.0}
    e = build_exposure_summary(
        _portfolio_with_position(), prices=prices,
    )
    assert e.position_count == 1
    assert e.total_exposure_usd > 0
    assert e.largest_position_symbol == "BTC/USDT"
    assert e.invested_pct > 0
    assert e.cash_pct > 0


def test_exposure_multi() -> None:
    prices = {"BTC/USDT": 52000.0, "ETH/USDT": 3200.0}
    e = build_exposure_summary(_portfolio_multi(), prices=prices)
    assert e.position_count == 2
    assert len(e.per_position) == 2


def test_exposure_to_dict() -> None:
    e = build_exposure_summary(_empty_portfolio())
    d = e.to_dict()
    assert d["position_count"] == 0
    assert d["cash_pct"] == 100.0


# ------------------------------------------------------------------
# Telegram text formatting
# ------------------------------------------------------------------


def test_portfolio_telegram_text_empty() -> None:
    s = build_portfolio_summary(_empty_portfolio())
    text = s.to_telegram_text()
    assert "*Portfolio Summary*" in text
    assert "No open positions" in text


def test_portfolio_telegram_text_with_pos() -> None:
    prices = {"BTC/USDT": 55000.0}
    s = build_portfolio_summary(
        _portfolio_with_position(), prices=prices,
    )
    text = s.to_telegram_text()
    assert "BTC/USDT" in text
    assert "500.00" in text  # unrealized PnL


def test_exposure_telegram_text_empty() -> None:
    e = build_exposure_summary(_empty_portfolio())
    text = e.to_telegram_text()
    assert "*Exposure Summary*" in text
    assert "fully in cash" in text


def test_exposure_telegram_text_with_pos() -> None:
    prices = {"BTC/USDT": 55000.0}
    e = build_exposure_summary(
        _portfolio_with_position(), prices=prices,
    )
    text = e.to_telegram_text()
    assert "BTC/USDT" in text


# ------------------------------------------------------------------
# Immutability
# ------------------------------------------------------------------


def test_position_snapshot_frozen() -> None:
    ps = PositionSnapshot(
        symbol="X", quantity=1.0, avg_entry_price=100.0,
        stop_loss=None, take_profit=None,
        current_price=110.0, unrealized_pnl=10.0,
        unrealized_pnl_pct=10.0, is_stale_price=False,
    )
    with pytest.raises(AttributeError):
        ps.quantity = 2.0  # type: ignore[misc]


def test_portfolio_summary_frozen() -> None:
    s = build_portfolio_summary(_empty_portfolio())
    with pytest.raises(AttributeError):
        s.cash = 0.0  # type: ignore[misc]


def test_exposure_summary_frozen() -> None:
    e = build_exposure_summary(_empty_portfolio())
    with pytest.raises(AttributeError):
        e.cash_pct = 0.0  # type: ignore[misc]


# ------------------------------------------------------------------
# No write methods
# ------------------------------------------------------------------


def test_no_write_methods_on_summaries() -> None:
    forbidden = [
        "place_order", "create_order", "submit_order",
        "execute", "trade", "write", "fill_order",
    ]
    s = build_portfolio_summary(_empty_portfolio())
    e = build_exposure_summary(_empty_portfolio())
    for name in forbidden:
        assert not hasattr(s, name), f"has {name}"
        assert not hasattr(e, name), f"has {name}"
