"""3-type Telegram message standard: NEWS, SIGNAL, EXCHANGE_RESPONSE.

Design invariants:
- side (buy/sell) and direction (long/short) are separate fields
- Telegram = display, JSON = truth
- All models are frozen dataclasses (immutable)
- NEWS never triggers order execution
- SIGNAL requires minimum fields before auto-trade
- EXCHANGE_RESPONSE always references a signal_id
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MessageType(StrEnum):
    """Top-level message classification."""

    NEWS = "news"
    SIGNAL = "signal"
    EXCHANGE_RESPONSE = "exchange_response"


class MarketType(StrEnum):
    SPOT = "spot"
    FUTURES = "futures"
    MARGIN = "margin"
    OPTIONS = "options"


class Side(StrEnum):
    """Order side — independent of direction for hedge-mode support."""

    BUY = "buy"
    SELL = "sell"


class Direction(StrEnum):
    """Trade direction — semantically distinct from side."""

    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class EntryType(StrEnum):
    """Normalized entry rule types."""

    MARKET = "market"
    AT = "at"
    BELOW = "below"
    ABOVE = "above"
    RANGE = "range"
    BREAKOUT_ABOVE = "breakout_above"
    BREAKDOWN_BELOW = "breakdown_below"


class SignalStatus(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ExchangeAction(StrEnum):
    """Standardized exchange response action types."""

    RECEIVED = "received"
    VALIDATED = "validated"
    REJECTED = "rejected"
    ORDER_CREATED = "order_created"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    STOP_LOSS_SET = "stop_loss_set"
    TAKE_PROFIT_SET = "take_profit_set"
    TAKE_PROFIT_HIT = "take_profit_hit"
    STOP_LOSS_HIT = "stop_loss_hit"
    POSITION_CLOSED = "position_closed"
    CANCELLED = "cancelled"
    ERROR = "error"


class ResponseStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    PENDING = "pending"


class RiskMode(StrEnum):
    ISOLATED = "isolated"
    CROSS = "cross"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# NEWS
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NewsMessage:
    """Informational message — no order execution allowed."""

    source: str
    title: str
    message: str = ""
    market: str = ""  # e.g. "Futures", "Spot", "Crypto"
    symbol: str = ""  # optional, e.g. "BTC/USDT"
    priority: Priority = Priority.MEDIUM
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    @property
    def message_type(self) -> MessageType:
        return MessageType.NEWS

    def to_dict(self) -> dict[str, object]:
        """Canonical JSON representation."""
        return {
            "message_type": self.message_type.value,
            "source": self.source,
            "title": self.title,
            "message": self.message,
            "market": self.market,
            "symbol": self.symbol,
            "priority": self.priority.value,
            "timestamp_utc": self.timestamp_utc,
        }


# ---------------------------------------------------------------------------
# SIGNAL
# ---------------------------------------------------------------------------

def _generate_signal_id(symbol: str) -> str:
    """Generate a unique signal ID: SIG-YYYYMMDD-SYMBOL-NNN."""
    now = datetime.now(UTC)
    clean_symbol = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    return f"SIG-{now.strftime('%Y%m%d')}-{clean_symbol}-{now.strftime('%H%M%S')}"


@dataclass(frozen=True)
class TradingSignal:
    """Concrete trading signal — can trigger order execution.

    Minimum required for auto-trade:
    signal_id, symbol, side, direction, entry_type, stop_loss, leverage
    """

    # Identity
    signal_id: str = ""
    source: str = ""

    # Market
    exchange_scope: list[str] = field(default_factory=list)
    market_type: MarketType = MarketType.FUTURES
    symbol: str = ""  # internal: "BTCUSDT"
    display_symbol: str = ""  # human: "BTC/USDT"

    # Direction
    side: Side = Side.BUY
    direction: Direction = Direction.LONG

    # Entry
    entry_type: EntryType = EntryType.MARKET
    entry_value: float | None = None
    entry_min: float | None = None  # for RANGE entries
    entry_max: float | None = None  # for RANGE entries

    # Risk
    targets: list[float] = field(default_factory=list)
    stop_loss: float | None = None
    leverage: int = 1
    risk_mode: RiskMode = RiskMode.ISOLATED

    # Metadata
    status: SignalStatus = SignalStatus.NEW
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    notes: str = ""
    confidence: float | None = None  # 0.0-1.0
    strategy_tag: str = ""
    time_in_force: str = "GTC"
    reduce_only: bool = False
    position_size_suggestion: float | None = None

    @property
    def message_type(self) -> MessageType:
        return MessageType.SIGNAL

    @property
    def is_valid_for_execution(self) -> bool:
        """Check whether the signal satisfies strict execution prerequisites."""
        return not self.validation_errors

    @property
    def validation_errors(self) -> list[str]:
        """Return validation errors for execution safety checks."""
        errors: list[str] = []
        if not self.signal_id.strip():
            errors.append("missing_signal_id")
        if not self.source.strip():
            errors.append("missing_source")
        if not self.exchange_scope:
            errors.append("missing_exchange_scope")
        if not self.symbol.strip():
            errors.append("missing_symbol")
        if self.direction == Direction.NEUTRAL:
            errors.append("invalid_direction_for_execution")

        if self.entry_type == EntryType.RANGE:
            if self.entry_min is None or self.entry_max is None:
                errors.append("missing_entry_range")
            elif self.entry_min <= 0 or self.entry_max <= 0:
                errors.append("invalid_entry_range")
            elif self.entry_min >= self.entry_max:
                errors.append("invalid_entry_range_order")
        elif self.entry_type != EntryType.MARKET:
            if self.entry_value is None or self.entry_value <= 0:
                errors.append("missing_entry_value")

        if not self.targets:
            errors.append("missing_targets")
        elif any(target <= 0 for target in self.targets):
            errors.append("invalid_targets")

        if self.stop_loss is None or self.stop_loss <= 0:
            errors.append("missing_stop_loss")
        if self.leverage <= 0:
            errors.append("invalid_leverage")
        if not self.timestamp_utc.strip():
            errors.append("missing_timestamp")
        return errors

    def to_dict(self) -> dict[str, object]:
        """Canonical JSON representation (internal truth)."""
        d: dict[str, object] = {
            "message_type": self.message_type.value,
            "signal_id": self.signal_id,
            "source": self.source,
            "exchange_scope": list(self.exchange_scope),
            "market_type": self.market_type.value,
            "symbol": self.symbol,
            "display_symbol": self.display_symbol,
            "side": self.side.value,
            "direction": self.direction.value,
            "entry_type": self.entry_type.value,
            "targets": list(self.targets),
            "stop_loss": self.stop_loss,
            "leverage": self.leverage,
            "risk_mode": self.risk_mode.value,
            "status": self.status.value,
            "timestamp_utc": self.timestamp_utc,
        }
        if self.entry_value is not None:
            d["entry_value"] = self.entry_value
        if self.entry_min is not None:
            d["entry_min"] = self.entry_min
        if self.entry_max is not None:
            d["entry_max"] = self.entry_max
        if self.notes:
            d["notes"] = self.notes
        if self.confidence is not None:
            d["confidence"] = self.confidence
        if self.strategy_tag:
            d["strategy_tag"] = self.strategy_tag
        if self.reduce_only:
            d["reduce_only"] = True
        if self.position_size_suggestion is not None:
            d["position_size_suggestion"] = self.position_size_suggestion
        return d


# ---------------------------------------------------------------------------
# EXCHANGE_RESPONSE
# ---------------------------------------------------------------------------

def _generate_response_id(symbol: str) -> str:
    """Generate a unique response ID: EXR-YYYYMMDD-SYMBOL-HHMMSS."""
    now = datetime.now(UTC)
    clean_symbol = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    return f"EXR-{now.strftime('%Y%m%d')}-{clean_symbol}-{now.strftime('%H%M%S')}"


@dataclass(frozen=True)
class ExchangeResponse:
    """Technical response from exchange or execution system."""

    # Identity
    response_id: str = ""
    related_signal_id: str = ""

    # Exchange
    exchange: str = ""  # e.g. "binance_futures", "bybit"
    symbol: str = ""
    market_type: MarketType = MarketType.FUTURES

    # Action
    action: ExchangeAction = ExchangeAction.RECEIVED
    status: ResponseStatus = ResponseStatus.PENDING

    # Order details
    order_side: Side | None = None
    position_side: Direction | None = None
    entry_price: float | None = None
    order_type: str = ""  # "limit", "market"
    quantity: float | None = None
    leverage: int | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    exchange_order_id: str = ""

    # Result
    result: str = ""  # e.g. "ALL_TARGETS_HIT"
    realized_profit: str = ""  # e.g. "63%"
    error_code: str = ""
    message: str = ""

    # Metadata
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    @property
    def message_type(self) -> MessageType:
        return MessageType.EXCHANGE_RESPONSE

    @property
    def is_success(self) -> bool:
        return self.status == ResponseStatus.SUCCESS

    @property
    def is_error(self) -> bool:
        return self.status == ResponseStatus.ERROR

    def to_dict(self) -> dict[str, object]:
        """Canonical JSON representation."""
        d: dict[str, object] = {
            "message_type": self.message_type.value,
            "response_id": self.response_id,
            "related_signal_id": self.related_signal_id,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "market_type": self.market_type.value,
            "action": self.action.value,
            "status": self.status.value,
            "timestamp_utc": self.timestamp_utc,
        }
        if self.order_side is not None:
            d["order_side"] = self.order_side.value
        if self.position_side is not None:
            d["position_side"] = self.position_side.value
        if self.entry_price is not None:
            d["entry_price"] = self.entry_price
        if self.order_type:
            d["order_type"] = self.order_type
        if self.quantity is not None:
            d["quantity"] = self.quantity
        if self.leverage is not None:
            d["leverage"] = self.leverage
        if self.stop_loss is not None:
            d["stop_loss"] = self.stop_loss
        if self.take_profit is not None:
            d["take_profit"] = self.take_profit
        if self.exchange_order_id:
            d["exchange_order_id"] = self.exchange_order_id
        if self.result:
            d["result"] = self.result
        if self.realized_profit:
            d["realized_profit"] = self.realized_profit
        if self.error_code:
            d["error_code"] = self.error_code
        if self.message:
            d["message"] = self.message
        return d


# ---------------------------------------------------------------------------
# ENVELOPE (v2 routing wrapper)
# ---------------------------------------------------------------------------

class SourceChannel(StrEnum):
    """Where a message entered the system."""

    TELEGRAM = "telegram"
    DASHBOARD = "dashboard"
    API = "api"
    VOICE = "voice"
    UNKNOWN = "unknown"


MessagePayload = NewsMessage | TradingSignal | ExchangeResponse


def _canonical_idempotency_key(payload_dict: dict[str, object]) -> str:
    """Deterministic key over canonical JSON — duplicate payloads collapse."""
    blob = json.dumps(payload_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def _generate_envelope_id(received_ts: str) -> str:
    """ENV-YYYYMMDDHHMMSS-<hex8> — sortable + unique enough per message."""
    stamp = "".join(ch for ch in received_ts if ch.isdigit())[:14]
    suffix = hashlib.sha256(received_ts.encode("utf-8")).hexdigest()[:8]
    return f"ENV-{stamp}-{suffix}"


@dataclass(frozen=True)
class MessageEnvelope:
    """Canonical routing wrapper around a NEWS/SIGNAL/EXCHANGE_RESPONSE payload.

    The envelope is the unit of record for routing and de-duplication.
    `idempotency_key` is deterministic over the payload, so the same
    structured block received twice (Telegram restart, dashboard re-submit)
    collapses to the same key and can be short-circuited by consumers.
    """

    envelope_id: str
    received_ts: str
    source_channel: SourceChannel
    payload_type: MessageType
    payload: dict[str, Any]
    idempotency_key: str
    chat_id: int | None = None
    operator_user_id: str | None = None
    trace_id: str | None = None

    @classmethod
    def wrap(
        cls,
        payload: MessagePayload,
        *,
        source_channel: SourceChannel | str,
        chat_id: int | None = None,
        operator_user_id: str | None = None,
        trace_id: str | None = None,
        received_ts: str | None = None,
    ) -> MessageEnvelope:
        """Wrap a 3-type payload into a canonical envelope.

        `received_ts` defaults to now(UTC). `idempotency_key` is derived
        from the payload's canonical dict (sort_keys) — deterministic.
        """
        if isinstance(source_channel, str):
            try:
                source_channel = SourceChannel(source_channel)
            except ValueError:
                source_channel = SourceChannel.UNKNOWN

        ts = received_ts or datetime.now(UTC).isoformat()
        payload_dict = payload.to_dict()
        idem = _canonical_idempotency_key(payload_dict)
        return cls(
            envelope_id=_generate_envelope_id(ts),
            received_ts=ts,
            source_channel=source_channel,
            payload_type=payload.message_type,
            payload=payload_dict,
            idempotency_key=idem,
            chat_id=chat_id,
            operator_user_id=operator_user_id,
            trace_id=trace_id,
        )

    def to_dict(self) -> dict[str, object]:
        """Canonical JSON representation for envelope audit logs."""
        d: dict[str, object] = {
            "envelope_id": self.envelope_id,
            "received_ts": self.received_ts,
            "source_channel": self.source_channel.value,
            "payload_type": self.payload_type.value,
            "idempotency_key": self.idempotency_key,
            "payload": dict(self.payload),
        }
        if self.chat_id is not None:
            d["chat_id"] = self.chat_id
        if self.operator_user_id:
            d["operator_user_id"] = self.operator_user_id
        if self.trace_id:
            d["trace_id"] = self.trace_id
        return d
