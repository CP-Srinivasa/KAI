"""Tests for the Research Watchlists."""

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from app.analysis.keywords.watchlist import WatchlistEntry
from app.core.watchlists import WatchlistRegistry, parse_watchlist_type
from tests.unit.factories import make_document


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


def test_watchlist_registry_loads_assets_persons_topics_and_sources(tmp_path: Path) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        yaml.safe_dump(
            {
                "crypto": [
                    {
                        "symbol": "ETH",
                        "name": "Ethereum",
                        "aliases": ["ethereum", "eth"],
                        "tags": ["defi"],
                    }
                ],
                "persons": [
                    {
                        "name": "Gary Gensler",
                        "aliases": ["gensler", "sec chair"],
                        "tags": ["regulation"],
                    }
                ],
                "topics": [
                    {
                        "name": "Regulation",
                        "aliases": ["regulatory"],
                        "tags": ["risk"],
                    }
                ],
                "domains": [
                    {
                        "domain": "reuters.com",
                        "credibility": 0.95,
                        "tags": ["macro"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    registry = WatchlistRegistry.from_file(watchlists_path)

    assert registry.get_watchlist("defi", item_type="assets") == ["ETH"]
    assert registry.get_watchlist("regulation", item_type="persons") == ["Gary Gensler"]
    assert registry.get_watchlist("risk", item_type="topics") == ["Regulation"]
    assert registry.get_watchlist("macro", item_type="sources") == ["reuters.com"]
    assert registry.get_items(item_type="persons") == ["Gary Gensler"]
    assert registry.get_items(item_type="topics") == ["Regulation"]
    assert registry.get_items(item_type="sources") == ["reuters.com"]


def test_watchlist_registry_save_roundtrip(tmp_path: Path) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        yaml.safe_dump(
            {
                "crypto": [
                    {
                        "symbol": "BTC",
                        "name": "Bitcoin",
                        "aliases": ["bitcoin", "btc"],
                        "tags": ["major"],
                    }
                ],
                "persons": [
                    {
                        "name": "Michael Saylor",
                        "aliases": ["saylor"],
                        "tags": ["bitcoin"],
                    }
                ],
                "topics": [
                    {
                        "name": "DeFi",
                        "aliases": ["defi"],
                        "tags": ["ethereum"],
                    }
                ],
                "domains": [
                    {
                        "domain": "coindesk.com",
                        "credibility": 0.85,
                        "tags": ["primary"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    registry = WatchlistRegistry.from_file(watchlists_path)
    roundtrip_path = tmp_path / "roundtrip.yml"
    registry.save(roundtrip_path)

    reloaded = WatchlistRegistry.from_file(roundtrip_path)

    assert reloaded.get_watchlist("major", item_type="assets") == ["BTC"]
    assert reloaded.get_watchlist("bitcoin", item_type="persons") == ["Michael Saylor"]
    assert reloaded.get_watchlist("ethereum", item_type="topics") == ["DeFi"]
    assert reloaded.get_watchlist("primary", item_type="sources") == ["coindesk.com"]


def test_watchlist_registry_filters_documents_by_supported_types(tmp_path: Path) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        yaml.safe_dump(
            {
                "crypto": [
                    {
                        "symbol": "BTC",
                        "name": "Bitcoin",
                        "aliases": ["bitcoin"],
                        "tags": ["major"],
                    }
                ],
                "persons": [
                    {
                        "name": "Gary Gensler",
                        "aliases": ["gensler"],
                        "tags": ["bitcoin"],
                    }
                ],
                "topics": [
                    {
                        "name": "Security",
                        "aliases": ["security"],
                        "tags": ["security"],
                    }
                ],
                "domains": [
                    {
                        "domain": "reuters.com",
                        "credibility": 0.95,
                        "tags": ["macro"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    registry = WatchlistRegistry.from_file(watchlists_path)

    docs = [
        make_document(
            title="BTC news",
            is_analyzed=True,
            tickers=["BTC"],
            crypto_assets=["BTC"],
        ),
        make_document(
            title="Gensler speech",
            is_analyzed=True,
            people=["Gary Gensler"],
        ),
        make_document(
            title="Regulation update",
            is_analyzed=True,
            tags=["security"],
        ),
        make_document(
            title="Reuters macro article",
            is_analyzed=True,
            url="https://www.reuters.com/world",
        ),
    ]

    assert [doc.title for doc in registry.filter_documents(docs, "major", item_type="assets")] == [
        "BTC news"
    ]
    assert [
        doc.title for doc in registry.filter_documents(docs, "bitcoin", item_type="persons")
    ] == ["Gensler speech"]
    assert [
        doc.title for doc in registry.filter_documents(docs, "security", item_type="topics")
    ] == ["Regulation update"]
    assert [doc.title for doc in registry.filter_documents(docs, "macro", item_type="sources")] == [
        "Reuters macro article"
    ]


def test_parse_watchlist_type_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        parse_watchlist_type("unknown")
