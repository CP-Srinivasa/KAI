"""Binance USDT-M futures liquidation normalizer — ``!forceOrder@arr`` (read-only).

Maps one raw Binance force-order message into the canonical
:class:`~app.market_data.liquidation_event.LiquidationEvent`. Pure + offline:
``normalize_forceorder`` takes the already-parsed dict and never touches the
network — the live WebSocket consumer (follow-up) feeds it.

Binance all-market stream caveat: ``!forceOrder@arr`` pushes only the LARGEST
liquidation per symbol per 1000 ms, so every event is flagged
``is_snapshot_limited=True`` — the feed under-counts true liquidation pressure.

Wire shape (one message)::

    {"e": "forceOrder", "E": 1568014460893, "o": {
        "s": "BTCUSDT", "S": "SELL", "ap": "9910.0", "p": "9910.0",
        "q": "0.014", "z": "0.014", "X": "FILLED", "T": 1568014460893}}

A forced SELL closes a LONG position; a forced BUY closes a SHORT.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.market_data.liquidation_event import LiquidationEvent

SOURCE = "binance_forceorder"
EXCHANGE = "binance"

# Quote suffixes stripped (longest-first) to derive the base asset_id.
_QUOTE_SUFFIXES: tuple[str, ...] = (
    "USDT",
    "USDC",
    "FDUSD",
    "TUSD",
    "BUSD",
    "USD",
)

_SIDE_TO_LIQUIDATED: dict[str, str] = {"SELL": "LONG", "BUY": "SHORT"}


def _asset_id(symbol: str) -> str:
    """Strip the (longest matching) quote suffix → base asset. Unknown → symbol."""
    for suffix in _QUOTE_SUFFIXES:
        if symbol.endswith(suffix) and len(symbol) > len(suffix):
            return symbol[: -len(suffix)]
    return symbol


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out >= 0.0 else None


def _payload_hash(raw: dict[str, Any]) -> str:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_forceorder(
    raw: dict[str, Any],
    *,
    received_at: datetime | None = None,
) -> LiquidationEvent | None:
    """Normalize one Binance force-order message; return None if malformed.

    Fail-closed: any missing/invalid critical field (symbol, price, quantity,
    event time) yields ``None`` rather than a fabricated or partial event.
    """
    if not isinstance(raw, dict):
        return None
    order = raw.get("o")
    if not isinstance(order, dict):
        return None

    symbol = order.get("s")
    if not isinstance(symbol, str) or not symbol:
        return None

    # Average fill price is the executed price; fall back to order price.
    price = _f(order.get("ap")) or _f(order.get("p"))
    # Filled accumulated quantity is the liquidated amount; fall back to original.
    quantity = _f(order.get("z")) or _f(order.get("q"))
    if not price or not quantity:
        return None

    # Trade time (exchange clock); fall back to the top-level event time.
    ts_ms = order.get("T") or raw.get("E")
    if ts_ms is None:
        return None
    try:
        ts_int = int(ts_ms)
        event_time = datetime.fromtimestamp(ts_int / 1000, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None

    received = received_at or datetime.now(UTC)
    latency_ms = max(0, int((received - event_time).total_seconds() * 1000))

    side = str(order.get("S", "")).upper()
    liquidated_side = _SIDE_TO_LIQUIDATED.get(side, "UNKNOWN")

    payload_hash = _payload_hash(raw)
    event_id = f"{EXCHANGE}:{symbol}:{ts_int}:{payload_hash[:16]}"

    return LiquidationEvent(
        event_id=event_id,
        source=SOURCE,
        exchange=EXCHANGE,
        symbol=symbol,
        asset_id=_asset_id(symbol),
        side=side,
        liquidated_side=liquidated_side,  # type: ignore[arg-type]
        price=price,
        quantity=quantity,
        notional_usd=round(price * quantity, 2),
        event_time=event_time,
        received_at=received,
        latency_ms=latency_ms,
        raw_payload_hash=payload_hash,
        confidence=1.0,  # direct exchange feed; under-count is captured below
        is_snapshot_limited=True,  # all-market stream: largest per symbol/1000ms
    )
