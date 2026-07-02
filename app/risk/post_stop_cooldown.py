"""NEO-V2: per-symbol post-stop cooldown gate (read-only).

Root-cause (NEO-F-202): the loop re-enters the same symbol minutes after a
stop-out, each round-trip bleeding ~1.2% in fees. There is no new persistence
here — the authoritative "last stop time per symbol" already exists in the
paper-execution audit JSONL as `position_closed` events with `reason=stop`
(see app/execution/paper_engine.py: close_position -> _append_audit).

Design choices:
- Read-only scan of the existing audit file; no schema migration, no extra state.
- Fail-OPEN on any read problem (missing file, malformed lines, missing/invalid
  timestamps): a transient read failure must never deadlock the loop. The cost
  is a temporarily missing guardrail, which is strictly less bad than blocking
  all trading. This is an intentional, documented trade-off.
- `cooldown_minutes <= 0` disables the gate entirely (backward-compatible).
- Strict `<` boundary: a stop exactly `cooldown_minutes` ago has elapsed.

Performance note: the audit file grows unbounded; for now we scan it linearly
per call. At current volumes (~10^2-10^3 lines/day) this is negligible relative
to the per-cycle market-data fetch. If the file grows to many MB this should be
revisited (tail-read or a small in-memory last-stop cache). Flagged as an open
risk, not silently assumed away.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_STOP_REASONS = {"stop", "sl", "stop_loss"}


def last_stop_timestamp(
    symbol: str,
    *,
    audit_path: Path,
) -> datetime | None:
    """Return the most-recent `position_closed reason=stop` timestamp for symbol.

    Returns None if no such event exists or the file is unreadable. Never raises.
    """
    if not audit_path.exists():
        return None

    latest: datetime | None = None
    try:
        with audit_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("event_type") != "position_closed":
                    continue
                if event.get("symbol") != symbol:
                    continue
                if str(event.get("reason", "")).lower() not in _STOP_REASONS:
                    continue
                raw_ts = event.get("timestamp_utc")
                if not raw_ts:
                    continue
                try:
                    ts = datetime.fromisoformat(str(raw_ts))
                except (ValueError, TypeError):
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if latest is None or ts > latest:
                    latest = ts
    except OSError as exc:
        logger.warning("[cooldown] cannot read audit %s: %s; failing open", audit_path, exc)
        return None

    return latest


def is_symbol_in_post_stop_cooldown(
    symbol: str,
    *,
    cooldown_minutes: int,
    audit_path: Path,
    now: datetime | None = None,
) -> bool:
    """True iff `symbol` was stopped out less than `cooldown_minutes` ago.

    cooldown_minutes <= 0 disables the gate. Fail-open: any read problem yields
    False (not in cooldown).
    """
    if cooldown_minutes <= 0:
        return False

    last_stop = last_stop_timestamp(symbol, audit_path=audit_path)
    if last_stop is None:
        return False

    current = now or datetime.now(UTC)
    elapsed_min = (current - last_stop).total_seconds() / 60.0
    return elapsed_min < cooldown_minutes
