"""Canonical liquidation-event schema (source-agnostic, v1).

#316 Liquidations — Data Foundation. One normalized shape that every provider
(Binance ``!forceOrder@arr`` canary first, CoinGlass later) maps into, so the
ledger, metrics and dashboard never depend on a provider's wire format.

Read-only by design: a ``LiquidationEvent`` is an observation. Nothing here
opens, sizes, gates or blocks a trade — liquidation data is a risk/volatility
*measurement* until an edge is proven (Track-2/3 doctrine: erst messen, dann
gewichten, dann ggf. Gate).

``is_snapshot_limited`` is mandatory and must be True for the Binance all-market
stream, which pushes only the LARGEST liquidation per symbol per 1000 ms — i.e.
the feed under-counts true market-wide liquidation pressure. Consumers MUST
surface this so the number is never read as a complete market total.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "liquidation_event.v1"

LiquidatedSide = Literal["LONG", "SHORT", "UNKNOWN"]


class LiquidationEvent(BaseModel):
    """A single (normalized) perpetual-swap liquidation observation."""

    # Stable, idempotent id (provider derives it; dedupe key for the ledger).
    event_id: str
    # Provider channel, e.g. "binance_forceorder" | "coinglass".
    source: str
    # Venue the liquidation happened on, e.g. "binance".
    exchange: str
    # Provider symbol, e.g. "BTCUSDT".
    symbol: str
    # Normalized base asset, e.g. "BTC".
    asset_id: str
    # Raw order side that closed the position ("SELL"/"BUY"/...).
    side: str
    # Position side that got liquidated: a forced SELL closes a LONG, a forced
    # BUY closes a SHORT. UNKNOWN when the provider does not disclose it.
    liquidated_side: LiquidatedSide
    price: float = Field(ge=0.0)
    quantity: float = Field(ge=0.0)
    # USD notional (price × quantity for USDT-margined pairs ≈ USD).
    notional_usd: float = Field(ge=0.0)
    # When the liquidation occurred (exchange clock).
    event_time: datetime
    # When KAI received/normalized it (our clock).
    received_at: datetime
    # received_at − event_time, milliseconds (>= 0; 0 if event_time is ahead).
    latency_ms: int = Field(ge=0)
    # sha256 of the raw provider payload — provenance/audit.
    raw_payload_hash: str
    # Provider confidence in [0,1]. Direct exchange feed = 1.0.
    confidence: float = Field(ge=0.0, le=1.0)
    # True when the source under-reports (Binance all-market: largest per 1000ms).
    is_snapshot_limited: bool
    schema_version: str = SCHEMA_VERSION

    model_config = {"frozen": True}
