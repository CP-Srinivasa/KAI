"""Keyword Engine — in-memory text matcher.

Loads keywords, watchlist entries, and entity aliases from monitor files.
Matches text against all three layers and returns structured KeywordHit results.

Match hierarchy (highest to lowest precedence in canonical resolution):
  1. Watchlist entry (symbol/name/alias) → symbol as canonical
  2. Entity alias → canonical person/org name
  3. Plain keyword → keyword as-is

Usage:
    engine = KeywordEngine.from_monitor_dir("monitor")
    hits = engine.match("Bitcoin ETF approved — BTC surges past ATH")
    tickers = engine.match_tickers("BTC and ETH rally")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.analysis.keywords.aliases import EntityAlias, load_entity_aliases
from app.analysis.keywords.watchlist import WatchlistEntry, load_watchlist


@dataclass(frozen=True)
class KeywordHit:
    canonical: str    # canonical name/symbol (BTC, not bitcoin)
    category: str     # "crypto" | "equity" | "etf" | "macro" | "keyword" | person-category
    occurrences: int  # count of matches in text


class KeywordEngine:
    """Match text against keywords, watchlist, and entity aliases.

    Thread-safe after construction (read-only after __init__).
    """

    def __init__(
        self,
        keywords: frozenset[str],
        watchlist_entries: list[WatchlistEntry],
        entity_aliases: list[EntityAlias],
    ) -> None:
        self._keywords = keywords
        self._watchlist = watchlist_entries
        self._entity_aliases = entity_aliases
        # term (lowercased) → (canonical, category)
        self._index: dict[str, tuple[str, str]] = {}
        self._build_index()

    def _build_index(self) -> None:
        # Keywords last so watchlist/entity entries can override
        for kw in self._keywords:
            self._index[kw.lower()] = (kw, "keyword")

        for entity in self._entity_aliases:
            cat = entity.category
            self._index[entity.canonical.lower()] = (entity.canonical, cat)
            for alias in entity.aliases:
                self._index[alias.lower()] = (entity.canonical, cat)

        # Watchlist has highest priority — applied last to override
        for entry in self._watchlist:
            cat = entry.category
            self._index[entry.symbol.lower()] = (entry.symbol, cat)
            self._index[entry.name.lower()] = (entry.symbol, cat)
            for alias in entry.aliases:
                self._index[alias.lower()] = (entry.symbol, cat)

    def match(self, text: str) -> list[KeywordHit]:
        """Return all keyword/entity hits in text. Order: most occurrences first."""
        counts: dict[str, tuple[str, str, int]] = {}
        tokens = _tokenize(text)
        for token in tokens:
            entry = self._index.get(token)
            if entry:
                canonical, category = entry
                existing = counts.get(canonical)
                if existing:
                    counts[canonical] = (canonical, category, existing[2] + 1)
                else:
                    counts[canonical] = (canonical, category, 1)
        return sorted(
            [KeywordHit(canonical=c, category=cat, occurrences=n) for c, cat, n in counts.values()],
            key=lambda h: h.occurrences,
            reverse=True,
        )

    def match_tickers(self, text: str) -> list[str]:
        """Return matched ticker symbols (crypto/equity/etf only)."""
        return [
            h.canonical
            for h in self.match(text)
            if h.category in ("crypto", "equity", "etf")
        ]

    def match_entities(self, text: str) -> list[KeywordHit]:
        """Return only person/org entity hits."""
        return [
            h
            for h in self.match(text)
            if h.category not in ("crypto", "equity", "etf", "macro", "keyword")
        ]

    @classmethod
    def from_monitor_dir(cls, path: str | Path) -> KeywordEngine:
        """Construct engine from a monitor directory.

        Expects:
            {path}/keywords.txt
            {path}/watchlists.yml
            {path}/entity_aliases.yml
        Missing files are silently ignored.
        """
        path = Path(path)
        keywords = _load_keywords(path / "keywords.txt")
        watchlist = load_watchlist(path / "watchlists.yml")
        entity_aliases = load_entity_aliases(path / "entity_aliases.yml")
        return cls(
            keywords=frozenset(keywords),
            watchlist_entries=watchlist,
            entity_aliases=entity_aliases,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[^\w$€£₿@#]")


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens, stripping punctuation."""
    return [t for t in _TOKEN_RE.sub(" ", text.lower()).split() if t]


def _load_keywords(path: Path) -> list[str]:
    """Load keywords.txt — one term per line, # = comment, empty lines ignored."""
    if not path.exists():
        return []
    keywords: list[str] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                keywords.append(line)
    return keywords
