"""
Watchlist Registry
==================
Loads and indexes structured watchlists from monitor/watchlists.yml.

Supports six categories:
  - crypto     — coins and tokens (symbol-based)
  - equities   — stocks (symbol-based)
  - etfs       — ETFs (symbol-based)
  - persons    — notable individuals (name-based)
  - topics     — themes like "DeFi", "Regulation" (name-based)
  - domains    — trusted news domains (domain-based)

Usage:
    registry = WatchlistRegistry.from_file("monitor/watchlists.yml")
    items = registry.find_by_text("The SEC approved the iShares Bitcoin ETF")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.enums import WatchlistCategory
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class WatchlistItem:
    """A single watchlist entry."""

    category: WatchlistCategory
    identifier: str            # Symbol (BTC, NVDA) or canonical name (Vitalik Buterin)
    display_name: str          # Human-readable name
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    credibility: float = 1.0   # Used for domain entries

    @property
    def all_names(self) -> list[str]:
        """All lowercase lookup strings: identifier + aliases."""
        names = [self.identifier.lower()]
        names += [a.lower() for a in self.aliases]
        return list(dict.fromkeys(names))  # deduplicate, preserve order

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "identifier": self.identifier,
            "display_name": self.display_name,
            "aliases": self.aliases,
            "tags": self.tags,
        }


@dataclass
class WatchlistMatch:
    """Result of a text lookup against the watchlist."""

    item: WatchlistItem
    matched_alias: str
    match_type: str = "exact"   # exact | partial | domain


class WatchlistRegistry:
    """
    In-memory watchlist registry with multi-category lookup.

    Indexing:
      - _alias_index: lowercase alias → WatchlistItem  (O(1) exact match)
      - _items_by_category: category → list of WatchlistItem

    Thread-safe for read-only use after construction.
    """

    def __init__(self) -> None:
        self._items: list[WatchlistItem] = []
        self._alias_index: dict[str, WatchlistItem] = {}
        self._items_by_category: dict[WatchlistCategory, list[WatchlistItem]] = {
            c: [] for c in WatchlistCategory
        }

    # ─── Construction ──────────────────────────────────────────────────────

    @classmethod
    def from_file(cls, path: str | Path) -> "WatchlistRegistry":
        """Load watchlists from a YAML file."""
        path = Path(path)
        registry = cls()
        if not path.exists():
            logger.warning("watchlist_file_not_found", path=str(path))
            return registry
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        registry._load_yaml(data)
        logger.info(
            "watchlist_loaded",
            path=str(path),
            total=len(registry._items),
        )
        return registry

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WatchlistRegistry":
        """Build registry from a raw parsed dict (useful in tests)."""
        registry = cls()
        registry._load_yaml(data)
        return registry

    def _load_yaml(self, data: dict[str, Any]) -> None:
        loaders = {
            "crypto": (WatchlistCategory.CRYPTO, self._load_symbol_entry),
            "equities": (WatchlistCategory.EQUITIES, self._load_symbol_entry),
            "etfs": (WatchlistCategory.ETFS, self._load_symbol_entry),
            "persons": (WatchlistCategory.PERSONS, self._load_name_entry),
            "topics": (WatchlistCategory.TOPICS, self._load_name_entry),
            "domains": (WatchlistCategory.DOMAINS, self._load_domain_entry),
        }
        for key, (category, loader) in loaders.items():
            for raw in data.get(key, []):
                item = loader(raw, category)
                self._register(item)

    def _load_symbol_entry(self, raw: dict, category: WatchlistCategory) -> WatchlistItem:
        return WatchlistItem(
            category=category,
            identifier=raw["symbol"].upper(),
            display_name=raw.get("name", raw["symbol"]),
            aliases=[str(a) for a in raw.get("aliases", [])],
            tags=raw.get("tags", []),
        )

    def _load_name_entry(self, raw: dict, category: WatchlistCategory) -> WatchlistItem:
        return WatchlistItem(
            category=category,
            identifier=raw["name"],
            display_name=raw["name"],
            aliases=[str(a) for a in raw.get("aliases", [])],
            tags=raw.get("tags", []),
        )

    def _load_domain_entry(self, raw: dict, category: WatchlistCategory) -> WatchlistItem:
        return WatchlistItem(
            category=category,
            identifier=raw["domain"],
            display_name=raw["domain"],
            aliases=[],
            tags=raw.get("tags", []),
            credibility=float(raw.get("credibility", 1.0)),
        )

    def _register(self, item: WatchlistItem) -> None:
        self._items.append(item)
        self._items_by_category[item.category].append(item)
        for name in item.all_names:
            if name not in self._alias_index:
                self._alias_index[name] = item

    # ─── Lookup API ────────────────────────────────────────────────────────

    def find_by_symbol(self, symbol: str) -> WatchlistItem | None:
        """Exact symbol lookup (case-insensitive)."""
        return self._alias_index.get(symbol.upper().lower())

    def find_by_name(self, name: str) -> WatchlistItem | None:
        """Exact name/alias lookup (case-insensitive)."""
        return self._alias_index.get(name.lower())

    def find_by_domain(self, domain: str) -> WatchlistItem | None:
        """Domain lookup — strips www. prefix."""
        clean = domain.lower().removeprefix("www.")
        return self._alias_index.get(clean)

    def find_by_text(self, text: str) -> list[WatchlistMatch]:
        """
        Scan text for watchlist hits using word-boundary matching.
        Returns all matches, deduplicated by item.
        """
        text_lower = text.lower()
        seen_ids: set[str] = set()
        matches: list[WatchlistMatch] = []

        for alias, item in self._alias_index.items():
            item_key = f"{item.category.value}:{item.identifier}"
            if item_key in seen_ids:
                continue
            # Word-boundary check to avoid BTC matching "BTCUSDT" mid-string
            pattern = r"(?<![a-z0-9_])" + re.escape(alias) + r"(?![a-z0-9_])"
            if re.search(pattern, text_lower):
                seen_ids.add(item_key)
                matches.append(WatchlistMatch(item=item, matched_alias=alias, match_type="exact"))

        return matches

    def get_by_category(self, category: WatchlistCategory) -> list[WatchlistItem]:
        return self._items_by_category.get(category, [])

    def all_symbols(self, *categories: WatchlistCategory) -> list[str]:
        """Return all symbols/identifiers for the given categories."""
        cats = categories or (WatchlistCategory.CRYPTO, WatchlistCategory.EQUITIES, WatchlistCategory.ETFS)
        return [item.identifier for cat in cats for item in self._items_by_category[cat]]

    @property
    def total(self) -> int:
        return len(self._items)

    def summary(self) -> dict[str, int]:
        return {cat.value: len(items) for cat, items in self._items_by_category.items()}
