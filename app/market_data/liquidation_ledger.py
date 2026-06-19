"""Append-only liquidation-event ledger (source-agnostic JSONL) — #316.

The Binance canary (and later CoinGlass) append normalized
:class:`LiquidationEvent` rows here; the dashboard/edge layer reads a recent
window back. Read path is fail-open: a malformed/partial trailing line (e.g. a
crash mid-write) is skipped, never raised, so a single bad row can't take down
the panel.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path

from app.market_data.liquidation_event import LiquidationEvent

DEFAULT_PATH = Path("artifacts/liquidation_events.jsonl")


def append_event(event: LiquidationEvent, path: Path = DEFAULT_PATH) -> None:
    """Append one event as a JSON line (creates parent dir on first write)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(event.model_dump_json() + "\n")


def load_events(
    path: Path = DEFAULT_PATH,
    *,
    since: datetime | None = None,
    max_lines: int = 50_000,
) -> list[LiquidationEvent]:
    """Load up to the last ``max_lines`` events, optionally filtered to
    ``event_time >= since``. Missing file → empty list; bad lines are skipped."""
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as fh:
            tail = deque(fh, maxlen=max_lines)
    except OSError:
        return []

    out: list[LiquidationEvent] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            event = LiquidationEvent.model_validate_json(line)
        except ValueError:
            continue  # fail-open: skip malformed rows, never raise
        if since is not None and event.event_time < since:
            continue
        out.append(event)
    return out
