"""Unit tests for the Paper Execution Engine."""
from __future__ import annotations

import pytest

from app.execution.paper_engine import PaperExecutionEngine


def _engine(tmp_path, initial_equity: float = 10000.0, **kwargs) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=initial_equity,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
        **kwargs,
    )


def test_live_enabled_raises():
    with pytest.raises(ValueError, match="live_enabled"):
        PaperExecutionEngine(live_enabled=True)


def test_initial_portfolio(tmp_path):
    eng = _engine(tmp_path)
    assert eng.portfolio.cash == 10000.0
    assert eng.portfolio.initial_equity == 10000.0
    assert eng.portfolio.trade_count == 0


def test_buy_order_fills_and_updates_portfolio(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="BTC/USDT", side="buy", quantity=0.1,
        stop_loss=60000.0, idempotency_key="test_buy_1"
    )
    fill = eng.fill_order(order, current_price=65000.0)
    assert fill is not None
    assert fill.side == "buy"
    assert fill.quantity == 0.1
    assert fill.fill_price > 65000.0  # slippage applied
    assert "BTC/USDT" in eng.portfolio.positions
    assert eng.portfolio.trade_count == 1
    assert eng.portfolio.cash < 10000.0  # reduced by cost + fee


def test_sell_without_position_fails(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(symbol="ETH/USDT", side="sell", quantity=1.0)
    fill = eng.fill_order(order, current_price=3200.0)
    assert fill is None


def test_buy_then_sell_records_pnl(tmp_path):
    eng = _engine(tmp_path)
    buy_order = eng.create_order(
        symbol="ETH/USDT", side="buy", quantity=1.0, idempotency_key="eth_buy"
    )
    eng.fill_order(buy_order, current_price=3000.0)

    sell_order = eng.create_order(
        symbol="ETH/USDT", side="sell", quantity=1.0, idempotency_key="eth_sell"
    )
    fill = eng.fill_order(sell_order, current_price=3200.0)
    assert fill is not None
    assert eng.portfolio.realized_pnl_usd > 0  # profitable trade
    assert "ETH/USDT" not in eng.portfolio.positions  # position closed


def test_idempotency_prevents_duplicate_fills(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="BTC/USDT", side="buy", quantity=0.01, idempotency_key="dup_test"
    )
    fill1 = eng.fill_order(order, current_price=65000.0)
    fill2 = eng.fill_order(order, current_price=65000.0)
    assert fill1 is not None
    assert fill2 is None
    assert eng.portfolio.trade_count == 1


def test_insufficient_cash_blocks_buy(tmp_path):
    eng = _engine(tmp_path, initial_equity=100.0)
    order = eng.create_order(
        symbol="BTC/USDT", side="buy", quantity=10.0  # would cost ~650k
    )
    fill = eng.fill_order(order, current_price=65000.0)
    assert fill is None


def test_stop_loss_detection(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="BTC/USDT", side="buy", quantity=0.01, stop_loss=60000.0
    )
    eng.fill_order(order, current_price=65000.0)

    # Price drops below stop loss
    trigger = eng.check_stop_take("BTC/USDT", current_price=59000.0)
    assert trigger == "stop"


def test_take_profit_detection(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="ETH/USDT", side="buy", quantity=1.0, take_profit=3500.0
    )
    eng.fill_order(order, current_price=3200.0)

    trigger = eng.check_stop_take("ETH/USDT", current_price=3600.0)
    assert trigger == "take"


def test_audit_log_written(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="SOL/USDT", side="buy", quantity=10.0, idempotency_key="audit_test"
    )
    eng.fill_order(order, current_price=150.0)

    audit_file = tmp_path / "audit.jsonl"
    assert audit_file.exists()
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2  # order_created + order_filled


def test_portfolio_drawdown(tmp_path):
    eng = _engine(tmp_path)
    # Buy then check drawdown — initially 0
    drawdown = eng.portfolio.drawdown_pct({})
    assert drawdown == 0.0
