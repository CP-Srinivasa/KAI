"""Tests for app/trading/watchlists/watchlist.py"""
from __future__ import annotations

import pytest

from app.core.enums import WatchlistCategory
from app.trading.watchlists.watchlist import WatchlistItem, WatchlistRegistry


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

MINIMAL_DATA = {
    "crypto": [
        {"symbol": "BTC", "name": "Bitcoin", "aliases": ["bitcoin", "₿"], "tags": ["layer1", "major"]},
        {"symbol": "ETH", "name": "Ethereum", "aliases": ["ethereum", "ether"], "tags": ["layer1", "defi"]},
    ],
    "equities": [
        {"symbol": "MSTR", "name": "MicroStrategy", "aliases": ["microstrategy"], "tags": ["bitcoin_proxy"]},
    ],
    "etfs": [
        {"symbol": "IBIT", "name": "iShares Bitcoin Trust", "aliases": ["ibit", "blackrock bitcoin etf"], "tags": ["bitcoin_etf"]},
    ],
    "persons": [
        {"name": "Vitalik Buterin", "aliases": ["vitalik"], "tags": ["ethereum", "founder"]},
    ],
    "topics": [
        {"name": "Regulation", "aliases": ["regulation", "sec", "cftc"], "tags": ["risk", "legal"]},
    ],
    "domains": [
        {"domain": "coindesk.com", "credibility": 0.85, "tags": ["crypto_news"]},
    ],
}


@pytest.fixture
def registry() -> WatchlistRegistry:
    return WatchlistRegistry.from_dict(MINIMAL_DATA)


# ──────────────────────────────────────────────
# WatchlistItem
# ──────────────────────────────────────────────

class TestWatchlistItem:
    def test_all_names_includes_identifier_and_aliases(self) -> None:
        item = WatchlistItem(
            category=WatchlistCategory.CRYPTO,
            identifier="BTC",
            display_name="Bitcoin",
            aliases=["bitcoin", "₿"],
        )
        names = item.all_names
        assert "btc" in names
        assert "bitcoin" in names
        assert "₿" in names

    def test_all_names_deduplicates(self) -> None:
        item = WatchlistItem(
            category=WatchlistCategory.CRYPTO,
            identifier="BTC",
            display_name="Bitcoin",
            aliases=["btc", "bitcoin"],
        )
        assert len(item.all_names) == len(set(item.all_names))

    def test_to_dict_structure(self) -> None:
        item = WatchlistItem(
            category=WatchlistCategory.CRYPTO,
            identifier="ETH",
            display_name="Ethereum",
            tags=["layer1"],
        )
        d = item.to_dict()
        assert d["category"] == "crypto"
        assert d["identifier"] == "ETH"
        assert "tags" in d


# ──────────────────────────────────────────────
# WatchlistRegistry — construction
# ──────────────────────────────────────────────

class TestRegistryConstruction:
    def test_loads_all_categories(self, registry: WatchlistRegistry) -> None:
        summary = registry.summary()
        assert summary["crypto"] == 2
        assert summary["equities"] == 1
        assert summary["etfs"] == 1
        assert summary["persons"] == 1
        assert summary["topics"] == 1
        assert summary["domains"] == 1

    def test_total_count(self, registry: WatchlistRegistry) -> None:
        assert registry.total == 7

    def test_empty_registry(self) -> None:
        r = WatchlistRegistry.from_dict({})
        assert r.total == 0

    def test_missing_file_returns_empty(self) -> None:
        r = WatchlistRegistry.from_file("/nonexistent/path/watchlists.yml")
        assert r.total == 0


# ──────────────────────────────────────────────
# Lookup — by symbol / name / domain
# ──────────────────────────────────────────────

