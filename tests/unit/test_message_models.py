"""Tests for message_models — NEWS, SIGNAL, EXCHANGE_RESPONSE."""

from __future__ import annotations

import pytest

from app.messaging.message_models import (
    Direction,
    EntryType,
    ExchangeAction,
    ExchangeResponse,
    MarketType,
    MessageType,
    NewsMessage,
    Priority,
    ResponseStatus,
    RiskMode,
    Side,
    SignalStatus,
    TradingSignal,
)


class TestNewsMessage:
    def test_creation(self) -> None:
        msg = NewsMessage(
            source="Premium Signals",
            title="Bitcoin shows downside pressure",
            message="Sentiment weakens after rejection.",
            market="Futures",
            symbol="BTC/USDT",
            priority=Priority.MEDIUM,
        )
        assert msg.message_type == MessageType.NEWS
        assert msg.source == "Premium Signals"
        assert msg.priority == Priority.MEDIUM

    def test_immutable(self) -> None:
        msg = NewsMessage(source="test", title="test")
        with pytest.raises(AttributeError):
            msg.source = "changed"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        msg = NewsMessage(source="src", title="ttl", symbol="ETH/USDT")
        d = msg.to_dict()
        assert d["message_type"] == "news"
        assert d["source"] == "src"
        assert d["symbol"] == "ETH/USDT"

    def test_default_priority(self) -> None:
        msg = NewsMessage(source="s", title="t")
        assert msg.priority == Priority.MEDIUM


class TestTradingSignal:
    def test_short_signal(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-20260325-BTCUSDT-001",
            symbol="BTCUSDT",
            display_symbol="BTC/USDT",
            side=Side.SELL,
            direction=Direction.SHORT,
            entry_type=EntryType.BELOW,
            entry_value=74700,
            targets=[72800],
            stop_loss=76600,
            leverage=10,
        )
        assert sig.message_type == MessageType.SIGNAL
        assert sig.side == Side.SELL
        assert sig.direction == Direction.SHORT
        assert sig.entry_type == EntryType.BELOW
        assert sig.targets == [72800]

    def test_long_signal_with_multiple_targets(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-20260325-BATUSDT-001",
            symbol="BATUSDT",
            side=Side.BUY,
            direction=Direction.LONG,
            entry_type=EntryType.AT,
            entry_value=1126,
            targets=[1131, 1137, 1142, 1148],
            stop_loss=1080,
            leverage=10,
        )
        assert len(sig.targets) == 4
        assert sig.targets[2] == 1142

    def test_range_entry(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-001",
            symbol="BTCUSDT",
            entry_type=EntryType.RANGE,
            entry_min=1120,
            entry_max=1128,
        )
        assert sig.entry_type == EntryType.RANGE
        assert sig.entry_min == 1120
        assert sig.entry_max == 1128

    def test_is_valid_for_execution_true(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-001",
            source="Premium Signals",
            exchange_scope=["binance_futures"],
            symbol="BTCUSDT",
            direction=Direction.LONG,
            targets=[70000],
            stop_loss=62000,
        )
        assert sig.is_valid_for_execution is True
        assert sig.validation_errors == []

    def test_is_valid_for_execution_missing_sl(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-001",
            source="test",
            exchange_scope=["binance_futures"],
            symbol="BTCUSDT",
            direction=Direction.LONG,
            targets=[70000],
        )
        assert sig.is_valid_for_execution is False
        assert "missing_stop_loss" in sig.validation_errors

    def test_is_valid_for_execution_missing_symbol(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-001",
            source="test",
            exchange_scope=["binance_futures"],
            direction=Direction.LONG,
            targets=[70000],
            stop_loss=62000,
        )
        assert sig.is_valid_for_execution is False
        assert "missing_symbol" in sig.validation_errors

    def test_immutable(self) -> None:
        sig = TradingSignal(signal_id="x", symbol="BTC")
        with pytest.raises(AttributeError):
            sig.symbol = "ETH"  # type: ignore[misc]

    def test_to_dict_includes_optional_fields(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-001",
            symbol="BTCUSDT",
            entry_value=74700,
            notes="Execute only if supported",
            confidence=0.85,
        )
        d = sig.to_dict()
        assert d["entry_value"] == 74700
        assert d["notes"] == "Execute only if supported"
        assert d["confidence"] == 0.85

    def test_to_dict_excludes_none_optional(self) -> None:
        sig = TradingSignal(signal_id="SIG-001", symbol="BTCUSDT")
        d = sig.to_dict()
        assert "entry_value" not in d
        assert "notes" not in d
        assert "confidence" not in d

    def test_default_values(self) -> None:
        sig = TradingSignal()
        assert sig.market_type == MarketType.FUTURES
        assert sig.side == Side.BUY
        assert sig.direction == Direction.LONG
        assert sig.entry_type == EntryType.MARKET
        assert sig.leverage == 1
        assert sig.risk_mode == RiskMode.ISOLATED
        assert sig.status == SignalStatus.NEW

    def test_exchange_scope_list(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-001",
            exchange_scope=["binance_futures", "okx", "bybit"],
        )
        assert len(sig.exchange_scope) == 3
        d = sig.to_dict()
        assert d["exchange_scope"] == ["binance_futures", "okx", "bybit"]


