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


def test_short_stop_and_take_detection(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="BTC/USDT",
        side="sell",
        quantity=0.1,
        stop_loss=70000.0,
        take_profit=60000.0,
        position_side="short",
    )
    eng.fill_order(order, current_price=65000.0)

    assert eng.check_stop_take("BTC/USDT", current_price=70100.0) == "stop"
    assert eng.check_stop_take("BTC/USDT", current_price=59900.0) == "take"


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


def _open_short(eng, symbol, qty, entry, *, sl=None, tp=None, idem=None):
    order = eng.create_order(
        symbol=symbol,
        side="sell",
        quantity=qty,
        stop_loss=sl,
        take_profit=tp,
        idempotency_key=idem or f"short_{symbol}",
        position_side="short",
    )
    return eng.fill_order(order, current_price=entry)


def test_short_order_fills_and_updates_portfolio(tmp_path):
    eng = _engine(tmp_path)
    fill = _open_short(eng, "BTC/USDT", 0.1, 65000.0, sl=70000.0, tp=60000.0)
    assert fill is not None
    assert fill.side == "sell"
    pos = eng.portfolio.positions["BTC/USDT"]
    assert pos.position_side == "short"
    assert eng.portfolio.cash > 10000.0


def test_close_short_realizes_profit_when_price_drops(tmp_path):
    eng = _engine(tmp_path)
    _open_short(eng, "ETH/USDT", 1.0, 3000.0, sl=3300.0, tp=2700.0)
    fill = eng.close_position("ETH/USDT", current_price=2700.0, reason="take")
    assert fill is not None
    assert fill.side == "buy"
    assert fill.position_side == "short"
    assert fill.pnl_usd > 0
    assert "ETH/USDT" not in eng.portfolio.positions
    assert eng.portfolio.realized_pnl_usd > 0


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


# --- V25-C: Multi-tier take-profit (staged exits) ---


def test_set_tp_tiers_attaches_ladder_to_open_position(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "HYPE/USDT", 100.0, 40.9, sl=39.26)
    ok = eng.set_position_tp_tiers(
        "HYPE/USDT",
        [(41.105, 0.25), (41.31, 0.25), (41.515, 0.25), (41.72, 0.25)],
    )
    assert ok is True
    pos = eng.portfolio.positions["HYPE/USDT"]
    assert len(pos.take_profit_tiers) == 4
    assert pos.take_profit_tiers[0][0] == 41.105
    assert pos.initial_quantity == 100.0


def test_set_tp_tiers_returns_false_for_missing_position(tmp_path):
    eng = _engine(tmp_path)
    assert eng.set_position_tp_tiers("BTC/USDT", [(70000.0, 1.0)]) is False


def test_monitor_positions_consumes_first_tier_only(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "HYPE/USDT", 100.0, 40.9, sl=39.26)
    eng.set_position_tp_tiers(
        "HYPE/USDT",
        [(41.105, 0.25), (41.31, 0.25), (41.515, 0.25), (41.72, 0.25)],
    )
    # Price hits TP1 only.
    fills = eng.monitor_positions({"HYPE/USDT": 41.15})
    assert len(fills) == 1
    pos = eng.portfolio.positions.get("HYPE/USDT")
    assert pos is not None
    # 25% closed → ~75% of original quantity remains.
    assert abs(pos.quantity - 75.0) < 1e-6
    # First tier consumed, three remaining.
    assert len(pos.take_profit_tiers) == 3
    assert pos.take_profit_tiers[0][0] == 41.31


def test_monitor_positions_consumes_multiple_tiers_in_one_tick(tmp_path):
    # A single wide candle clears TP1 + TP2 — both tiers must close.
    eng = _engine(tmp_path)
    _open_long(eng, "HYPE/USDT", 100.0, 40.9, sl=39.26)
    eng.set_position_tp_tiers(
        "HYPE/USDT",
        [(41.105, 0.25), (41.31, 0.25), (41.515, 0.25), (41.72, 0.25)],
    )
    fills = eng.monitor_positions({"HYPE/USDT": 41.4})  # > TP2
    assert len(fills) == 2
    pos = eng.portfolio.positions["HYPE/USDT"]
    assert abs(pos.quantity - 50.0) < 1e-6
    assert len(pos.take_profit_tiers) == 2


def test_monitor_positions_full_sweep_through_all_tiers(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "HYPE/USDT", 100.0, 40.9, sl=39.26)
    eng.set_position_tp_tiers(
        "HYPE/USDT",
        [(41.105, 0.25), (41.31, 0.25), (41.515, 0.25), (41.72, 0.25)],
    )
    fills = eng.monitor_positions({"HYPE/USDT": 42.0})  # > TP4
    assert len(fills) == 4
    # Position fully exited after the last tier.
    assert "HYPE/USDT" not in eng.portfolio.positions