class TestLookup:
    def test_find_by_symbol_exact(self, registry: WatchlistRegistry) -> None:
        item = registry.find_by_symbol("BTC")
        assert item is not None
        assert item.identifier == "BTC"

    def test_find_by_symbol_case_insensitive(self, registry: WatchlistRegistry) -> None:
        item = registry.find_by_symbol("btc")
        assert item is not None

    def test_find_by_name_alias(self, registry: WatchlistRegistry) -> None:
        item = registry.find_by_name("bitcoin")
        assert item is not None
        assert item.identifier == "BTC"

    def test_find_by_name_unicode_alias(self, registry: WatchlistRegistry) -> None:
        item = registry.find_by_name("₿")
        assert item is not None
        assert item.identifier == "BTC"

    def test_find_by_domain(self, registry: WatchlistRegistry) -> None:
        item = registry.find_by_domain("coindesk.com")
        assert item is not None
        assert item.identifier == "coindesk.com"

    def test_find_by_domain_unknown(self, registry: WatchlistRegistry) -> None:
        assert registry.find_by_domain("unknown.xyz") is None

    def test_find_by_symbol_unknown(self, registry: WatchlistRegistry) -> None:
        assert registry.find_by_symbol("DOGE") is None


# ──────────────────────────────────────────────
# Text search
# ──────────────────────────────────────────────

class TestTextSearch:
    def test_finds_bitcoin_in_text(self, registry: WatchlistRegistry) -> None:
        matches = registry.find_by_text("BlackRock launched a Bitcoin ETF today.")
        identifiers = [m.item.identifier for m in matches]
        assert "BTC" in identifiers

    def test_finds_multiple_assets(self, registry: WatchlistRegistry) -> None:
        matches = registry.find_by_text("Ethereum and Bitcoin both rallied.")
        identifiers = [m.item.identifier for m in matches]
        assert "BTC" in identifiers
        assert "ETH" in identifiers

    def test_no_false_positive_substring(self, registry: WatchlistRegistry) -> None:
        # "ibitcoin" should NOT match BTC (word boundary check)
        matches = registry.find_by_text("ibitcoin is a fake coin")
        identifiers = [m.item.identifier for m in matches]
        assert "BTC" not in identifiers

    def test_finds_entity(self, registry: WatchlistRegistry) -> None:
        matches = registry.find_by_text("Vitalik Buterin spoke at DevCon.")
        identifiers = [m.item.identifier for m in matches]
        assert "Vitalik Buterin" in identifiers

    def test_finds_topic(self, registry: WatchlistRegistry) -> None:
        matches = registry.find_by_text("The SEC launched an investigation into crypto firms.")
        identifiers = [m.item.identifier for m in matches]
        assert "Regulation" in identifiers

    def test_empty_text_returns_empty(self, registry: WatchlistRegistry) -> None:
        assert registry.find_by_text("") == []

    def test_deduplicates_same_item(self, registry: WatchlistRegistry) -> None:
        # Both "bitcoin" and "₿" map to same item — should appear once
        matches = registry.find_by_text("Bitcoin (₿) rally continues")
        btc_matches = [m for m in matches if m.item.identifier == "BTC"]
        assert len(btc_matches) == 1


# ──────────────────────────────────────────────
# Category queries
# ──────────────────────────────────────────────

class TestCategoryQueries:
    def test_get_by_category(self, registry: WatchlistRegistry) -> None:
        crypto_items = registry.get_by_category(WatchlistCategory.CRYPTO)
        assert len(crypto_items) == 2
        assert all(i.category == WatchlistCategory.CRYPTO for i in crypto_items)

    def test_all_symbols_crypto(self, registry: WatchlistRegistry) -> None:
        symbols = registry.all_symbols(WatchlistCategory.CRYPTO)
        assert "BTC" in symbols
        assert "ETH" in symbols

    def test_all_symbols_defaults(self, registry: WatchlistRegistry) -> None:
        symbols = registry.all_symbols()
        assert "BTC" in symbols
        assert "MSTR" in symbols
        assert "IBIT" in symbols
