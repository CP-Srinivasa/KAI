"""Signal candidate models — immutable, fully typed. (Security First)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class SignalDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


class SignalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SignalProvenance:
    """Origin tag for any signal feeding downstream pipelines.

    D-125 invariant: every TradingView-pivot signal must carry provenance so
    the later quality-bar phase can attribute precision deltas to the right
    signal path (RSS pipeline vs. tradingview_webhook vs. binance_ohlcv_rsi).
    `signal_path_id` is None for audit-only stages (e.g. TV-1 webhook ingest).
    """

    source: str  # e.g. "rss", "tradingview_webhook", "binance_ohlcv_rsi"
    version: str  # pivot stage tag, e.g. "tv-1", "tv-2", "tv-3"
    signal_path_id: str | None = None  # set when routed to a concrete pipeline


def _new_decision_id() -> str:
    return f"dec_{uuid.uuid4().hex[:12]}"


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class SignalCandidate:
    """
    Candidate trading signal derived from AnalysisResult + MarketDataPoint.

    Contains all mandatory KAI decision fields (see DECISION_SCHEMA.json).
    Immutable — never mutate after creation.

    Design invariants:
    - All required fields must be populated at construction
    - confidence_score must be in [0.0, 1.0]
    - entry_price must be > 0
    - stop_loss_price is required by default (require_stop_loss=True)
    """

    decision_id: str
    timestamp_utc: str
    symbol: str
    market: str  # "crypto" | "equities" | "forex" | "unknown"
    venue: str  # "paper" | exchange name
    mode: str  # "paper" | "live"
    direction: SignalDirection

    # Mandatory decision fields (KAI Decision Schema)
    thesis: str
    supporting_factors: tuple[str, ...]
    contradictory_factors: tuple[str, ...]
    confidence_score: float  # [0.0, 1.0]
    confluence_count: int

    # Market context
    market_regime: str  # "trending" | "ranging" | "volatile" | "unknown"
    volatility_state: str  # "low" | "normal" | "high" | "extreme"
    liquidity_state: str  # "adequate" | "low" | "critical"

    # Entry / exit
    entry_price: float
    stop_loss_price: float | None
    take_profit_price: float | None
    invalidation_condition: str

    # Risk
    risk_assessment: str
    position_size_rationale: str
    max_loss_estimate_pct: float

    # Traceability
    data_sources_used: tuple[str, ...]
    source_document_id: str
    model_version: str
    prompt_version: str

    # State (default: pending)
    approval_state: SignalState = SignalState.PENDING
    execution_state: SignalState = SignalState.PENDING

    # D-125 provenance: optional, fail-open (None = legacy/RSS path).
    provenance: SignalProvenance | None = None