def test_stop_loss_overrides_tier_ladder(tmp_path):
    # SL is the safety net — even with a tier ladder, an SL hit closes the
    # entire residual at once and discards the remaining tiers.
    eng = _engine(tmp_path)
    _open_long(eng, "HYPE/USDT", 100.0, 40.9, sl=39.26)
    eng.set_position_tp_tiers(
        "HYPE/USDT",
        [(41.105, 0.25), (41.31, 0.25), (41.515, 0.25), (41.72, 0.25)],
    )
    fills = eng.monitor_positions({"HYPE/USDT": 39.0})
    assert len(fills) == 1
    assert "HYPE/USDT" not in eng.portfolio.positions


def test_sl_after_partial_tier_close_kills_remainder(tmp_path):
    # Sequence: TP1 hit (25% closed) → price reverses → SL fires → 75% closed.
    eng = _engine(tmp_path)
    _open_long(eng, "HYPE/USDT", 100.0, 40.9, sl=39.26)
    eng.set_position_tp_tiers(
        "HYPE/USDT",
        [(41.105, 0.25), (41.31, 0.25), (41.515, 0.25), (41.72, 0.25)],
    )
    eng.monitor_positions({"HYPE/USDT": 41.15})
    pos = eng.portfolio.positions["HYPE/USDT"]
    assert abs(pos.quantity - 75.0) < 1e-6
    fills = eng.monitor_positions({"HYPE/USDT": 39.0})
    assert len(fills) == 1
    assert "HYPE/USDT" not in eng.portfolio.positions


def test_last_tier_closes_full_remaining_quantity(tmp_path):
    # Floating-point safety: the final tier must zero the position out
    # exactly, no dust quantity left behind.
    eng = _engine(tmp_path)
    _open_long(eng, "ETH/USDT", 1.0, 3000.0, sl=2900.0)
    eng.set_position_tp_tiers(
        "ETH/USDT",
        [(3050.0, 0.333333), (3100.0, 0.333333), (3150.0, 0.333333)],
    )
    fills = eng.monitor_positions({"ETH/USDT": 3200.0})
    assert len(fills) == 3
    assert "ETH/USDT" not in eng.portfolio.positions


def test_audit_replay_restores_tiers_after_restart(tmp_path):
    # Open position + set tiers → consume TP1 → simulate restart by
    # rehydrating a fresh engine from the same audit log → tiers must be
    # consistent with what was on disk before the "restart".
    eng1 = _engine(tmp_path)
    _open_long(eng1, "HYPE/USDT", 100.0, 40.9, sl=39.26)
    eng1.set_position_tp_tiers(
        "HYPE/USDT",
        [(41.105, 0.25), (41.31, 0.25), (41.515, 0.25), (41.72, 0.25)],
    )
    eng1.monitor_positions({"HYPE/USDT": 41.15})
    pos1 = eng1.portfolio.positions["HYPE/USDT"]
    assert len(pos1.take_profit_tiers) == 3

    eng2 = _engine(tmp_path)
    eng2.rehydrate_from_audit()
    pos2 = eng2.portfolio.positions["HYPE/USDT"]
    assert len(pos2.take_profit_tiers) == 3
    assert pos2.take_profit_tiers[0][0] == 41.31
    assert abs(pos2.quantity - 75.0) < 1e-3


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


# ---------------------------------------------------------------------------
# NEO-P-101-r2: schema-v2 audit, position_side, file-lock, per-trade NETTO PnL
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402


