"""Windowed liquidation metrics (pure, read-only) — #316 Data Foundation.

Aggregates a list of canonical :class:`LiquidationEvent` into the dashboard /
edge-measurement metrics the operator specified: per-window notional + counts,
long/short split, imbalance, largest event, per-asset bucket, feed gap/health.

Pure and deterministic — no I/O, no clock of its own (``now`` is injected) — so
it is fully unit-testable and never blocks a request. It computes; it does not
decide. Nothing here gates a trade.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from app.market_data.liquidation_event import LiquidationEvent

# Measurement windows (seconds).
_WINDOWS: dict[str, int] = {"1m": 60, "5m": 300, "15m": 900}
# Beyond this with no events the feed is "idle" (calm market OR feed down — the
# live consumer's heartbeat is the authoritative connectivity signal; from the
# ledger alone we only report the gap honestly).
_IDLE_AFTER_SECONDS = 900


@dataclass(frozen=True)
class LiquidationMetrics:
    generated_at: str
    total_events: int
    events_per_min: int
    window_events: dict[str, int] = field(default_factory=dict)
    notional_usd: dict[str, float] = field(default_factory=dict)
    long_notional_usd_15m: float = 0.0
    short_notional_usd_15m: float = 0.0
    imbalance_15m: float | None = None
    largest_event_usd_15m: float = 0.0
    asset_bucket_15m: dict[str, float] = field(default_factory=dict)
    exchange_count_15m: int = 0
    data_gap_seconds: float | None = None
    feed_health: str = "no_data"  # ok | idle | no_data
    is_snapshot_limited: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _empty(now: datetime) -> LiquidationMetrics:
    return LiquidationMetrics(
        generated_at=now.isoformat(),
        total_events=0,
        events_per_min=0,
        window_events=dict.fromkeys(_WINDOWS, 0),
        notional_usd=dict.fromkeys(_WINDOWS, 0.0),
        feed_health="no_data",
    )


def compute_liquidation_metrics(
    events: list[LiquidationEvent],
    now: datetime,
    *,
    idle_after_seconds: int = _IDLE_AFTER_SECONDS,
) -> LiquidationMetrics:
    """Aggregate ``events`` as of ``now`` (tz-aware UTC). Empty → no_data."""
    if not events:
        return _empty(now)

    window_events: dict[str, int] = {}
    notional_usd: dict[str, float] = {}
    for name, secs in _WINDOWS.items():
        cutoff = now - timedelta(seconds=secs)
        in_win = [e for e in events if e.event_time >= cutoff]
        window_events[name] = len(in_win)
        notional_usd[name] = round(sum(e.notional_usd for e in in_win), 2)

    cutoff_15m = now - timedelta(seconds=_WINDOWS["15m"])
    win15 = [e for e in events if e.event_time >= cutoff_15m]

    long_notional = sum(e.notional_usd for e in win15 if e.liquidated_side == "LONG")
    short_notional = sum(e.notional_usd for e in win15 if e.liquidated_side == "SHORT")
    denom = long_notional + short_notional
    imbalance = round((long_notional - short_notional) / denom, 4) if denom > 0 else None

    bucket: dict[str, float] = defaultdict(float)
    for e in win15:
        bucket[e.asset_id] += e.notional_usd
    asset_bucket = {
        k: round(v, 2) for k, v in sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)
    }

    last_event_time = max(e.event_time for e in events)
    data_gap = round((now - last_event_time).total_seconds(), 1)
    feed_health = "ok" if data_gap <= idle_after_seconds else "idle"

    return LiquidationMetrics(
        generated_at=now.isoformat(),
        total_events=len(events),
        events_per_min=window_events["1m"],
        window_events=window_events,
        notional_usd=notional_usd,
        long_notional_usd_15m=round(long_notional, 2),
        short_notional_usd_15m=round(short_notional, 2),
        imbalance_15m=imbalance,
        largest_event_usd_15m=round(max((e.notional_usd for e in win15), default=0.0), 2),
        asset_bucket_15m=asset_bucket,
        exchange_count_15m=len({e.exchange for e in win15}),
        data_gap_seconds=data_gap,
        feed_health=feed_health,
        is_snapshot_limited=any(e.is_snapshot_limited for e in win15),
    )
