"""Read-only token-unlock calendar loader for the operator dashboard.

Single source of truth for the NEXT scheduled unlock per token. The per-event
parse is lifted to app level from ``scripts/unlock_pressure_research._load_events``
so the read-only dashboard (Phase 2) and a later, evidence-gated sizing overlay
(Phase 3) read ONE loader instead of two drifting copies. Forward-looking and
fail-soft: any malformed artifact/token degrades to an empty result, never raises.

CONTEXT, NOT A SIGNAL: token unlocks as a DIRECTIONAL signal are terminally
falsified (#487 beta-neutral pooled −111 bps; #482 0 survivors). This surfaces the
PUBLIC vesting schedule as a risk / expected-volatility *marker* for operator
awareness around a cliff — never a long/short call. Pure parsing, no network.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_EVENTS_PATH = Path("artifacts/research/unlock_events.json")
_MS_PER_DAY = 86_400_000


def _next_upcoming(symbol: str, info: dict[str, Any], now_ms: int) -> dict[str, Any] | None:
    """Earliest unlock strictly after ``now_ms`` for one token; None if none/invalid."""
    raw_events = info.get("events")
    if not isinstance(raw_events, list):
        return None
    upcoming: list[tuple[int, float]] = []
    for pair in raw_events:
        try:
            ms, amt = int(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if ms > now_ms:
            upcoming.append((ms, amt))
    if not upcoming:
        return None
    event_ms, amount_tokens = min(upcoming, key=lambda e: e[0])

    max_supply = info.get("max_supply")
    frac: float | None = None
    if isinstance(max_supply, (int, float)) and max_supply > 0.0:
        frac = amount_tokens / float(max_supply)

    return {
        "symbol": symbol,
        "event_ms": event_ms,
        "event_iso": datetime.fromtimestamp(event_ms / 1000, tz=UTC).isoformat(),
        "days_until": round((event_ms - now_ms) / _MS_PER_DAY, 2),
        "amount_tokens": amount_tokens,
        "frac_of_max_supply": None if frac is None else round(frac, 6),
    }


def load_unlock_calendar(
    path: Path = DEFAULT_EVENTS_PATH, *, now_ms: int | None = None
) -> list[dict[str, Any]]:
    """Next upcoming unlock per token, soonest first. Fail-soft → [] on any error.

    Reads the ``build_unlock_events.py`` artifact (``{"schema", "generated_at"?,
    "tokens": {SYM: {"max_supply", "events": [[ms, amt], ...]}}}``). Tokens with no
    event after ``now_ms`` are omitted (the calendar shows only what is still
    ahead). ``frac_of_max_supply`` is None when max supply is unknown.
    """
    if now_ms is None:
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tokens = doc.get("tokens") if isinstance(doc, dict) else None
    if not isinstance(tokens, dict):
        return []

    out: list[dict[str, Any]] = []
    for symbol, info in tokens.items():
        if not isinstance(info, dict):
            continue
        entry = _next_upcoming(str(symbol), info, now_ms)
        if entry is not None:
            out.append(entry)
    out.sort(key=lambda e: e["event_ms"])
    return out


def read_generated_at(path: Path = DEFAULT_EVENTS_PATH) -> str | None:
    """Artifact build time (schema-2 ``generated_at``); None if absent/old/unreadable.

    A missing timestamp (schema-1 artifact, or a dead refresh that never wrote one)
    must read as "unknown age" so the consumer can flag the calendar STALE rather
    than implying freshness. Fail-soft, no raise.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    ts = doc.get("generated_at")
    return ts if isinstance(ts, str) and ts else None
