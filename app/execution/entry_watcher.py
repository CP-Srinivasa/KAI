"""Deterministic EntryRangeWatcher — periodic price evaluation against
NormalizedTradeSignal entries with plausibility-filter, stale-data gate,
and TTL-awareness. Issues WAITING_FOR_ENTRY → ENTRY_TRIGGERED transitions
when entry-conditions hit.

Spec: docs/architecture/signal_to_execution_gap_analysis_20260510.md
      Aufgabenpaket 4 (Operator-Auftrag 2026-05-10).

Why
---
The existing bridge-tick (``envelope_to_paper_bridge.run_tick``) is
event-driven by an external scheduler. If a market crosses a tight
entry-range for a few seconds and the next scheduler tick is minutes
later, the signal misses the fill window.

This module is a deterministic, higher-frequency complementary layer
that polls per signal and issues lifecycle-transitions auditable per
correlation_id. It does NOT place orders — only emits the transition;
downstream PaperEngine / LiveEngine consume.

Contract
--------
1. **Pure observation.** NEVER places orders. Only evaluates and emits
   transition records.
2. **Plausibility-Pflicht.** Reject ticks deviating >
   ``plausibility_max_deviation_pct`` from a rolling median of the last
   ``plausibility_window_size`` ticks. First few ticks (window not full)
   are always plausible — bootstrap-tolerance.
3. **Stale-data fail-closed.** If ``quote_age_seconds`` >
   ``market_data_max_staleness_seconds`` → log + skip, no transition.
4. **Entry-type aware.** Symmetric for LONG/SHORT:
   - ``range``:   hit when ``entry_min <= price <= entry_max``
   - ``limit``:   LONG hit when ``price <= entry_value``;
                  SHORT hit when ``price >= entry_value``
   - ``trigger``: LONG hit when ``price >= entry_value`` (stop-buy);
                  SHORT hit when ``price <= entry_value`` (stop-sell)
   - ``market``:  always hit
5. **TTL-aware.** Caller signals ``ttl_expired`` → watcher emits
   ``EXPIRED`` transition with audit-reason.
6. **Idempotent.** Calling ``step()`` on a non-WAITING_FOR_ENTRY signal
   is a HOLD no-op.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from statistics import median

from app.execution.normalized_signal import (
    NormalizedTradeSignal,
    SignalStatus,
)


# ─── Configuration ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EntryWatcherConfig:
    """Tunables for the watcher loop. Defaults are conservative."""

    poll_interval_seconds: float = 5.0
    plausibility_max_deviation_pct: float = 5.0
    plausibility_window_size: int = 5
    market_data_max_staleness_seconds: float = 30.0


# ─── Decision Vocabulary ─────────────────────────────────────────────────────


class WatcherDecision(StrEnum):
    """Outcome of a single tick evaluation."""

    HOLD = "HOLD"  # keep waiting, no transition
    TRIGGER_ENTRY = "TRIGGER_ENTRY"  # WAITING_FOR_ENTRY → ENTRY_TRIGGERED
    REJECT_TICK_PLAUSIBILITY = "REJECT_TICK_PLAUSIBILITY"  # bad tick, ignore
    SKIP_STALE_DATA = "SKIP_STALE_DATA"  # quote too old, fail-closed
    EXPIRE_TTL = "EXPIRE_TTL"  # WAITING_FOR_ENTRY → EXPIRED


@dataclass(frozen=True)
class TickEvaluation:
    """Audit-grade outcome of one tick."""

    decision: WatcherDecision
    reason: str
    price_evaluated: float | None = None
    rolling_median: float | None = None
    deviation_pct: float | None = None


# ─── Plausibility-Filter ─────────────────────────────────────────────────────


class TickPlausibility:
    """Rolling-window plausibility check — guards against single-tick
    corruption (exchange feed glitches, $0 ticks, factor-of-1000 jumps).

    Bootstrap-tolerant: the first ``window_size`` ticks always pass since
    we have no rolling reference. After that, every tick is checked
    against the median of the last ``window_size`` accepted ticks.
    """

    def __init__(
        self,
        *,
        max_deviation_pct: float = 5.0,
        window_size: int = 5,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        if max_deviation_pct <= 0:
            raise ValueError("max_deviation_pct must be > 0")
        self._max_deviation_pct = max_deviation_pct
        self._window_size = window_size
        self._rolling: deque[float] = deque(maxlen=window_size)

    @property
    def window_filled(self) -> bool:
        return len(self._rolling) >= self._window_size

    def check(self, price: float) -> tuple[bool, float | None, float | None]:
        """Returns ``(is_plausible, rolling_median, deviation_pct)``.

        - Negative or zero price → always implausible.
        - Bootstrap (window not yet filled) → always plausible, no median yet.
        - Filled window → compare to median, reject if > max_deviation_pct.
        """
        if price <= 0:
            return False, None, None
        if not self.window_filled:
            return True, None, None
        med = median(self._rolling)
        if med <= 0:
            return False, med, None
        dev_pct = abs(price - med) / med * 100.0
        return dev_pct <= self._max_deviation_pct, med, dev_pct

    def record(self, price: float) -> None:
        """Add an accepted tick to the rolling window. Implausible ticks
        should NOT be recorded — they would poison the median."""
        if price > 0:
            self._rolling.append(price)


# ─── Pure Entry-Condition Evaluator ──────────────────────────────────────────


def is_entry_condition_met(
    signal: NormalizedTradeSignal,
    *,
    current_price: float,
) -> bool:
    """Pure entry-condition predicate. No side-effects.

    Mapping:
        market                 → True (always hit)
        range/long+short       → entry_min <= price <= entry_max
        limit/long             → price <= entry_value
        limit/short            → price >= entry_value
        trigger/long           → price >= entry_value (stop-buy)
        trigger/short          → price <= entry_value (stop-sell)
    """
    if current_price <= 0:
        return False

    if signal.entry_type == "market":
        return True

    if signal.entry_type == "range":
        if signal.entry_min is None or signal.entry_max is None:
            return False
        if signal.entry_min <= 0 or signal.entry_max <= signal.entry_min:
            return False
        return signal.entry_min <= current_price <= signal.entry_max

    if signal.entry_type == "limit":
        if signal.entry_value is None or signal.entry_value <= 0:
            return False
        if signal.direction == "long":
            return current_price <= signal.entry_value
        return current_price >= signal.entry_value

    if signal.entry_type == "trigger":
        if signal.entry_value is None or signal.entry_value <= 0:
            return False
        if signal.direction == "long":
            return current_price >= signal.entry_value
        return current_price <= signal.entry_value

    return False


# ─── Tick-Evaluator ──────────────────────────────────────────────────────────


def evaluate_tick(
    signal: NormalizedTradeSignal,
    *,
    current_price: float,
    quote_age_seconds: float,
    plausibility: TickPlausibility,
    config: EntryWatcherConfig | None = None,
    ttl_expired: bool = False,
) -> TickEvaluation:
    """Pure decision function for one tick.

    Order of checks (fail-closed cascade):
    1. Signal must be in ``WAITING_FOR_ENTRY`` (else HOLD)
    2. TTL expired → ``EXPIRE_TTL``
    3. Quote stale → ``SKIP_STALE_DATA``
    4. Tick implausible → ``REJECT_TICK_PLAUSIBILITY`` (no record!)
    5. Entry condition hit → ``TRIGGER_ENTRY``
    6. Else → ``HOLD``

    Side-effect: only-on-plausible-non-trigger ticks → ``plausibility.record(price)``.
    Implausible ticks must not poison the rolling median.
    """
    cfg = config or EntryWatcherConfig()

    if signal.status != SignalStatus.WAITING_FOR_ENTRY:
        return TickEvaluation(
            decision=WatcherDecision.HOLD,
            reason=f"signal_not_waiting:{signal.status.value}",
        )

    if ttl_expired:
        return TickEvaluation(
            decision=WatcherDecision.EXPIRE_TTL,
            reason="ttl_exceeded",
            price_evaluated=current_price,
        )

    if quote_age_seconds > cfg.market_data_max_staleness_seconds:
        return TickEvaluation(
            decision=WatcherDecision.SKIP_STALE_DATA,
            reason=(
                f"quote_age_{quote_age_seconds:.1f}s_exceeds_threshold_"
                f"{cfg.market_data_max_staleness_seconds:.1f}s"
            ),
            price_evaluated=current_price,
        )

    plausible, rolling_med, dev_pct = plausibility.check(current_price)
    if not plausible:
        return TickEvaluation(
            decision=WatcherDecision.REJECT_TICK_PLAUSIBILITY,
            reason=(
                f"tick_deviation_{dev_pct:.2f}pct_exceeds_"
                f"{cfg.plausibility_max_deviation_pct:.1f}pct"
                if dev_pct is not None
                else f"invalid_price:{current_price}"
            ),
            price_evaluated=current_price,
            rolling_median=rolling_med,
            deviation_pct=dev_pct,
        )

    # Tick is plausible — record it (poison-free).
    plausibility.record(current_price)

    if is_entry_condition_met(signal, current_price=current_price):
        return TickEvaluation(
            decision=WatcherDecision.TRIGGER_ENTRY,
            reason=_describe_entry_hit(signal, current_price),
            price_evaluated=current_price,
            rolling_median=rolling_med,
        )

    return TickEvaluation(
        decision=WatcherDecision.HOLD,
        reason=_describe_entry_miss(signal, current_price),
        price_evaluated=current_price,
        rolling_median=rolling_med,
    )


# ─── Watcher (per-signal stateful wrapper) ───────────────────────────────────


class EntryRangeWatcher:
    """Per-signal watcher state. One instance per correlation_id, lives as
    long as the signal is in ``WAITING_FOR_ENTRY``.

    Usage::

        watcher = EntryRangeWatcher(signal)
        # Loop (driven by scheduler / asyncio task):
        evaluation, signal = watcher.step(
            current_price=65250.0,
            quote_age_seconds=2.0,
        )
        if evaluation.decision == WatcherDecision.TRIGGER_ENTRY:
            # Hand off to PaperEngine / LiveEngine
            ...
    """

    def __init__(
        self,
        signal: NormalizedTradeSignal,
        *,
        config: EntryWatcherConfig | None = None,
    ) -> None:
        self._signal = signal
        self._config = config or EntryWatcherConfig()
        self._plausibility = TickPlausibility(
            max_deviation_pct=self._config.plausibility_max_deviation_pct,
            window_size=self._config.plausibility_window_size,
        )

    @property
    def signal(self) -> NormalizedTradeSignal:
        return self._signal

    @property
    def config(self) -> EntryWatcherConfig:
        return self._config

    def step(
        self,
        *,
        current_price: float,
        quote_age_seconds: float,
        ttl_expired: bool = False,
    ) -> tuple[TickEvaluation, NormalizedTradeSignal]:
        """Evaluate one tick. Returns ``(evaluation, signal)`` where the
        signal may be a new instance with an applied transition.

        - ``TRIGGER_ENTRY`` → signal transitions to ``ENTRY_TRIGGERED``
        - ``EXPIRE_TTL``    → signal transitions to ``EXPIRED``
        - All other decisions → signal returned unchanged
        """
        evaluation = evaluate_tick(
            self._signal,
            current_price=current_price,
            quote_age_seconds=quote_age_seconds,
            plausibility=self._plausibility,
            config=self._config,
            ttl_expired=ttl_expired,
        )

        if evaluation.decision == WatcherDecision.TRIGGER_ENTRY:
            self._signal = self._signal.transition_to(
                SignalStatus.ENTRY_TRIGGERED,
                actor="EntryRangeWatcher",
                reason=evaluation.reason,
            )
        elif evaluation.decision == WatcherDecision.EXPIRE_TTL:
            self._signal = self._signal.transition_to(
                SignalStatus.EXPIRED,
                actor="EntryRangeWatcher",
                reason=evaluation.reason,
            )

        return evaluation, self._signal


# ─── Helper: human-readable reason strings ───────────────────────────────────


def _describe_entry_hit(
    signal: NormalizedTradeSignal, price: float
) -> str:
    if signal.entry_type == "range":
        return (
            f"range_hit:{signal.entry_min}<={price}<={signal.entry_max}"
            f"_dir={signal.direction}"
        )
    if signal.entry_type == "limit":
        op = "<=" if signal.direction == "long" else ">="
        return f"limit_hit:{price}{op}{signal.entry_value}_dir={signal.direction}"
    if signal.entry_type == "trigger":
        op = ">=" if signal.direction == "long" else "<="
        return f"trigger_hit:{price}{op}{signal.entry_value}_dir={signal.direction}"
    return f"market_entry:{price}"


def _describe_entry_miss(
    signal: NormalizedTradeSignal, price: float
) -> str:
    if signal.entry_type == "range":
        return (
            f"price_outside_range:{price}_not_in_"
            f"[{signal.entry_min},{signal.entry_max}]"
        )
    if signal.entry_type in {"limit", "trigger"}:
        return (
            f"price_{price}_not_yet_at_entry_{signal.entry_value}"
            f"_dir={signal.direction}"
        )
    return f"price_{price}_no_match"


__all__ = [
    "EntryRangeWatcher",
    "EntryWatcherConfig",
    "TickEvaluation",
    "TickPlausibility",
    "WatcherDecision",
    "evaluate_tick",
    "is_entry_condition_met",
]
