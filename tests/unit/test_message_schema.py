"""Tests for runtime JSON-schema validation of 3-type Telegram messages."""

from __future__ import annotations

import pytest

from app.messaging.message_models import (
    Direction,
    EntryType,
    ExchangeAction,
    ExchangeResponse,
    MarketType,
    NewsMessage,
    ResponseStatus,
    Side,
    TradingSignal,
)
from app.messaging.message_schema import (
    MessageSchemaValidationError,
    validate_message_model,
    validate_message_payload,
)


def test_validate_news_payload_success() -> None:
    payload = {
        "message_type": "news",
        "source": "Premium Signals",
        "title": "Macro pressure remains elevated",
        "priority": "medium",
        "timestamp_utc": "2026-03-25T18:31:00Z",
    }
    validated = validate_message_payload(payload)
    assert validated["message_type"] == "news"


def test_validate_signal_payload_missing_required_field_fails() -> None:
    """Schema rejects signal missing hard-required fields (symbol, side, etc.)."""
    payload = {
        "message_type": "signal",
        "signal_id": "SIG-20260325-BTCUSDT-001",
        # missing: market_type, symbol, side, direction, entry_type, etc.
        "timestamp_utc": "2026-03-25T18:31:00Z",
    }
    with pytest.raises(MessageSchemaValidationError, match="schema validation failed"):
        validate_message_payload(payload)


def test_validate_exchange_response_payload_success() -> None:
    payload = {
        "message_type": "exchange_response",
        "response_id": "EXR-20260325-BTCUSDT-001",
        "related_signal_id": "SIG-20260325-BTCUSDT-001",
        "exchange": "bybit",
        "symbol": "BTC/USDT",
        "market_type": "futures",
        "action": "order_created",
        "status": "success",
        "timestamp_utc": "2026-03-25T18:31:03Z",
    }
    validated = validate_message_payload(payload)
    assert validated["message_type"] == "exchange_response"


def test_validate_message_model_signal_success() -> None:
    signal = TradingSignal(
        signal_id="SIG-20260325-BTCUSDT-001",
        source="Premium Signals",
        exchange_scope=["binance_futures", "bybit"],
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
        timestamp_utc="2026-03-25T18:31:00Z",
    )
    validated = validate_message_model(signal)
    assert validated["entry_type"] == "below"


def test_validate_message_model_news_fails_on_missing_title() -> None:
    news = NewsMessage(
        source="Premium Signals",
        title="",
        timestamp_utc="2026-03-25T18:31:00Z",
    )
    with pytest.raises(MessageSchemaValidationError, match="schema validation failed"):
        validate_message_model(news)


def test_validate_message_model_exchange_response_accepts_empty_related_signal() -> None:
    """Empty related_signal_id is allowed at schema level (checked at execution gate)."""
    response = ExchangeResponse(
        response_id="EXR-20260325-BTCUSDT-001",
        related_signal_id="",
        exchange="bybit",
        symbol="BTC/USDT",
        action=ExchangeAction.ORDER_CREATED,
        status=ResponseStatus.SUCCESS,
        timestamp_utc="2026-03-25T18:31:03Z",
    )
    validated = validate_message_model(response)
    assert validated["message_type"] == "exchange_response"
