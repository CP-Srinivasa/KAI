"""Watchlist loader — reads monitor/watchlists.yml into typed entries.

Format (watchlists.yml):
    crypto:
      - symbol: BTC
        name: Bitcoin
        aliases: [bitcoin, "₿", XBT]
        tags: [store_of_value, layer1]

    equities:
      - symbol: MSTR
        name: MicroStrategy
        aliases: [microstrategy]
        tags: [bitcoin_proxy]
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True)
class WatchlistEntry:
    symbol: str
    name: str
    aliases: frozenset[str]
    tags: tuple[str, ...]
    category: str  # "crypto" | "equity" | "etf" | "macro"


def load_watchlist(path: str | Path) -> list[WatchlistEntry]:
    """Parse watchlists.yml → list[WatchlistEntry]."""
    path = Path(path)
    if not path.exists():
        return []

    with path.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    entries: list[WatchlistEntry] = []
    category_map = {
        "crypto": "crypto",
        "equities": "equity",
        "etfs": "etf",
        "macro": "macro",
    }

    for section, category in category_map.items():
        for item in data.get(section) or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).strip()
            name = str(item.get("name", "")).strip()
            if not symbol:
                continue
            raw_aliases: list[str] = [str(a) for a in (item.get("aliases") or [])]
            tags: list[str] = [str(t) for t in (item.get("tags") or [])]
            entries.append(
                WatchlistEntry(
                    symbol=symbol,
                    name=name,
                    aliases=frozenset(a.lower() for a in raw_aliases),
                    tags=tuple(tags),
                    category=category,
                )
            )

    return entries
