"""High-frequency entry watcher for accepted operator signal envelopes.

The existing operator bridge remains the only component that submits paper
orders. This module watches pending entries at a shorter cadence and, when the
EntryRangeWatcher sees an entry hit, immediately invokes the bridge with that
same observed price. Idempotency and risk gates therefore stay in one place.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.settings import get_settings
from app.execution.entry_watcher import (
    EntryRangeWatcher,
    EntryWatcherConfig,
    WatcherDecision,
)
from app.execution.envelope_to_paper_bridge import (
    BridgeTickResult,
    _collect_pending_signals,
    _extract_source,
    _latest_bridge_stage_by_envelope,
    _parse_allowlist,
    _payload,
    _read_jsonl,
    _resolve_entry_price,
    _ttl_exceeded,
    run_tick,
)
from app.execution.normalized_signal import SignalStatus, new_signal
from app.market_data.models import MarketDataSnapshot
from app.market_data.service import get_market_data_snapshot

logger = logging.getLogger(__name__)

_ENVELOPE_LOG = Path("artifacts/telegram_message_envelope.jsonl")
_BRIDGE_LOG = Path("artifacts/bridge_pending_orders.jsonl")
_ENTRY_WATCH_AUDIT = Path("artifacts/entry_watcher_audit.jsonl")


@dataclass
class EntryWatchResult:
    enabled: bool
    scanned: int = 0
    watched: int = 0
    held: int = 0
    triggered: int = 0
    expired: int = 0
    stale_or_unavailable: int = 0
    implausible: int = 0
    skipped_source: int = 0
    bridge_filled: int = 0
    bridge_rejected: int = 0
    errors: list[str] = field(default_factory=list)

    def merge_bridge(self, bridge: BridgeTickResult) -> None:
        self.bridge_filled += bridge.filled
        self.bridge_rejected += (
            bridge.rejected_risk
            + bridge.rejected_size
            + bridge.rejected_incomplete
            + bridge.rejected_fill
            + bridge.rejected_position_exists
            + bridge.skipped_source
        )

    def add(self, other: EntryWatchResult) -> None:
        self.scanned += other.scanned
        self.watched += other.watched
        self.held += other.held
        self.triggered += other.triggered
        self.expired += other.expired
        self.stale_or_unavailable += other.stale_or_unavailable
        self.implausible += other.implausible
        self.skipped_source += other.skipped_source
        self.bridge_filled += other.bridge_filled
        self.bridge_rejected += other.bridge_rejected
        self.errors.extend(other.errors)

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "scanned": self.scanned,
            "watched": self.watched,
            "held": self.held,
            "triggered": self.triggered,
            "expired": self.expired,
            "stale_or_unavailable": self.stale_or_unavailable,
            "implausible": self.implausible,
            "skipped_source": self.skipped_source,
            "bridge_filled": self.bridge_filled,
            "bridge_rejected": self.bridge_rejected,
            "errors": list(self.errors),
        }


def _append_watch_audit(record: dict[str, object]) -> None:
    _ENTRY_WATCH_AUDIT.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _ENTRY_WATCH_AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.error("[entry-watch] audit write failed: %s", exc)


def _correlation_id(envelope: dict[str, object], envelope_id: str) -> str:
    return str(
        envelope.get("origin_envelope_id")
        or envelope.get("trace_id")
        or envelope.get("envelope_id")
        or envelope_id
    )


def _canonical_symbol(payload: dict[str, object]) -> str:
    display = payload.get("display_symbol")
    if isinstance(display, str) and display.strip():
        return display.strip().upper()
    raw = payload.get("symbol")
    return raw.strip().upper() if isinstance(raw, str) and raw.strip() else ""


def _entry_type(payload: dict[str, object]) -> str:
    raw = str(payload.get("entry_type") or "limit").lower()
    if raw == "range":
        return "range"
    if raw == "market":
        return "market"
    if raw in {"trigger", "above", "below", "breakout_above", "breakdown_below"}:
        return "trigger"
    return "limit"


def _float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _targets(payload: dict[str, object]) -> list[float]:
    raw = payload.get("targets")
    if not isinstance(raw, list):
        return []
    return [
        float(v) for v in raw if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
    ]


def _build_waiting_signal(
    *, envelope: dict[str, object], envelope_id: str, source: str
) -> Any | None:
    payload = _payload(envelope)
    direction = payload.get("direction")
    side = payload.get("side")
    stop_loss = _float(payload.get("stop_loss"))
    targets = _targets(payload)
    entry_type = _entry_type(payload)
    entry_value = _resolve_entry_price(payload) if entry_type != "range" else None
    entry_min = _float(payload.get("entry_min")) if entry_type == "range" else None
    entry_max = _float(payload.get("entry_max")) if entry_type == "range" else None
    if (
        direction not in {"long", "short"}
        or side not in {"buy", "sell"}
        or stop_loss is None
        or not targets
    ):
        return None
    try:
        signal = new_signal(
            correlation_id=_correlation_id(envelope, envelope_id),
            source=source,
            symbol=_canonical_symbol(payload),
            display_symbol=str(payload.get("display_symbol") or _canonical_symbol(payload)),
            side=side,
            direction=direction,
            entry_type=entry_type,  # type: ignore[arg-type]
            entry_value=entry_value,
            entry_min=entry_min,
            entry_max=entry_max,
            stop_loss=stop_loss,
            targets=tuple(targets),
            leverage=int(_float(payload.get("leverage")) or 1),
            risk_allocation_pct=_float(payload.get("margin_pct")),
        )
        signal = signal.transition_to(
            SignalStatus.VALIDATED,
            actor="OperatorEntryWatch",
            reason="bridge_pending_envelope",
        )
        return signal.transition_to(
            SignalStatus.WAITING_FOR_ENTRY,
            actor="OperatorEntryWatch",
            reason="watch_registered",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[entry-watch] unable to build signal envelope=%s: %s", envelope_id, exc)
        return None


async def _snapshot(symbol: str, config: EntryWatcherConfig) -> MarketDataSnapshot:
    settings = get_settings()
    provider = (
        settings.operator.signal_auto_run_provider
        if hasattr(settings.operator, "signal_auto_run_provider")
        else "fallback"
    )
    if not provider or provider == "coingecko":
        provider = "fallback"
    return await get_market_data_snapshot(
        symbol=symbol,
        provider=provider,
        freshness_threshold_seconds=config.market_data_max_staleness_seconds,
    )


async def run_watch_once(
    *,
    watchers: dict[str, EntryRangeWatcher] | None = None,
    config: EntryWatcherConfig | None = None,
) -> EntryWatchResult:
    settings = get_settings()
    if not settings.execution.operator_signal_bridge_enabled:
        return EntryWatchResult(enabled=False)

    cfg = config or EntryWatcherConfig()
    active = watchers if watchers is not None else {}
    result = EntryWatchResult(enabled=True)
    allowlist = _parse_allowlist(settings.execution.operator_signal_source_allowlist)
    ttl_hours = settings.execution.operator_signal_ttl_hours

    envelope_records = _read_jsonl(_ENVELOPE_LOG)
    bridge_records = _read_jsonl(_BRIDGE_LOG)
    pending = _collect_pending_signals(
        envelope_records,
        _latest_bridge_stage_by_envelope(bridge_records),
    )
    result.scanned = len(pending)

    for envelope in pending:
        envelope_id = str(envelope.get("envelope_id") or "")
        source = _extract_source(envelope)
        cid = _correlation_id(envelope, envelope_id)
        if source not in allowlist:
            result.skipped_source += 1
            continue

        signal = _build_waiting_signal(envelope=envelope, envelope_id=envelope_id, source=source)
        if signal is None:
            continue
        result.watched += 1
        watcher = active.get(cid)
        if watcher is None or watcher.signal.status != SignalStatus.WAITING_FOR_ENTRY:
            watcher = EntryRangeWatcher(signal, config=cfg)
            active[cid] = watcher

        ts_raw = envelope.get("timestamp_utc")
        ttl_expired = _ttl_exceeded(ts_raw if isinstance(ts_raw, str) else None, ttl_hours)
        snap = await _snapshot(signal.display_symbol, cfg)
        price = snap.price if snap.available and snap.price is not None else 0.0
        quote_age = snap.freshness_seconds
        if quote_age is None:
            quote_age = cfg.market_data_max_staleness_seconds + 1.0

        evaluation, new_signal_state = watcher.step(
            current_price=price,
            quote_age_seconds=quote_age,
            ttl_expired=ttl_expired,
        )
        _append_watch_audit(
            {
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "event": "entry_range_watcher",
                "envelope_id": envelope_id,
                "correlation_id": cid,
                "symbol": signal.display_symbol,
                "decision": evaluation.decision.value,
                "reason": evaluation.reason,
                "price": evaluation.price_evaluated,
                "quote_age_seconds": quote_age,
                "lifecycle_state": new_signal_state.status.value,
            }
        )

        if evaluation.decision == WatcherDecision.TRIGGER_ENTRY:
            result.triggered += 1
            trigger_price = price
            trigger_symbol = signal.display_symbol

            async def _trigger_price_provider(
                symbol: str,
                observed_price: float = trigger_price,
                observed_symbol: str = trigger_symbol,
            ) -> float | None:
                return observed_price if symbol == observed_symbol else None

            bridge = await run_tick(price_provider=_trigger_price_provider)
            result.merge_bridge(bridge)
            continue
        if evaluation.decision == WatcherDecision.EXPIRE_TTL:
            result.expired += 1
            bridge = await run_tick()
            result.merge_bridge(bridge)
            continue
        if evaluation.decision == WatcherDecision.REJECT_TICK_PLAUSIBILITY:
            result.implausible += 1
            continue
        if evaluation.decision == WatcherDecision.SKIP_STALE_DATA:
            result.stale_or_unavailable += 1
            continue
        result.held += 1

    return result


async def run_watch_loop(
    *,
    duration_seconds: float,
    poll_interval_seconds: float = 5.0,
    config: EntryWatcherConfig | None = None,
) -> EntryWatchResult:
    cfg = config or EntryWatcherConfig(poll_interval_seconds=poll_interval_seconds)
    watchers: dict[str, EntryRangeWatcher] = {}
    total = EntryWatchResult(enabled=True)
    deadline = time.monotonic() + max(0.0, duration_seconds)
    while True:
        tick = await run_watch_once(watchers=watchers, config=cfg)
        total.enabled = tick.enabled
        total.add(tick)
        if not tick.enabled or time.monotonic() >= deadline:
            return total
        await asyncio.sleep(max(0.1, poll_interval_seconds))


__all__ = [
    "EntryWatchResult",
    "run_watch_loop",
    "run_watch_once",
]
