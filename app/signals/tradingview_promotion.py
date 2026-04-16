"""TV-3.1: operator-gated promotion of pending TV events to SignalCandidate.

A `TradingViewSignalEvent` in the pending queue carries only what a TV
webhook alert contains — not the KAI Decision-Schema fields (thesis,
confluence, risk assessment). Promotion is therefore an **explicit
operator step**, not an automatic conversion:

    pending TV-event  +  operator judgement  +  optional OHLCV/RSI context
    ────────────────────────────────────────────────────────────────────▶
                               SignalCandidate

Invariants:
    - Append-only: pending_signals.jsonl and promoted/decisions logs are
      never rewritten. Decisions live in `pending_decisions.jsonl`
      (event_id + decision + reason + timestamp). `list` filters by
      replaying this log.
    - Re-deciding the same event is rejected (idempotency).
    - Market classification is a heuristic; operator can override.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.analysis.indicators import RSI_DEFAULT_PERIOD, compute_rsi
from app.signals.models import (
    SignalCandidate,
    SignalDirection,
    SignalProvenance,
    SignalState,
)
from app.signals.tradingview_event import TradingViewSignalEvent

Decision = Literal["promoted", "rejected"]

_TV_ACTION_TO_DIRECTION: dict[str, SignalDirection] = {
    "buy": SignalDirection.LONG,
    "sell": SignalDirection.SHORT,
}


class PromotionError(ValueError):
    """Raised when a TV event cannot be promoted (invalid action, missing price)."""


@dataclass(frozen=True)
class DecisionRecord:
    event_id: str
    decision: Decision
    timestamp_utc: str
    operator_reason: str
    promoted_decision_id: str | None  # set only when decision == "promoted"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_decision_id() -> str:
    return f"dec_{uuid4().hex[:12]}"


def load_pending_events(pending_path: Path) -> list[TradingViewSignalEvent]:
    """Read and parse all pending events. Malformed rows are silently skipped."""
    if not pending_path.exists():
        return []
    events: list[TradingViewSignalEvent] = []
    for line in pending_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            prov = raw["provenance"]
            events.append(
                TradingViewSignalEvent(
                    event_id=raw["event_id"],
                    received_at=raw["received_at"],
                    ticker=raw["ticker"],
                    action=raw["action"],
                    price=raw.get("price"),
                    note=raw.get("note"),
                    strategy=raw.get("strategy"),
                    source_request_id=raw["source_request_id"],
                    source_payload_hash=raw["source_payload_hash"],
                    provenance=SignalProvenance(
                        source=prov["source"],
                        version=prov["version"],
                        signal_path_id=prov.get("signal_path_id"),
                    ),
                )
            )
        except (KeyError, json.JSONDecodeError, TypeError):
            continue
    return events


def load_decisions(decisions_path: Path) -> dict[str, DecisionRecord]:
    """Load the decision log. Returns event_id → most recent DecisionRecord."""
    if not decisions_path.exists():
        return {}
    decided: dict[str, DecisionRecord] = {}
    for line in decisions_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            decided[raw["event_id"]] = DecisionRecord(
                event_id=raw["event_id"],
                decision=raw["decision"],
                timestamp_utc=raw["timestamp_utc"],
                operator_reason=raw.get("operator_reason", ""),
                promoted_decision_id=raw.get("promoted_decision_id"),
            )
        except (KeyError, json.JSONDecodeError, TypeError):
            continue
    return decided


def filter_open_events(
    events: list[TradingViewSignalEvent],
    decisions: dict[str, DecisionRecord],
) -> list[TradingViewSignalEvent]:
    """Return events that have no recorded decision yet."""
    return [ev for ev in events if ev.event_id not in decisions]


def append_decision(path: Path, record: DecisionRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(record), ensure_ascii=False, separators=(",", ":")) + "\n")


def append_promoted_candidate(path: Path, candidate: SignalCandidate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(_candidate_to_dict(candidate), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )


def _candidate_to_dict(c: SignalCandidate) -> dict[str, object]:
    data = asdict(c)
    data["direction"] = c.direction.value
    data["approval_state"] = c.approval_state.value
    data["execution_state"] = c.execution_state.value
    return data


def _heuristic_market(ticker: str) -> str:
    upper = ticker.upper()
    for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH"):
        if upper.endswith(quote):
            return "crypto"
    return "unknown"


@dataclass(frozen=True)
class PromotionInputs:
    """Operator-supplied decision fields not carried by a TV alert."""

    thesis: str
    confidence_score: float = 0.75
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    invalidation_condition: str = "manual_invalidate"
    risk_assessment: str = "operator_review"
    position_size_rationale: str = "operator_default"
    max_loss_estimate_pct: float = 0.25
    venue: str = "paper"
    mode: str = "paper"


def promote_event(
    event: TradingViewSignalEvent,
    inputs: PromotionInputs,
    *,
    rsi_value: float | None = None,
    rsi_period: int = RSI_DEFAULT_PERIOD,
    now_iso: str | None = None,
) -> SignalCandidate:
    """Build a SignalCandidate from a TV event + operator inputs + optional RSI.

    Raises PromotionError if the event's action is unsupported (e.g. "close")
    or if no entry price is available.
    """
    direction = _TV_ACTION_TO_DIRECTION.get(event.action)
    if direction is None:
        raise PromotionError(
            f"action {event.action!r} cannot be promoted to a directional candidate"
        )
    if event.price is None:
        raise PromotionError("event has no price; promotion requires an entry price")
    if not (0.0 <= inputs.confidence_score <= 1.0):
        raise PromotionError("confidence_score must be in [0.0, 1.0]")

    supporting: list[str] = ["tradingview_alert_trigger"]
    data_sources: list[str] = ["tradingview_webhook"]
    confluence = 1
    if event.strategy:
        supporting.append(f"strategy:{event.strategy}")
    if event.note:
        supporting.append(f"note:{event.note[:120]}")
    if rsi_value is not None:
        supporting.append(f"rsi_{rsi_period}={rsi_value:.2f}")
        data_sources.append("binance_ohlcv_rsi")
        confluence += 1

    promoted_provenance = SignalProvenance(
        source="tradingview_webhook",
        version="tv-3.1",
        signal_path_id=event.provenance.signal_path_id,
    )

    return SignalCandidate(
        decision_id=_new_decision_id(),
        timestamp_utc=now_iso or _utc_now_iso(),
        symbol=event.ticker,
        market=_heuristic_market(event.ticker),
        venue=inputs.venue,
        mode=inputs.mode,
        direction=direction,
        thesis=inputs.thesis,
        supporting_factors=tuple(supporting),
        contradictory_factors=(),
        confidence_score=inputs.confidence_score,
        confluence_count=confluence,
        market_regime="unknown",
        volatility_state="normal",
        liquidity_state="adequate",
        entry_price=event.price,
        stop_loss_price=inputs.stop_loss_price,
        take_profit_price=inputs.take_profit_price,
        invalidation_condition=inputs.invalidation_condition,
        risk_assessment=inputs.risk_assessment,
        position_size_rationale=inputs.position_size_rationale,
        max_loss_estimate_pct=inputs.max_loss_estimate_pct,
        data_sources_used=tuple(data_sources),
        source_document_id=event.source_request_id,
        model_version="tv-3.1",
        prompt_version="none",
        approval_state=SignalState.APPROVED,  # promotion IS the approval
        execution_state=SignalState.PENDING,
        provenance=promoted_provenance,
    )


async def fetch_rsi_context(ticker: str, *, period: int = RSI_DEFAULT_PERIOD) -> float | None:
    """Fetch OHLCV from the Binance adapter (if enabled) and compute RSI.

    Returns None if BINANCE_ENABLED is false, the fetch fails, or there
    aren't enough candles. Never raises — fail-soft.
    """
    from app.core.settings import get_settings

    settings = get_settings()
    if not settings.binance.enabled:
        return None
    try:
        from app.market_data.binance_adapter import BinanceAdapter

        adapter = BinanceAdapter(
            base_url=settings.binance.base_url,
            timeout_seconds=settings.binance.timeout_seconds,
            max_retries=settings.binance.max_retries,
            freshness_threshold_seconds=settings.binance.freshness_threshold_seconds,
        )
        candles = await adapter.get_ohlcv(ticker, timeframe="1h", limit=period + 50)
    except Exception:
        return None
    if len(candles) < period + 1:
        return None
    closes = [c.close for c in candles]
    rsi_series = compute_rsi(closes, period=period)
    latest = rsi_series[-1]
    return latest if latest is not None else None


def fetch_rsi_context_sync(ticker: str, *, period: int = RSI_DEFAULT_PERIOD) -> float | None:
    """Sync wrapper for CLI contexts."""
    try:
        return asyncio.run(fetch_rsi_context(ticker, period=period))
    except RuntimeError:
        # already in a running loop (rare in CLI) — give up on enrichment
        return None
