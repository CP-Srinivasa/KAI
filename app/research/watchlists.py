"""Watchlist Manager for Research.

Aggregates the core watchlists.yml configuration by tags, essentially transforming
tags into callable watchlists (e.g. watchlist 'defi' returns ['ETH', 'BNB', ...]).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from app.analysis.keywords.watchlist import WatchlistEntry, load_watchlist


class WatchlistRegistry:
    """Manages programmatic access to tagged watchlists from the central YAML structure."""

    def __init__(self, entries: list[WatchlistEntry]) -> None:
        self._entries = entries
        self._tag_to_symbols: dict[str, set[str]] = self._build_index()

    @classmethod
    def from_monitor_dir(cls, monitor_dir: Path | str) -> WatchlistRegistry:
        """Initialize from the monitor/watchlists.yml file."""
        monitor_path = Path(monitor_dir)
        entries = load_watchlist(monitor_path / "watchlists.yml")
        return cls(entries)

    def _build_index(self) -> dict[str, set[str]]:
        index: dict[str, set[str]] = {}
        for entry in self._entries:
            for tag in entry.tags:
                tag_lower = tag.lower()
                if tag_lower not in index:
                    index[tag_lower] = set()
                # Store symbol identically to how it's matched/searched
                index[tag_lower].add(entry.symbol)
        return index

    def get_watchlist(self, tag: str) -> list[str]:
        """Return a sorted list of symbols belonging to a specific tag/watchlist."""
        return sorted(self._tag_to_symbols.get(tag.lower(), set()))

    def get_all_watchlists(self) -> Mapping[str, list[str]]:
        """Return a dictionary of all available watchlists and their corresponding symbols."""
        return {tag: sorted(symbols) for tag, symbols in self._tag_to_symbols.items()}

    def get_symbols_for_category(self, category: str) -> list[str]:
        """Return all symbols belonging to a specific top-level category (crypto, equity, etc)."""
        cat_lower = category.lower()
        return sorted([e.symbol for e in self._entries if e.category.lower() == cat_lower])