class TestExchangeResponse:
    def test_success_order(self) -> None:
        resp = ExchangeResponse(
            response_id="EXR-001",
            related_signal_id="SIG-001",
            exchange="bybit",
            symbol="BTC/USDT",
            action=ExchangeAction.ORDER_CREATED,
            status=ResponseStatus.SUCCESS,
            entry_price=74695,
            quantity=0.025,
            leverage=10,
            stop_loss=76600,
            take_profit=72800,
            exchange_order_id="883726182",
        )
        assert resp.message_type == MessageType.EXCHANGE_RESPONSE
        assert resp.is_success is True
        assert resp.is_error is False

    def test_error_response(self) -> None:
        resp = ExchangeResponse(
            response_id="EXR-002",
            related_signal_id="SIG-001",
            exchange="okx",
            action=ExchangeAction.REJECTED,
            status=ResponseStatus.ERROR,
            error_code="INVALID_SYMBOL",
            message="Symbol not supported",
        )
        assert resp.is_error is True
        assert resp.error_code == "INVALID_SYMBOL"

    def test_position_closed(self) -> None:
        resp = ExchangeResponse(
            response_id="EXR-003",
            action=ExchangeAction.POSITION_CLOSED,
            status=ResponseStatus.SUCCESS,
            result="ALL_TARGETS_HIT",
            realized_profit="63%",
        )
        d = resp.to_dict()
        assert d["result"] == "ALL_TARGETS_HIT"
        assert d["realized_profit"] == "63%"

    def test_to_dict_excludes_none(self) -> None:
        resp = ExchangeResponse(
            response_id="EXR-001",
            exchange="binance",
        )
        d = resp.to_dict()
        assert "entry_price" not in d
        assert "quantity" not in d
        assert "error_code" not in d


class TestEnums:
    def test_side_values(self) -> None:
        assert Side.BUY.value == "buy"
        assert Side.SELL.value == "sell"

    def test_direction_values(self) -> None:
        assert Direction.LONG.value == "long"
        assert Direction.SHORT.value == "short"
        assert Direction.NEUTRAL.value == "neutral"

    def test_entry_types(self) -> None:
        types = [e.value for e in EntryType]
        assert "market" in types
        assert "below" in types
        assert "breakout_above" in types

    def test_exchange_actions(self) -> None:
        actions = [a.value for a in ExchangeAction]
        assert "order_created" in actions
        assert "take_profit_hit" in actions
        assert "stop_loss_hit" in actions
        assert "position_closed" in actions

    def test_market_types(self) -> None:
        assert MarketType.SPOT.value == "spot"
        assert MarketType.FUTURES.value == "futures"
        assert MarketType.MARGIN.value == "margin"
