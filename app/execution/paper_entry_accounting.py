"""Paper-entry accounting — opening-fill semantics + day-bounded counting.

Extracted from ``app/orchestrator/trading_loop.py`` (Sprint S7 god-file
ratchet, 2026-06-11): these helpers define what counts as a NEW
(risk-increasing) paper entry and how daily caps combine. They are consumed by
the trading loop's daily-entry cap AND the entry-policy route limiter — before
this module both carried their own copy of the opening-fill predicate, which
is exactly the drift the daily-cap/limiter pair must never have.

Pure functions over audit rows / a JSONL path: no engine state, no settings.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def is_opening_fill(record: dict[str, object]) -> bool:
    """True when an ``order_filled`` audit row represents a NEW entry (opening).

    An opening fill increases exposure: a long is opened by a ``buy`` with
    ``position_side="long"``; a short by a ``sell`` with ``position_side="short"``.
    The opposite side/position_side combinations are exits (TP/SL/manual close)
    and are deliberately NOT counted by entry caps/limits. Rows that predate
    the ``position_side`` audit field default to ``"long"`` (the engine
    default), matching their historical long-only behaviour.
    """
    if record.get("event_type") != "order_filled":
        return False
    side = str(record.get("side") or "").lower()
    position_side = str(record.get("position_side") or "long").lower()
    return (side == "buy" and position_side == "long") or (
        side == "sell" and position_side == "short"
    )


def count_paper_entries_today(audit_path: Path, *, now: datetime | None = None) -> int:
    """Count opening paper fills that settled today (UTC).

    Pure read of the paper-execution audit JSONL — no engine state mutation, no
    side effects beyond reading the file. Mirrors the spirit of the
    ``max_daily_loss`` day-bounded accounting: the window is the calendar UTC
    day of ``now``. A missing/unreadable audit file yields 0 (fail-open ONLY
    for the *count*; the caller decides the gate). Used by the
    ``max_daily_paper_entries`` cap so a re-activated paper-learning stream
    cannot open an unbounded number of positions per day.
    """
    now_utc = now or datetime.now(UTC)
    today_prefix = now_utc.date().isoformat()
    if not audit_path.exists():
        return 0
    count = 0
    try:
        with audit_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                ts = record.get("timestamp_utc")
                if not isinstance(ts, str) or not ts.startswith(today_prefix):
                    continue
                if is_opening_fill(record):
                    count += 1
    except OSError as exc:
        logger.warning("[paper-entry] count read failed: %s", exc)
        return 0
    return count


def effective_daily_paper_cap(global_cap: int, feeder_cap: int) -> int:
    """Combine two daily-entry caps to the stricter (smallest positive) bound.

    Each cap uses 0 == UNLIMITED for its own axis. The effective cap is the
    smallest positive of the two; if both are 0 the result is 0 (no cap). This
    keeps either knob a no-op when left at default and lets the feeder-specific
    cap tighten — never loosen — the global one.
    """
    positives = [c for c in (global_cap, feeder_cap) if c > 0]
    return min(positives) if positives else 0


__all__ = [
    "count_paper_entries_today",
    "effective_daily_paper_cap",
    "is_opening_fill",
]