def _read_audit_records(audit_path):
    return [
        _json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_buy_event_trade_pnl_zero(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="BTC/USDT", side="buy", quantity=0.1, idempotency_key="buy_pnl_zero"
    )
    eng.fill_order(order, current_price=65000.0)
    records = _read_audit_records(tmp_path / "audit.jsonl")
    fill_records = [r for r in records if r.get("event_type") == "order_filled"]
    assert len(fill_records) == 1
    fill = fill_records[0]
    assert fill["pnl_usd"] == 0.0
    assert fill["side"] == "buy"


def test_limit_order_uses_maker_fee_metadata(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="ETH/USDT",
        side="buy",
        quantity=1.0,
        order_type="limit",
        limit_price=100.0,
        venue="okx",
        idempotency_key="okx_limit_maker",
    )
    fill = eng.fill_order(order, current_price=100.0)
    assert fill is not None
    assert fill.fee_role == "maker"
    assert fill.fee_venue == "okx"
    assert fill.fee_bps_applied == pytest.approx(8.0)

    records = _read_audit_records(tmp_path / "audit.jsonl")
    fill_records = [r for r in records if r.get("event_type") == "order_filled"]
    assert fill_records[0]["fee_role"] == "maker"
    assert fill_records[0]["fee_bps_applied"] == pytest.approx(8.0)


def test_default_order_uses_paper_fee_metadata(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="ETH/USDT",
        side="buy",
        quantity=1.0,
        order_type="limit",
        limit_price=100.0,
        idempotency_key="paper_default_fee",
    )
    assert order.venue == "paper"
    fill = eng.fill_order(order, current_price=100.0)
    assert fill is not None
    assert fill.fee_venue == "paper"
    assert fill.fee_role == "maker"
    assert fill.fee_bps_applied == pytest.approx(60.0)


def test_explicit_legacy_venue_preserves_constructor_fee(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="ETH/USDT",
        side="buy",
        quantity=1.0,
        order_type="limit",
        limit_price=100.0,
        venue="legacy",
        idempotency_key="legacy_fee",
    )
    fill = eng.fill_order(order, current_price=100.0)
    assert fill is not None
    assert fill.fee_venue == "legacy"
    assert fill.fee_bps_applied == pytest.approx(10.0)


def test_sell_close_trade_pnl_netto_correct(tmp_path):
    eng = _engine(tmp_path)
    buy = eng.create_order(symbol="X/USDT", side="buy", quantity=1.0, idempotency_key="b1")
    eng.fill_order(buy, current_price=100.0)
    sell = eng.create_order(symbol="X/USDT", side="sell", quantity=1.0, idempotency_key="s1")
    fill = eng.fill_order(sell, current_price=110.0)
    assert fill is not None
    assert fill.fee_venue == "paper"
    assert fill.fee_bps_applied == pytest.approx(60.0)
    assert 9.2 < fill.pnl_usd < 9.3, f"netto pnl off: {fill.pnl_usd}"
    records = _read_audit_records(tmp_path / "audit.jsonl")
    sell_fills = [
        r for r in records if r.get("event_type") == "order_filled" and r.get("side") == "sell"
    ]
    assert len(sell_fills) == 1
    assert sell_fills[0]["fee_venue"] == "paper"
    assert sell_fills[0]["fee_bps_applied"] == pytest.approx(60.0)
    assert 9.2 < sell_fills[0]["pnl_usd"] < 9.3


def test_position_closed_event_has_fee_usd(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "ETH/USDT", 1.0, 3000.0, tp=3200.0)
    eng.close_position("ETH/USDT", current_price=3300.0, reason="take")
    records = _read_audit_records(tmp_path / "audit.jsonl")
    closed = [r for r in records if r.get("event_type") == "position_closed"]
    assert len(closed) == 1
    rec = closed[0]
    assert "fee_usd" in rec
    assert rec["fee_usd"] > 0
    assert "trade_pnl_usd" in rec
    assert rec["trade_pnl_usd"] > 0


def test_schema_version_v2_in_every_new_event(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "BTC/USDT", 0.1, 65000.0, sl=60000.0, tp=70000.0)
    eng.close_position("BTC/USDT", current_price=71000.0, reason="take")
    records = _read_audit_records(tmp_path / "audit.jsonl")
    assert len(records) >= 3
    for r in records:
        assert r.get("schema_version") == "v2", f"missing v2: {r}"


def test_audit_replay_handles_v1_legacy_lines(tmp_path):
    from app.execution.audit_replay import replay_paper_audit

    audit = tmp_path / "legacy.jsonl"
    v1_records = [
        {
            "event_type": "order_created",
            "timestamp_utc": "2026-01-01T00:00:00Z",
            "order_id": "ord_legacy_1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 0.1,
            "stop_loss": 60000.0,
            "take_profit": 70000.0,
        },
        {
            "event_type": "order_filled",
            "timestamp_utc": "2026-01-01T00:00:01Z",
            "fill_id": "fill_legacy_1",
            "order_id": "ord_legacy_1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 0.1,
            "fill_price": 65000.0,
            "fee_usd": 6.5,
            "filled_at": "2026-01-01T00:00:01Z",
            "slippage_pct": 0.05,
            "portfolio_cash": 3493.5,
            "realized_pnl_usd": 0.0,
        },
    ]
    audit.write_text("\n".join(_json.dumps(r) for r in v1_records) + "\n", encoding="utf-8")
    result = replay_paper_audit(audit)
    assert result.available is True
    assert result.error is None
    assert "BTC/USDT" in result.positions
    pos = result.positions["BTC/USDT"]
    assert pos.position_side == "long"


def test_audit_replay_restores_open_short_position(tmp_path):
    from app.execution.audit_replay import replay_paper_audit

    audit = tmp_path / "short.jsonl"
    rows = [
        {
            "schema_version": "v2",
            "event_type": "order_created",
            "order_id": "short_o1",
            "symbol": "ETH/USDT",
            "side": "sell",
            "quantity": 1.0,
            "stop_loss": 3300.0,
            "take_profit": 2700.0,
            "position_side": "short",
            "timestamp_utc": "2026-01-01T00:00:00Z",
        },
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "fill_id": "short_f1",
            "order_id": "short_o1",
            "symbol": "ETH/USDT",
            "side": "sell",
            "quantity": 1.0,
            "fill_price": 3000.0,
            "fee_usd": 3.0,
            "filled_at": "2026-01-01T00:00:01Z",
            "slippage_pct": 0.05,
            "pnl_usd": 0.0,
            "position_side": "short",
            "portfolio_cash": 12997.0,
            "realized_pnl_usd": 0.0,
            "timestamp_utc": "2026-01-01T00:00:01Z",
        },
    ]
    audit.write_text("\n".join(_json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    result = replay_paper_audit(audit)

    assert result.available is True, result.error
    pos = result.positions["ETH/USDT"]
    assert pos.position_side == "short"
    assert pos.stop_loss == 3300.0
    assert pos.take_profit == 2700.0


def test_audit_replay_handles_mixed_v1_v2(tmp_path):
    from app.execution.audit_replay import replay_paper_audit

    audit = tmp_path / "mixed.jsonl"
    rows = [
        {
            "event_type": "order_created",
            "order_id": "o1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 0.1,
            "stop_loss": 60000.0,
            "take_profit": 70000.0,
            "timestamp_utc": "2026-01-01T00:00:00Z",
        },
        {
            "event_type": "order_filled",
            "fill_id": "f1",
            "order_id": "o1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 0.1,
            "fill_price": 65000.0,
            "fee_usd": 6.5,
            "filled_at": "2026-01-01T00:00:01Z",
            "slippage_pct": 0.05,
            "portfolio_cash": 3493.5,
            "realized_pnl_usd": 0.0,
            "timestamp_utc": "2026-01-01T00:00:01Z",
        },
        {"event_type": "cycle_start", "timestamp_utc": "2026-01-01T00:00:02Z"},
        {
            "schema_version": "v2",
            "event_type": "order_created",
            "order_id": "o2",
            "symbol": "ETH/USDT",
            "side": "buy",
            "quantity": 1.0,
            "position_side": "long",
            "timestamp_utc": "2026-01-02T00:00:00Z",
        },
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "fill_id": "f2",
            "order_id": "o2",
            "symbol": "ETH/USDT",
            "side": "buy",
            "quantity": 1.0,
            "fill_price": 3000.0,
            "fee_usd": 3.0,
            "filled_at": "2026-01-02T00:00:01Z",
            "slippage_pct": 0.05,
            "pnl_usd": 0.0,
            "position_side": "long",
            "portfolio_cash": 497.5,
            "realized_pnl_usd": 0.0,
            "timestamp_utc": "2026-01-02T00:00:01Z",
        },
        {
            "schema_version": "v2",
            "event_type": "position_closed",
            "symbol": "BTC/USDT",
            "reason": "take",
            "quantity": 0.1,
            "entry_price": 65000.0,
            "exit_price": 71000.0,
            "trade_pnl_usd": 599.0,
            "fee_usd": 7.1,
            "position_side": "long",
            "realized_pnl_usd": 599.0,
            "fill_id": "fz",
            "order_id": "oz",
            "timestamp_utc": "2026-01-02T00:00:02Z",
        },
    ]
    audit.write_text("\n".join(_json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    result = replay_paper_audit(audit)
    assert result.available is True, result.error
    assert "BTC/USDT" in result.positions
    assert "ETH/USDT" in result.positions


def test_paper_position_default_position_side_long(tmp_path):
    eng = _engine(tmp_path)
    _open_long(eng, "SOL/USDT", 10.0, 150.0)
    pos = eng.portfolio.positions["SOL/USDT"]
    assert pos.position_side == "long"


def test_engine_accepts_position_side_short(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="BTC/USDT",
        side="sell",
        quantity=0.01,
        idempotency_key="short_v5",
        position_side="short",
    )
    assert order.position_side == "short"


def test_partial_sell_keeps_position(tmp_path):
    eng = _engine(tmp_path)
    buy = eng.create_order(symbol="ETH/USDT", side="buy", quantity=2.0, idempotency_key="b_partial")
    eng.fill_order(buy, current_price=3000.0)
    sell = eng.create_order(
        symbol="ETH/USDT", side="sell", quantity=1.0, idempotency_key="s_partial"
    )
    fill = eng.fill_order(sell, current_price=3300.0)
    assert fill is not None
    pos = eng.portfolio.positions.get("ETH/USDT")
    assert pos is not None
    assert abs(pos.quantity - 1.0) < 1e-6
    assert fill.pnl_usd > 0
    assert fill.quantity == 1.0


def test_average_down_then_close(tmp_path):
    eng = _engine(tmp_path)
    b1 = eng.create_order(symbol="X/USDT", side="buy", quantity=1.0, idempotency_key="b_avg_1")
    eng.fill_order(b1, current_price=100.0)
    b2 = eng.create_order(symbol="X/USDT", side="buy", quantity=1.0, idempotency_key="b_avg_2")
    eng.fill_order(b2, current_price=90.0)
    sell = eng.create_order(symbol="X/USDT", side="sell", quantity=2.0, idempotency_key="s_avg")
    fill = eng.fill_order(sell, current_price=100.0)
    assert fill is not None
    assert 8.0 < fill.pnl_usd < 11.0, f"unexpected netto pnl: {fill.pnl_usd}"
    assert "X/USDT" not in eng.portfolio.positions


def test_append_audit_concurrent_writes_no_corruption(tmp_path):
    import threading

    eng = _engine(tmp_path)

    def writer(tag):
        for i in range(50):
            eng._append_audit(
                "concurrency_probe",
                {"tag": tag, "i": i, "payload": "x" * 100},
            )

    t1 = threading.Thread(target=writer, args=("A",))
    t2 = threading.Thread(target=writer, args=("B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    audit = tmp_path / "audit.jsonl"
    raw_lines = audit.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) >= 100
    probes = []
    for line in raw_lines:
        if not line.strip():
            continue
        rec = _json.loads(line)
        if rec.get("event_type") == "concurrency_probe":
            probes.append(rec)
    assert len(probes) == 100


def test_order_filled_emits_executed_transition(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=0.1,
        idempotency_key="fill_trans",
        correlation_id="dec_123",
    )
    eng.fill_order(order, current_price=65000.0)

    records = _read_audit_records(tmp_path / "audit.jsonl")
    transitions = [r for r in records if r.get("event_type") == "signal_state_transition"]
    assert len(transitions) == 1
    t = transitions[0]
    assert t["decision_id"] == "dec_123"
    assert t["from_state"] == "approved"
    assert t["to_state"] == "executed"
    assert t["source"] == "paper_engine"


def test_close_position_emits_closed_transition(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="ETH/USDT",
        side="buy",
        quantity=1.0,
        idempotency_key="close_trans",
        correlation_id="dec_456",
        take_profit=3500.0,
    )
    eng.fill_order(order, current_price=3000.0)

    eng.close_position("ETH/USDT", current_price=3600.0, reason="take")

    records = _read_audit_records(tmp_path / "audit.jsonl")
    transitions = [r for r in records if r.get("event_type") == "signal_state_transition"]
    assert len(transitions) == 2

    # Second transition should be EXECUTED -> CLOSED
    t = transitions[1]
    assert t["decision_id"] == "dec_456"
    assert t["from_state"] == "executed"
    assert t["to_state"] == "closed"
    assert t["source"] == "paper_engine"
    assert t["reason"] == "take"


def test_fill_without_correlation_id_skips_signal_transition(tmp_path):
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=0.1,
        idempotency_key="no_corr_id",
    )
    fill = eng.fill_order(order, current_price=65000.0)
    assert fill is not None

    records = _read_audit_records(tmp_path / "audit.jsonl")
    transitions = [r for r in records if r.get("event_type") == "signal_state_transition"]
    assert transitions == []


def test_illegal_signal_transition_logged_not_raised(tmp_path, caplog):
    import logging

    from app.signals.models import SignalState

    eng = _engine(tmp_path)
    caplog.set_level(logging.ERROR)

    eng._validate_and_append_signal_transition(
        decision_id="dec_illegal",
        from_state=SignalState.CLOSED,
        to_state=SignalState.EXECUTED,
        source="paper_engine",
        reason="fuzzing_test",
    )

    assert any("Illegal signal state transition" in rec.message for rec in caplog.records), (
        "Expected error log for illegal transition"
    )

    audit_path = tmp_path / "audit.jsonl"
    if audit_path.exists():
        assert "signal_state_transition" not in audit_path.read_text(encoding="utf-8")
