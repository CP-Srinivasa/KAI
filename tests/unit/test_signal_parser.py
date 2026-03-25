"""Tests for signal_parser — structured Telegram signal parsing."""

from __future__ import annotations

import pytest

from app.messaging.signal_parser import (
    SignalDirection,
    SignalParseError,
    parse_signal_message,
)


class TestParseSignalMessage:
    """Tests for parse_signal_message()."""

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
        assert signal.stop_loss is None
        assert signal.take_profit is None

    def test_long_alias(self) -> None:
        signal = parse_signal_message("LONG SOL SL=120 TP=200 SIZE=0.5")
        assert signal.direction == SignalDirection.BUY
        assert signal.asset == "SOL"
        assert signal.price is None  # no price given
        assert signal.stop_loss == 120.0
        assert signal.take_profit == 200.0
        assert signal.size == 0.5

    def test_short_alias(self) -> None:
        signal = parse_signal_message("SHORT BTC 64000")
        assert signal.direction == SignalDirection.SELL
        assert signal.asset == "BTC"
        assert signal.price == 64000.0

    def test_german_kaufen(self) -> None:
        signal = parse_signal_message("KAUFEN ETH 3000")
        assert signal.direction == SignalDirection.BUY
        assert signal.asset == "ETH"
        assert signal.price == 3000.0

    def test_german_verkaufen(self) -> None:
        signal = parse_signal_message("VERKAUFEN BTC 60000")
        assert signal.direction == SignalDirection.SELL
        assert signal.asset == "BTC"

    def test_case_insensitive(self) -> None:
        signal = parse_signal_message("buy btc 65000")
        assert signal.direction == SignalDirection.BUY
        assert signal.asset == "BTC"

    def test_comma_decimal(self) -> None:
        signal = parse_signal_message("BUY ETH 3400,50 SL=3200,00")
        assert signal.price == 3400.50
        assert signal.stop_loss == 3200.0

    def test_market_order_property(self) -> None:
        signal = parse_signal_message("BUY BTC")
        assert signal.is_market_order is True

    def test_limit_order_property(self) -> None:
        signal = parse_signal_message("BUY BTC 65000")
        assert signal.is_market_order is False

    def test_stop_alias(self) -> None:
        signal = parse_signal_message("BUY BTC STOP=62000 TAKE=70000")
        assert signal.stop_loss == 62000.0
        assert signal.take_profit == 70000.0


class TestSignalParseErrors:
    """Tests for error cases."""

    def test_empty_string(self) -> None:
        with pytest.raises(SignalParseError, match="Empty"):
            parse_signal_message("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(SignalParseError, match="Empty"):
            parse_signal_message("   ")

    def test_single_token(self) -> None:
        with pytest.raises(SignalParseError, match="DIRECTION and ASSET"):
            parse_signal_message("BUY")

    def test_unknown_direction(self) -> None:
        with pytest.raises(SignalParseError, match="Unknown direction"):
            parse_signal_message("HODL BTC 65000")

    def test_invalid_asset_numeric(self) -> None:
        with pytest.raises(SignalParseError, match="Invalid asset"):
            parse_signal_message("BUY 123 65000")

    def test_invalid_asset_too_short(self) -> None:
        with pytest.raises(SignalParseError, match="Invalid asset"):
            parse_signal_message("BUY X 65000")
