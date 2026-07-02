"""Build a compact token-unlock events artifact from DefiLlama (free, no key).

Source: ``defillama-datasets.llama.fi/emissions/{slug}`` — the same dataset the
DefiLlama "Unlocks" page uses. Per protocol it exposes ``metadata.unlockEvents``:
discrete scheduled unlocks, each a ``timestamp`` plus ``cliffAllocations`` /
``linearAllocations`` (recipient, category, amount in tokens). Schedules are
PUBLIC IN ADVANCE, so conditioning a bar at time t on "tokens unlocking in the
next N days" is causal w.r.t. information (the schedule was known at t).

Caveat (documented): DefiLlama serves the CURRENT schedule; a token whose vesting
plan was later revised carries a mild look-ahead on revisions. Major tokens use
fixed cliff/linear schedules, so this is small for the v1 universe — flagged, not
ignored.

Output (artifacts/research/unlock_events.json):
    {"schema": 2, "generated_at": "<UTC ISO>",
     "tokens": {"APT": {"max_supply": <float|null>,
                         "events": [[event_ms, amount_tokens], ...]}, ...}}

``generated_at`` (schema 2) is the fetch time, so a consumer can tell HOW OLD the
calendar is and flag a silently-dead refresh as stale instead of showing months-
old data as if fresh. Older schema-1 artifacts (no ``generated_at``) are still
read; the consumer treats a missing timestamp as "unknown age" → stale.

Run: python scripts/build_unlock_events.py
"""

from __future__ import annotations

import argparse
import json
import logging
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Unlock tokens that also have a Binance perp + a meaningful vesting schedule.
# slug (DefiLlama) -> trading base symbol.
DEFAULT_UNIVERSE: dict[str, str] = {
    "aptos": "APT",
    "arbitrum": "ARB",
    "celestia": "TIA",
    "sei": "SEI",
    "dydx": "DYDX",
    "worldcoin": "WLD",
    "ethena": "ENA",
    "jupiter": "JUP",
    "arkham": "ARKM",
    "altlayer": "ALT",
    "layerzero": "ZRO",
    "ondo-finance": "ONDO",
}
_BASE = "https://defillama-datasets.llama.fi/emissions/"
DEFAULT_OUT = Path("artifacts/research/unlock_events.json")


def _fetch(slug: str, timeout: float) -> dict[str, Any] | None:
    """Fetch one protocol's emission doc; None on any failure (fail-soft)."""
    try:
        req = urllib.request.Request(_BASE + slug, headers={"User-Agent": "kai-research/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed https host
            doc = json.loads(resp.read().decode("utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception as exc:  # noqa: BLE001 — research preprocessor must not crash on one source
        logger.warning("fetch %s failed: %s", slug, exc)
        return None


def _event_amount(event: dict[str, Any]) -> float:
    """Total tokens entering at one unlock event (cliff + linear allocations)."""
    total = 0.0
    for key in ("cliffAllocations", "linearAllocations"):
        for alloc in event.get(key) or []:
            amt = alloc.get("amount")
            if isinstance(amt, (int, float)) and amt > 0:
                total += float(amt)
    return total


def build(universe: dict[str, str], out: Path, timeout: float) -> int:
    tokens: dict[str, dict[str, Any]] = {}
    for slug, symbol in universe.items():
        doc = _fetch(slug, timeout)
        if doc is None:
            continue
        md = doc.get("metadata") or {}
        raw_events = md.get("unlockEvents") or []
        events: list[list[float]] = []
        for ev in raw_events:
            ts = ev.get("timestamp")
            if not isinstance(ts, (int, float)) or ts <= 0:
                continue
            amt = _event_amount(ev)
            if amt > 0:
                events.append([int(ts) * 1000, amt])
        events.sort(key=lambda p: p[0])
        sm = doc.get("supplyMetrics") or {}
        max_supply = sm.get("maxSupply") if isinstance(sm, dict) else None
        max_supply = float(max_supply) if isinstance(max_supply, (int, float)) else None
        tokens[symbol] = {"max_supply": max_supply, "events": events}
        logger.info(
            "%-5s (%s): %d events, max_supply=%s",
            symbol,
            slug,
            len(events),
            f"{max_supply:.3e}" if max_supply else "?",
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()
    out.write_text(
        json.dumps({"schema": 2, "generated_at": generated_at, "tokens": tokens}),
        encoding="utf-8",
    )
    logger.info("wrote %s (%d tokens, generated_at=%s)", out, len(tokens), generated_at)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build token-unlock events artifact (DefiLlama).")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    return build(DEFAULT_UNIVERSE, Path(args.out), args.timeout)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
