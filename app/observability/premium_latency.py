"""Premium-Pipeline latency stats (informational) — auto-escalation trigger RETIRED.

2026-06-24 forensic (kai_premium_pipeline_backlog_20260514): the old
``p95(receive→fill) > 20min`` auto-trigger was a daily FALSE alarm. The reasons,
each verified against the live ``bridge_pending_orders.jsonl``:

- **receive→fill latency is price-wait, not a pipeline fault.** Premium/webhook
  signals are LIMIT orders. The entry-watch loop re-checks every pending order
  every ~5s (``operator_entry_watch`` ``poll_interval_seconds=5``) — there is NO
  processing/cron gap. 1997/2000 recent bridge events are ``pending /
  price_outside_tolerance``: the order sits until the market price drifts into the
  entry band (p95 = hours) or the TTL expires (~50%). That is correct limit-order
  behaviour. The originally-planned P2 #11 fix (event-driven inotify) would not
  change it — there is no tick-gap to close.

- **a "pipeline pickup" metric (origin→first-bridge-touch) is not reliably
  computable here.** It is contaminated by backlog sweeps (single ``expired``
  records, origin days old) that evade every filter, and the engaged-signal
  sample is too small (~17/week) for a percentile trigger — one outlier drives
  p95 to hours. Three filter attempts all leaked; see the forensic memory.

Real pipeline OUTAGES (the 2026-05 failure mode: bridge units down) are caught by
liveness — ``kai-premium-healthcheck`` (services + paper-tick) — not by latency.
So this module now produces an INFORMATIONAL digest only; it never auto-escalates.
``trigger_fired`` is retained (always False) for report/digest backward-compat.

``origin_envelope_timestamp`` = approval re-emit time (envelope handed to bridge);
worker→envelope latency is sub-second so this is a faithful "received" anchor.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_HOURS = 168  # 7 days

# An "expired" record only reflects pipeline fill performance when the signal
# entered fresh and waited out its TTL — i.e. age(origin→expiry) ≈ ttl_hours.
# A signal that was already far older than its TTL when first ingested expires
# on first contact and says nothing about whether the pipeline can fill: it was
# never fillable. The 2026-06-11 backlog sweep (57 signals, median age ~31 days,
# up to ~54 days) inflated expired_pct to 90.6% this way. We classify any expiry
# whose origin→expiry age exceeds ``ttl_hours * STALE_ON_ARRIVAL_TTL_FACTOR`` as
# stale-on-arrival and exclude it from ``expired_pct`` (counting it separately so
# it stays visible, never silently dropped). Factor 2.0 is deliberately generous
# — a fresh signal expires at age≈ttl, so only clearly-stale backlog (>2× ttl)
# is excluded.
STALE_ON_ARRIVAL_TTL_FACTOR = 2.0

# Why the auto-trigger is retired rather than re-tuned (see module docstring).
_TRIGGER_RETIRED_REASON = (
    "auto-trigger retired 2026-06-24: receive→fill is limit-order price-wait, not a "
    "pipeline fault; real outages are caught by kai-premium-healthcheck liveness"
)

_BRIDGE_LOG = Path("artifacts/bridge_pending_orders.jsonl")
_BASELINE_PATH = Path("artifacts/premium_latency_audit_baseline.json")


@dataclass(frozen=True)
class LatencyStats:
    sample_size: int
    expired_count: int
    expired_pct: float
    stale_expired_count: int  # expiries excluded from expired_pct (stale-on-arrival)
    p50_seconds: float | None
    p95_seconds: float | None
    p99_seconds: float | None
    max_seconds: float | None
    lookback_hours: int
    trigger_fired: bool  # retired — always False (kept for report/digest compat)
    trigger_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile. Returns None for empty input."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def _get_or_init_baseline(path: Path, now: datetime) -> datetime:
    """Return the audit baseline timestamp; create it if missing.

    The baseline cuts off pre-baseline records so historical pre-fix outliers do
    not pollute the (informational) distribution. Idempotent: a present file is
    honoured, never overwritten — the operator may delete it to reset the window.
    """
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("baseline_at")
            if isinstance(raw, str):
                ts = datetime.fromisoformat(raw)
                return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("[latency] baseline read failed (%s) — re-initialising", exc)
    payload = {
        "baseline_at": now.isoformat(),
        "rationale": (
            "Audit baseline (premium-latency digest). Samples from before this "
            "timestamp are not counted. Operator may delete this file to reset "
            "the window; the next audit run will create a fresh baseline."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return now


def _parse_iso(ts_raw: object) -> datetime | None:
    if not isinstance(ts_raw, str):
        return None
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def compute_latency_stats(
    audit_path: Path | None = None,
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    now: datetime | None = None,
    baseline_path: Path | None = None,
) -> LatencyStats:
    """Sweep the bridge-audit log and compute the receive→fill distribution.

    INFORMATIONAL ONLY — ``trigger_fired`` is always False (auto-escalation
    retired, see module docstring). Tolerant against malformed JSON lines +
    missing fields; a missing audit log returns a zero-sample LatencyStats.

    The effective lookback window is ``max(now - lookback_hours, baseline)``.
    """
    path = audit_path or _BRIDGE_LOG
    current = now or datetime.now(UTC)
    lookback_cutoff = current - timedelta(hours=lookback_hours)
    baseline = _get_or_init_baseline(baseline_path or _BASELINE_PATH, current)
    cutoff = max(lookback_cutoff, baseline)

    latencies: list[float] = []
    expired_count = 0
    stale_expired_count = 0

    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logger.warning("[latency] read failed: %s", exc)
            lines = []
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_iso(rec.get("timestamp_utc"))
            if ts is None or ts < cutoff:
                continue
            stage = rec.get("stage")
            if stage == "expired":
                # Stale-on-arrival expiries (origin→expiry age >> ttl) reflect
                # backlog/replay of long-dead signals, not pipeline fill ability.
                # Exclude from expired_pct but count separately (never hidden).
                # When origin or ttl is missing we cannot classify → count genuine.
                origin_ts = _parse_iso(rec.get("origin_envelope_timestamp"))
                ttl_hours_rec = rec.get("ttl_hours")
                if origin_ts is not None and isinstance(ttl_hours_rec, (int, float)):
                    age_hours = (ts - origin_ts).total_seconds() / 3600.0
                    if age_hours > ttl_hours_rec * STALE_ON_ARRIVAL_TTL_FACTOR:
                        stale_expired_count += 1
                        continue
                expired_count += 1
                continue
            if stage not in ("filled", "filled_duplicate_suppressed"):
                continue
            origin_ts = _parse_iso(rec.get("origin_envelope_timestamp"))
            if origin_ts is None:
                continue
            delta = (ts - origin_ts).total_seconds()
            # Defensive: negative or absurd values are dropped (clock skew etc.)
            if 0 <= delta < 7 * 24 * 3600:
                latencies.append(delta)

    latencies.sort()
    n = len(latencies)
    p50 = _percentile(latencies, 50) if n else None
    p95 = _percentile(latencies, 95) if n else None
    p99 = _percentile(latencies, 99) if n else None
    max_lat = latencies[-1] if n else None

    total_terminal = n + expired_count
    expired_pct = (expired_count / total_terminal * 100) if total_terminal else 0.0

    return LatencyStats(
        sample_size=n,
        expired_count=expired_count,
        expired_pct=round(expired_pct, 2),
        stale_expired_count=stale_expired_count,
        p50_seconds=round(p50, 2) if p50 is not None else None,
        p95_seconds=round(p95, 2) if p95 is not None else None,
        p99_seconds=round(p99, 2) if p99 is not None else None,
        max_seconds=round(max_lat, 2) if max_lat is not None else None,
        lookback_hours=lookback_hours,
        trigger_fired=False,  # retired
        trigger_reason=_TRIGGER_RETIRED_REASON,
    )


__all__ = [
    "DEFAULT_LOOKBACK_HOURS",
    "STALE_ON_ARRIVAL_TTL_FACTOR",
    "LatencyStats",
    "compute_latency_stats",
]
