"""Tests for signal_parser structured and legacy parsing."""

from __future__ import annotations

import pytest

from app.messaging.signal_parser import (
    SignalDirection,
    SignalParseError,
    detect_message_type,
    parse_signal_message,
    parse_structured_message,
)


class TestParseSignalMessage:
    def test_buy_with_full_params(self) -> None:
        signal = parse_signal_message("BUY BTC 65000 SL=62000 TP=70000")
        assert signal.direction == SignalDirection.BUY
        assert signal.asset == "BTC"
        assert signal.price == 65000.0
        assert signal.stop_loss == 62000.0
        assert signal.take_profit == 70000.0
        assert signal.size is None

    def test_sell_minimal(self) -> None:
        signal = parse_signal_message("SELL ETH 3400")
        assert signal.direction == SignalDirection.SELL
        assert signal.asset == "ETH"
        assert signal.price == 3400.0

    def test_short_sell_compound_token(self) -> None:
        signal = parse_signal_message("SHORT/SELL BTC 64000")
        assert signal.direction == SignalDirection.SELL
        assert signal.asset == "BTC"

    def test_long_alias(self) -> None:
        signal = parse_signal_message("LONG SOL SL=120 TP=200 SIZE=0.5")
        assert signal.direction == SignalDirection.BUY
        assert signal.asset == "SOL"
        assert signal.price is None
        assert signal.stop_loss == 120.0
        assert signal.take_profit == 200.0
        assert signal.size == 0.5

    def test_german_aliases(self) -> None:
        buy_signal = parse_signal_message("KAUFEN ETH 3000")
        sell_signal = parse_signal_message("VERKAUFEN BTC 60000")
        assert buy_signal.direction == SignalDirection.BUY
        assert sell_signal.direction == SignalDirection.SELL

    def test_market_order_property(self) -> None:
        signal = parse_signal_message("BUY BTC")
        assert signal.is_market_order is True


class TestSignalParseErrors:
    def test_empty_string(self) -> None:
        with pytest.raises(SignalParseError, match="Empty"):
            parse_signal_message("")

    def test_single_token(self) -> None:
        with pytest.raises(SignalParseError, match="DIRECTION and ASSET"):
            parse_signal_message("BUY")

    def test_unknown_direction(self) -> None:
        with pytest.raises(SignalParseError, match="Unknown direction"):
            parse_signal_message("HODL BTC 65000")

    def test_invalid_asset_numeric(self) -> None:
        with pytest.raises(SignalParseError, match="Invalid asset"):
            parse_signal_message("BUY 123 65000")


class TestDetectMessageType:
    def test_bracket_headers(self) -> None:
        assert detect_message_type("[SIGNAL]\nSymbol: BTC/USDT") == "signal"
        assert detect_message_type("[NEWS]\nTitle: Test") == "news"
        assert detect_message_type("[EXCHANGE_RESPONSE]\nExchange: Bybit") == "exchange_response"

    def test_emoji_headers(self) -> None:
        assert detect_message_type("📡 SIGNAL\nSymbol: BTC/USDT") == "signal"
        assert detect_message_type("📰 NEWS\nTitle: Test") == "news"
        assert (
            detect_message_type("✅ EXCHANGE RESPONSE\nExchange: Bybit")
            == "exchange_response"
        )

    def test_no_header(self) -> None:
        assert detect_message_type("BUY BTC 65000") is None


class TestParseStructuredSignal:
    def test_full_short_signal(self) -> None:
        text = """[SIGNAL]
Signal ID: SIG-20260325-BTCUSDT-001
Source: Premium Signals
Exchange Scope: Binance Futures, OKX, Bybit
Market Type: Futures
Symbol: BTC/USDT
Side: SELL
Direction: SHORT
Entry Rule: BELOW 74700
Targets: 72800
Leverage: 10x
Stop Loss: 76600
Risk Mode: Isolated
Status: NEW
Timestamp: 2026-03-25T18:31:00Z
"""
        sig = parse_structured_message(text)
        assert sig.message_type.value == "signal"
        assert sig.signal_id == "SIG-20260325-BTCUSDT-001"
        assert sig.source == "Premium Signals"
        assert sig.symbol == "BTCUSDT"
        assert sig.display_symbol == "BTC/USDT"
        assert sig.side.value == "sell"
        assert sig.direction.value == "short"
        assert sig.entry_type.value == "below"
        assert sig.entry_value == 74700
        assert sig.targets == [72800]
        assert sig.stop_loss == 76600
        assert sig.leverage == 10
        assert sig.risk_mode.value == "isolated"
        assert sig.exchange_scope == ["binance_futures", "okx", "bybit"]
        assert sig.is_valid_for_execution is True

    def test_range_entry(self) -> None:
        text = """[SIGNAL]
Source: Premium Signals
Exchange Scope: ["binance_futures"]
Symbol: ETH/USDT
Direction: LONG
Entry Rule: RANGE 1120 1128
Targets: [1131, 1137]
Stop Loss: 1080
"""
        sig = parse_structured_message(text)
        assert sig.entry_type.value == "range"
        assert sig.entry_min == 1120
        assert sig.entry_max == 1128
        assert sig.targets == [1131, 1137]

    def test_auto_generates_signal_id(self) -> None:
        text = """[SIGNAL]
Symbol: BTC/USDT
Direction: LONG
"""
        sig = parse_structured_message(text)
        assert sig.signal_id.startswith("SIG-")


class TestParseStructuredNews:
    def test_basic_news(self) -> None:
        text = """[NEWS]
Source: Premium Signals
Market: Futures
Symbol: BTC/USDT
Title: Bitcoin shows increasing downside pressure
Message: Market sentiment weakens.
Priority: Medium
"""
        news = parse_structured_message(text)
        assert news.message_type.value == "news"
        assert news.source == "Premium Signals"
        assert news.title == "Bitcoin shows increasing downside pressure"
        assert news.priority.value == "medium"


class TestParseStructuredExchangeResponse:
    def test_success(self) -> None:
        text = """[EXCHANGE_RESPONSE]
Response ID: EXR-20260325-BTCUSDT-001
Related Signal ID: SIG-20260325-BTCUSDT-001
Exchange: Bybit
Symbol: BTC/USDT
Market Type: Futures
Action: ORDER_CREATED
Status: SUCCESS
Entry Price: 74695
Quantity: 0.025
Leverage: 10x
Stop Loss: 76600
Take Profit: 72800
Exchange Order ID: 883726182
Timestamp: 2026-03-25T18:31:03Z
"""
        resp = parse_structured_message(text)
        assert resp.message_type.value == "exchange_response"
        assert resp.response_id == "EXR-20260325-BTCUSDT-001"
        assert resp.related_signal_id == "SIG-20260325-BTCUSDT-001"
        assert resp.exchange == "bybit"
        assert resp.action.value == "order_created"
        assert resp.status.value == "success"
        assert resp.entry_price == 74695
        assert resp.exchange_order_id == "883726182"

    def test_alias_action_order_rejected_maps_to_rejected(self) -> None:
        text = """✅ EXCHANGE RESPONSE
Exchange: OKX
Action: ORDER_REJECTED
Status: ERROR
Error Code: INVALID_SYMBOL
Message: Symbol not supported.
"""
        resp = parse_structured_message(text)
        assert resp.exchange == "okx"
        assert resp.action.value == "rejected"
        assert resp.status.value == "error"
        assert resp.error_code == "INVALID_SYMBOL"

    def test_no_header_raises(self) -> None:
        with pytest.raises(SignalParseError, match="No message type"):
            parse_structured_message("BUY BTC 65000")
