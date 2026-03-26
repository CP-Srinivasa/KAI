"""Tests for message_formatter Telegram display + JSON output."""

from __future__ import annotations

import json

from app.messaging.message_formatter import (
    format_as_json,
    format_exchange_response_telegram,
    format_news_telegram,
    format_signal_telegram,
)
from app.messaging.message_models import (
    Direction,
    EntryType,
    ExchangeAction,
    ExchangeResponse,
    MarketType,
    NewsMessage,
    Priority,
    ResponseStatus,
    Side,
    TradingSignal,
)


class TestFormatNewsTelegram:
    def test_basic(self) -> None:
        msg = NewsMessage(
            source="Premium Signals",
            title="Bitcoin shows downside pressure",
            market="Futures",
            symbol="BTC/USDT",
        )
        text = format_news_telegram(msg)
        assert "NEWS" in text
        assert "Premium Signals" in text
        assert "BTC/USDT" in text
        assert "Bitcoin shows downside pressure" in text

    def test_priority_display(self) -> None:
        msg = NewsMessage(source="s", title="t", priority=Priority.HIGH)
        text = format_news_telegram(msg)
        assert "High" in text


class TestFormatSignalTelegram:
    def test_short_signal(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-20260325-BTCUSDT-001",
            market_type=MarketType.FUTURES,
            symbol="BTCUSDT",
            display_symbol="BTC/USDT",
            side=Side.SELL,
            direction=Direction.SHORT,
            entry_type=EntryType.BELOW,
            entry_value=74700,
            targets=[72800],
            stop_loss=76600,
            leverage=10,
            exchange_scope=["binance_futures", "okx", "bybit"],
        )
        text = format_signal_telegram(sig)
        assert "SIGNAL" in text
        assert "SIG-20260325-BTCUSDT-001" in text
        assert "BTC/USDT" in text
        assert "SELL" in text
        assert "SHORT" in text
        assert "BELOW 74700" in text
        assert "72800" in text
        assert "76600" in text
        assert "10x" in text
        assert "Binance Futures" in text

    def test_range_entry(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-001",
            symbol="BTCUSDT",
            entry_type=EntryType.RANGE,
            entry_min=1120,
            entry_max=1128,
        )
        text = format_signal_telegram(sig)
        assert "RANGE 1120" in text
        assert "1128" in text

    def test_no_leverage_display_when_1(self) -> None:
        sig = TradingSignal(signal_id="SIG-001", symbol="BTC", leverage=1)
        text = format_signal_telegram(sig)
        assert "Leverage" not in text


class TestFormatExchangeResponseTelegram:
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
            exchange_order_id="883726182",
        )
        text = format_exchange_response_telegram(resp)
        assert "EXCHANGE RESPONSE" in text
        assert "SIG-001" in text
        assert "Bybit" in text
        assert "74695" in text
        assert "883726182" in text

    def test_error_response(self) -> None:
        resp = ExchangeResponse(
            response_id="EXR-002",
            exchange="okx",
            action=ExchangeAction.REJECTED,
            status=ResponseStatus.ERROR,
            error_code="INVALID_SYMBOL",
            message="Symbol not supported",
        )
        text = format_exchange_response_telegram(resp)
        assert "INVALID_SYMBOL" in text

    def test_tp_hit(self) -> None:
        resp = ExchangeResponse(
            action=ExchangeAction.TAKE_PROFIT_HIT,
            status=ResponseStatus.SUCCESS,
            result="ALL_TARGETS_HIT",
            realized_profit="63%",
        )
        text = format_exchange_response_telegram(resp)
        assert "ALL_TARGETS_HIT" in text
        assert "63%" in text


class TestFormatAsJson:
    def test_news_json(self) -> None:
        msg = NewsMessage(source="src", title="ttl")
        data = json.loads(format_as_json(msg))
        assert data["message_type"] == "news"

    def test_signal_json(self) -> None:
        sig = TradingSignal(
            signal_id="SIG-001",
            symbol="BTCUSDT",
            targets=[72800, 71000],
        )
        data = json.loads(format_as_json(sig))
        assert data["message_type"] == "signal"
        assert data["targets"] == [72800, 71000]

    def test_response_json(self) -> None:
        resp = ExchangeResponse(response_id="EXR-001", exchange="binance_futures")
        data = json.loads(format_as_json(resp))
        assert data["message_type"] == "exchange_response"
        assert data["exchange"] == "binance_futures"
