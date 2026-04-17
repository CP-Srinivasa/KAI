"""TV-4 bridge: consume promoted TradingView signals for the trading loop.

Reads `artifacts/tradingview_promoted_signals.jsonl`, converts entries with
`execution_state=pending` into `SignalCandidate` objects, and marks them as
consumed after successful processing.

Fail-closed: malformed entries are skipped and logged, never silently promoted.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.signals.models import (
    SignalCandidate,
    SignalDirection,
    SignalProvenance,
    SignalState,
)

logger = logging.getLogger(__name__)

_PROMOTED_PATH = Path("artifacts/tradingview_promoted_signals.jsonl")
_CONSUMED_MARKER_PATH = Path("artifacts/tradingview_consumed_ids.json")


def _load_consumed_ids() -> set[str]:
    if not _CONSUMED_MARKER_PATH.exists():
        return set()
    try:
        data = json.loads(_CONSUMED_MARKER_PATH.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, OSError):
        return set()


def _save_consumed_ids(ids: set[str]) -> None:
    _CONSUMED_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONSUMED_MARKER_PATH.write_text(
        json.dumps(sorted(ids), ensure_ascii=False), encoding="utf-8",
    )


def _parse_promoted(raw: dict[str, Any]) -> SignalCandidate | None:
    """Convert a promoted-signals JSONL row into a SignalCandidate."""
    try:
        prov_raw = raw.get("provenance") or {}
        provenance = SignalProvenance(
            source=prov_raw.get("source", "tradingview_webhook"),
            version=prov_raw.get("version", "tv-4"),
            signal_path_id=prov_raw.get("signal_path_id"),
        )
        direction_str = raw.get("direction", "").lower()
        direction = SignalDirection.LONG if direction_str == "long" else SignalDirection.SHORT

        return SignalCandidate(
            decision_id=raw["decision_id"],
            timestamp_utc=raw.get("timestamp_utc", ""),
            symbol=raw["symbol"],
            market=raw.get("market", "crypto"),
            venue=raw.get("venue", "paper"),
            mode=raw.get("mode", "paper"),
            direction=direction,
            thesis=raw.get("thesis", "tradingview_promoted"),
            supporting_factors=tuple(raw.get("supporting_factors", [])),
            contradictory_factors=tuple(raw.get("contradictory_factors", [])),
            confidence_score=float(raw.get("confidence_score", 0.5)),
            confluence_count=int(raw.get("confluence_count", 1)),
            market_regime=raw.get("market_regime", "unknown"),
            volatility_state=raw.get("volatility_state", "normal"),
            liquidity_state=raw.get("liquidity_state", "adequate"),
            entry_price=float(raw["entry_price"]),
            stop_loss_price=(
                float(raw["stop_loss_price"]) if raw.get("stop_loss_price") else None
            ),
            take_profit_price=(
                float(raw["take_profit_price"]) if raw.get("take_profit_price") else None
            ),
            invalidation_condition=raw.get("invalidation_condition", "manual_invalidate"),
            risk_assessment=raw.get("risk_assessment", "operator_review"),
            position_size_rationale=raw.get("position_size_rationale", "operator_default"),
            max_loss_estimate_pct=float(raw.get("max_loss_estimate_pct", 0.25)),
            data_sources_used=tuple(raw.get("data_sources_used", ["tradingview_webhook"])),
            source_document_id=raw.get("source_document_id", ""),
            model_version=raw.get("model_version", "tv-4"),
            prompt_version=raw.get("prompt_version", "none"),
            approval_state=SignalState.APPROVED,
            execution_state=SignalState.PENDING,
            provenance=provenance,
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("tv_consumer: skip malformed promoted signal: %s", exc)
        return None


def load_pending_promoted(
    promoted_path: Path | None = None,
) -> list[SignalCandidate]:
    """Return promoted TV signals that haven't been consumed yet."""
    path = promoted_path or _PROMOTED_PATH
    if not path.exists():
        return []

    consumed = _load_consumed_ids()
    candidates: list[SignalCandidate] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue

        decision_id = raw.get("decision_id", "")
        if decision_id in consumed:
            continue
        if raw.get("execution_state") not in ("pending", "PENDING", None):
            continue

        candidate = _parse_promoted(raw)
        if candidate is not None:
            candidates.append(candidate)

    return candidates


def mark_consumed(decision_id: str) -> None:
    """Mark a promoted signal as consumed (idempotent)."""
    consumed = _load_consumed_ids()
    consumed.add(decision_id)
    _save_consumed_ids(consumed)
    logger.info("tv_consumer: marked %s as consumed", decision_id)
