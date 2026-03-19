"""Tests for the Research Watchlists."""

from app.analysis.keywords.watchlist import WatchlistEntry
from app.research.watchlists import WatchlistRegistry


def test_watchlist_registry_builds_index():
    entries = [
        WatchlistEntry("BTC", "Bitcoin", frozenset(), ("layer1", "major"), "crypto"),
        WatchlistEntry("ETH", "Ethereum", frozenset(), ("layer1", "defi"), "crypto"),
        WatchlistEntry("UNI", "Uniswap", frozenset(), ("defi", "dex"), "crypto"),
    ]

    registry = WatchlistRegistry(entries)

    layer1 = registry.get_watchlist("layer1")
    assert layer1 == ["BTC", "ETH"]

    defi = registry.get_watchlist("defi")
    assert defi == ["ETH", "UNI"]

    empty = registry.get_watchlist("nonexistent")
    assert empty == []


def test_watchlist_registry_case_insensitive_tags():
    entries = [
        WatchlistEntry("BTC", "Bitcoin", frozenset(), ("Layer1",), "crypto"),
    ]
    registry = WatchlistRegistry(entries)

    layer1 = registry.get_watchlist("layer1")
    assert layer1 == ["BTC"]

    layer1_upper = registry.get_watchlist("LAYER1")
    assert layer1_upper == ["BTC"]


def test_watchlist_registry_get_all():
    entries = [
        WatchlistEntry("BTC", "Bitcoin", frozenset(), ("major",), "crypto"),
        WatchlistEntry("ETH", "Ethereum", frozenset(), ("major",), "crypto"),
    ]
    registry = WatchlistRegistry(entries)
    all_watchlists = registry.get_all_watchlists()

    assert "major" in all_watchlists
    assert all_watchlists["major"] == ["BTC", "ETH"]


def test_watchlist_registry_get_by_category():
    entries = [
        WatchlistEntry("BTC", "Bitcoin", frozenset(), (), "crypto"),
        WatchlistEntry("MSTR", "MicroStrategy", frozenset(), (), "equity"),
    ]
    registry = WatchlistRegistry(entries)

    crypto = registry.get_symbols_for_category("crypto")
    assert crypto == ["BTC"]

    equity = registry.get_symbols_for_category("equity")
    assert equity == ["MSTR"]
