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
        symbol="BTC/USDT", side="buy", quantity=0.1, stop_loss=60000.0, idempotency_key="test_buy_1"
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
        symbol="BTC/USDT",
        side="buy",
        quantity=10.0,  # would cost ~650k
    )
    fill = eng.fill_order(order, current_price=65000.0)
    assert fill is None


def test_stop_loss_detection(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(symbol="BTC/USDT", side="buy", quantity=0.01, stop_loss=60000.0)
    eng.fill_order(order, current_price=65000.0)

    # Price drops below stop loss
    trigger = eng.check_stop_take("BTC/USDT", current_price=59000.0)
    assert trigger == "stop"


def test_take_profit_detection(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(symbol="ETH/USDT", side="buy", quantity=1.0, take_profit=3500.0)
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


def _open_long(eng, symbol, qty, entry, *, sl=None, tp=None, idem=None):
    order = eng.create_order(
        symbol=symbol,
        side="buy",
        quantity=qty,
        stop_loss=sl,
        take_profit=tp,
        idempotency_key=idem or f"open_{symbol}",
    )
    return eng.fill_order(order, current_price=entry)


def test_close_position_full_exit_realizes_pnl(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "BTC/USDT", 0.1, 65000.0, sl=60000.0, tp=70000.0)
    assert "BTC/USDT" in eng.portfolio.positions

    fill = eng.close_position("BTC/USDT", current_price=71000.0, reason="take")
    assert fill is not None
    assert fill.side == "sell"
    assert fill.quantity == 0.1
    assert "BTC/USDT" not in eng.portfolio.positions
    assert eng.portfolio.realized_pnl_usd > 0


def test_close_position_no_position_returns_none(tmp_path):
    eng = _engine(tmp_path)
    assert eng.close_position("BTC/USDT", current_price=65000.0, reason="stop") is None


def test_close_position_invalid_price_returns_none(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "BTC/USDT", 0.1, 65000.0, sl=60000.0)
    assert eng.close_position("BTC/USDT", current_price=0.0, reason="stop") is None
    assert eng.close_position("BTC/USDT", current_price=-5.0, reason="stop") is None
    assert "BTC/USDT" in eng.portfolio.positions


def test_close_position_is_idempotent(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "ETH/USDT", 1.0, 3000.0, sl=2800.0)
    f1 = eng.close_position("ETH/USDT", current_price=2790.0, reason="stop")
    assert f1 is not None
    # Same reason on a now-empty slot returns None; a fresh position would
    # have a new opened_at and therefore a new idempotency key.
    f2 = eng.close_position("ETH/USDT", current_price=2790.0, reason="stop")
    assert f2 is None


def test_close_position_writes_position_closed_audit(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "SOL/USDT", 10.0, 150.0, tp=170.0)
    eng.close_position("SOL/USDT", current_price=175.0, reason="take")
    import json
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]
    events = [rec["event_type"] for rec in records]
    assert events.count("position_closed") == 1
    closed = next(rec for rec in records if rec["event_type"] == "position_closed")
    assert closed["symbol"] == "SOL/USDT"
    assert closed["reason"] == "take"
    assert closed["quantity"] == 10.0
    assert "entry_price" in closed
    assert "exit_price" in closed
    assert closed["realized_pnl_usd"] != 0.0


def test_monitor_positions_triggers_stop_loss(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "BTC/USDT", 0.1, 65000.0, sl=60000.0, tp=70000.0)
    fills = eng.monitor_positions({"BTC/USDT": 59500.0})
    assert len(fills) == 1
    assert fills[0].side == "sell"
    assert "BTC/USDT" not in eng.portfolio.positions
    assert eng.portfolio.realized_pnl_usd < 0  # loss from stop


def test_monitor_positions_triggers_take_profit(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "ETH/USDT", 1.0, 3000.0, tp=3200.0)
    fills = eng.monitor_positions({"ETH/USDT": 3250.0})
    assert len(fills) == 1
    assert eng.portfolio.realized_pnl_usd > 0


def test_monitor_positions_skips_symbols_without_price(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "BTC/USDT", 0.1, 65000.0, sl=60000.0)
    _open_long(eng, "ETH/USDT", 1.0, 3000.0, sl=2800.0, idem="open_ETH/USDT")
    # Only BTC gets a price; ETH is absent — must not be closed.
    fills = eng.monitor_positions({"BTC/USDT": 58000.0})
    assert len(fills) == 1
    assert fills[0].symbol == "BTC/USDT"
    assert "ETH/USDT" in eng.portfolio.positions


def test_monitor_positions_no_trigger_keeps_positions(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "BTC/USDT", 0.1, 65000.0, sl=60000.0, tp=70000.0)
    # Price stays within SL/TP range.
    fills = eng.monitor_positions({"BTC/USDT": 66000.0})
    assert fills == []
    assert "BTC/USDT" in eng.portfolio.positions


def test_monitor_positions_rejects_non_positive_prices(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "BTC/USDT", 0.1, 65000.0, sl=60000.0)
    assert eng.monitor_positions({"BTC/USDT": 0.0}) == []
    assert eng.monitor_positions({"BTC/USDT": -100.0}) == []
    assert "BTC/USDT" in eng.portfolio.positions


# --- Defense-in-depth: reject fills with inverted SL/TP geometry ---


def test_fill_long_with_sl_above_price_rejected(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=0.01,
        stop_loss=73718.0,  # above current price → inverted
        idempotency_key="inv_sl",
    )
    fill = eng.fill_order(order, current_price=65000.0)
    assert fill is None
    assert "BTC/USDT" not in eng.portfolio.positions
    # Audit contains a rejection event
    import json
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line)["event_type"] for line in lines]
    assert "order_rejected_invalid_sl" in events


def test_fill_long_with_tp_below_price_rejected(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="ETH/USDT",
        side="buy",
        quantity=1.0,
        take_profit=2800.0,  # below current price → inverted
        idempotency_key="inv_tp",
    )
    fill = eng.fill_order(order, current_price=3000.0)
    assert fill is None
    import json
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line)["event_type"] for line in lines]
    assert "order_rejected_invalid_tp" in events


def test_fill_long_with_valid_geometry_accepted(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=0.01,
        stop_loss=60000.0,
        take_profit=72000.0,
    )
    fill = eng.fill_order(order, current_price=65000.0)
    assert fill is not None
    assert "BTC/USDT" in eng.portfolio.positions


def test_close_position_not_blocked_by_geometry_check(tmp_path):
    """Close-orders (sell side) carry no SL/TP — defense check must not block them."""
    eng = _engine(tmp_path)
    _open_long(eng, "BTC/USDT", 0.01, 65000.0, sl=60000.0, tp=72000.0)
    fill = eng.close_position("BTC/USDT", current_price=71000.0, reason="take")
    assert fill is not None
    assert "BTC/USDT" not in eng.portfolio.positions
