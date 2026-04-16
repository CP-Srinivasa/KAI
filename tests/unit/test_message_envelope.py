"""Tests for MessageEnvelope v2 routing wrapper."""

from __future__ import annotations

from app.messaging.message_models import (
    Direction,
    EntryType,
    ExchangeAction,
    ExchangeResponse,
    MarketType,
    MessageEnvelope,
    MessageType,
    NewsMessage,
    Priority,
    ResponseStatus,
    Side,
    SignalStatus,
    SourceChannel,
    TradingSignal,
)


def _news() -> NewsMessage:
    return NewsMessage(
        source="Premium Signals",
        title="BTC pressure",
        priority=Priority.HIGH,
        timestamp_utc="2026-04-15T10:00:00+00:00",
    )


def _signal() -> TradingSignal:
    return TradingSignal(
        signal_id="SIG-20260415-BTCUSDT-001",
        source="Premium Signals",
        exchange_scope=["binance_futures"],
        market_type=MarketType.FUTURES,
        symbol="BTCUSDT",
        display_symbol="BTC/USDT",
        side=Side.BUY,
        direction=Direction.LONG,
        entry_type=EntryType.MARKET,
        targets=[70000.0],
        stop_loss=62000.0,
        leverage=10,
        status=SignalStatus.NEW,
        timestamp_utc="2026-04-15T10:00:00+00:00",
    )


def _exchange_response() -> ExchangeResponse:
    return ExchangeResponse(
        response_id="EXR-20260415-BTCUSDT-100000",
        related_signal_id="SIG-20260415-BTCUSDT-001",
        exchange="bybit",
        symbol="BTCUSDT",
        action=ExchangeAction.ORDER_CREATED,
        status=ResponseStatus.SUCCESS,
        timestamp_utc="2026-04-15T10:00:05+00:00",
    )


class TestWrap:
    def test_wrap_news_sets_payload_type(self) -> None:
        env = MessageEnvelope.wrap(_news(), source_channel="telegram")
        assert env.payload_type == MessageType.NEWS
        assert env.source_channel == SourceChannel.TELEGRAM
        assert env.payload["title"] == "BTC pressure"

    def test_wrap_signal_sets_payload_type(self) -> None:
        env = MessageEnvelope.wrap(_signal(), source_channel="dashboard")
        assert env.payload_type == MessageType.SIGNAL
        assert env.source_channel == SourceChannel.DASHBOARD
        assert env.payload["signal_id"] == "SIG-20260415-BTCUSDT-001"

    def test_wrap_exchange_response_sets_payload_type(self) -> None:
        env = MessageEnvelope.wrap(_exchange_response(), source_channel="api")
        assert env.payload_type == MessageType.EXCHANGE_RESPONSE
        assert env.source_channel == SourceChannel.API

    def test_wrap_with_enum_source_channel(self) -> None:
        env = MessageEnvelope.wrap(_news(), source_channel=SourceChannel.VOICE)
        assert env.source_channel == SourceChannel.VOICE

    def test_unknown_source_channel_falls_back(self) -> None:
        env = MessageEnvelope.wrap(_news(), source_channel="not_a_channel")
        assert env.source_channel == SourceChannel.UNKNOWN

    def test_wrap_passes_metadata(self) -> None:
        env = MessageEnvelope.wrap(
            _news(),
            source_channel="telegram",
            chat_id=4242,
            operator_user_id="sascha",
            trace_id="t-123",
        )
        assert env.chat_id == 4242
        assert env.operator_user_id == "sascha"
        assert env.trace_id == "t-123"


class TestIdempotency:
    def test_same_payload_same_key(self) -> None:
        a = MessageEnvelope.wrap(_news(), source_channel="telegram")
        b = MessageEnvelope.wrap(_news(), source_channel="dashboard", chat_id=1)
        assert a.idempotency_key == b.idempotency_key

    def test_different_payload_different_key(self) -> None:
        other = NewsMessage(
            source="Premium Signals",
            title="Different headline",
            priority=Priority.HIGH,
            timestamp_utc="2026-04-15T10:00:00+00:00",
        )
        a = MessageEnvelope.wrap(_news(), source_channel="telegram")
        b = MessageEnvelope.wrap(other, source_channel="telegram")
        assert a.idempotency_key != b.idempotency_key

    def test_idempotency_key_is_32_hex_chars(self) -> None:
        env = MessageEnvelope.wrap(_news(), source_channel="telegram")
        assert len(env.idempotency_key) == 32
        int(env.idempotency_key, 16)  # must parse as hex


class TestSerialization:
    def test_to_dict_contains_canonical_fields(self) -> None:
        env = MessageEnvelope.wrap(
            _signal(), source_channel="telegram", chat_id=42
        )
        d = env.to_dict()
        assert d["envelope_id"] == env.envelope_id
        assert d["received_ts"] == env.received_ts
        assert d["source_channel"] == "telegram"
        assert d["payload_type"] == "signal"
        assert d["idempotency_key"] == env.idempotency_key
        assert d["chat_id"] == 42
        assert d["payload"]["signal_id"] == "SIG-20260415-BTCUSDT-001"

    def test_to_dict_omits_none_optional_fields(self) -> None:
        env = MessageEnvelope.wrap(_news(), source_channel="api")
        d = env.to_dict()
        assert "chat_id" not in d
        assert "operator_user_id" not in d
        assert "trace_id" not in d

    def test_envelope_id_has_expected_prefix_and_stamp(self) -> None:
        env = MessageEnvelope.wrap(
            _news(),
            source_channel="telegram",
            received_ts="2026-04-15T10:00:00+00:00",
        )
        assert env.envelope_id.startswith("ENV-20260415100000-")


class TestExplicitTimestamp:
    def test_wrap_respects_given_received_ts(self) -> None:
        ts = "2020-01-01T00:00:00+00:00"
        env = MessageEnvelope.wrap(_news(), source_channel="api", received_ts=ts)
        assert env.received_ts == ts

    def test_received_ts_does_not_affect_idempotency(self) -> None:
        a = MessageEnvelope.wrap(
            _news(), source_channel="api", received_ts="2020-01-01T00:00:00+00:00"
        )
        b = MessageEnvelope.wrap(
            _news(), source_channel="api", received_ts="2099-12-31T23:59:59+00:00"
        )
        # News payload has a timestamp field so the news itself carries time;
        # but identical news objects (same timestamp_utc) must map to same key
        # regardless of envelope received_ts.
        assert a.idempotency_key == b.idempotency_key
