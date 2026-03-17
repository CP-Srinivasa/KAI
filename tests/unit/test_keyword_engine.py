"""Tests for KeywordEngine, WatchlistEntry, EntityAlias loaders."""

from pathlib import Path

import yaml

from app.analysis.keywords.aliases import EntityAlias, load_entity_aliases
from app.analysis.keywords.engine import KeywordEngine, _tokenize
from app.analysis.keywords.watchlist import WatchlistEntry, load_watchlist

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_engine(
    keywords: set[str] | None = None,
    watchlist: list[WatchlistEntry] | None = None,
    aliases: list[EntityAlias] | None = None,
) -> KeywordEngine:
    return KeywordEngine(
        keywords=frozenset(keywords or set()),
        watchlist_entries=watchlist or [],
        entity_aliases=aliases or [],
    )


def _btc_entry() -> WatchlistEntry:
    return WatchlistEntry(
        symbol="BTC",
        name="Bitcoin",
        aliases=frozenset({"bitcoin", "xbt", "₿"}),
        tags=("store_of_value",),
        category="crypto",
    )


def _eth_entry() -> WatchlistEntry:
    return WatchlistEntry(
        symbol="ETH",
        name="Ethereum",
        aliases=frozenset({"ethereum", "ether"}),
        tags=("layer1",),
        category="crypto",
    )


def _saylor_alias() -> EntityAlias:
    return EntityAlias(
        canonical="Michael Saylor",
        aliases=frozenset({"saylor", "michael saylor", "michael j. saylor"}),
        handles={"twitter": "@saylor"},
        category="bitcoin_maxi",
    )


# ── Tokenizer ─────────────────────────────────────────────────────────────────


def test_tokenizer_strips_punctuation():
    tokens = _tokenize("Bitcoin, ETH! rally — BTC?")
    assert "bitcoin" in tokens
    assert "eth" in tokens
    assert "btc" in tokens


def test_tokenizer_empty():
    assert _tokenize("") == []


def test_tokenizer_preserves_special_chars():
    tokens = _tokenize("₿ is Bitcoin's symbol")
    assert "₿" in tokens


# ── Watchlist matching ─────────────────────────────────────────────────────────


def test_match_by_symbol():
    engine = _make_engine(watchlist=[_btc_entry()])
    hits = engine.match("BTC rallied today")
    assert any(h.canonical == "BTC" and h.category == "crypto" for h in hits)


def test_match_by_name():
    engine = _make_engine(watchlist=[_btc_entry()])
    hits = engine.match("Bitcoin is up 5%")
    assert any(h.canonical == "BTC" for h in hits)


def test_match_by_alias():
    engine = _make_engine(watchlist=[_btc_entry()])
    hits = engine.match("XBT futures settle at record high")
    assert any(h.canonical == "BTC" for h in hits)


def test_multiple_assets():
    engine = _make_engine(watchlist=[_btc_entry(), _eth_entry()])
    hits = engine.match("BTC and ETH both rally as altcoins lag")
    canonicals = {h.canonical for h in hits}
    assert "BTC" in canonicals
    assert "ETH" in canonicals


def test_occurrence_count():
    engine = _make_engine(watchlist=[_btc_entry()])
    hits = engine.match("BTC is up. Bitcoin surged. BTC hit ATH.")
    btc_hit = next((h for h in hits if h.canonical == "BTC"), None)
    assert btc_hit is not None
    assert btc_hit.occurrences == 3  # BTC, Bitcoin, BTC


# ── Entity alias matching ──────────────────────────────────────────────────────


def test_match_entity_alias():
    engine = _make_engine(aliases=[_saylor_alias()])
    hits = engine.match("Saylor announces 1000 BTC purchase")
    assert any(h.canonical == "Michael Saylor" for h in hits)


def test_match_entity_canonical_name():
    engine = _make_engine(aliases=[_saylor_alias()])
    hits = engine.match("Michael Saylor is bullish on bitcoin")
    assert any(h.canonical == "Michael Saylor" for h in hits)


def test_entity_category_passthrough():
    engine = _make_engine(aliases=[_saylor_alias()])
    hits = engine.match("Saylor tweets again")
    hit = next((h for h in hits if h.canonical == "Michael Saylor"), None)
    assert hit is not None
    assert hit.category == "bitcoin_maxi"


# ── Keyword matching ───────────────────────────────────────────────────────────


def test_plain_keyword_match():
    engine = _make_engine(keywords={"inflation", "regulation"})
    hits = engine.match("inflation data shows CPI above expectations regulation fears grow")
    canonicals = {h.canonical for h in hits}
    assert "inflation" in canonicals
    assert "regulation" in canonicals


def test_keyword_category_is_keyword():
    engine = _make_engine(keywords={"inflation"})
    hits = engine.match("inflation surges")
    hit = next((h for h in hits if h.canonical == "inflation"), None)
    assert hit is not None
    assert hit.category == "keyword"


# ── Priority: watchlist > entity > keyword ────────────────────────────────────


def test_watchlist_overrides_keyword():
    # "Bitcoin" added as both keyword and watchlist entry — watchlist wins
    engine = _make_engine(
        keywords={"Bitcoin"},
        watchlist=[_btc_entry()],
    )
    hits = engine.match("Bitcoin surges")
    hit = next((h for h in hits if h.canonical in ("BTC", "Bitcoin")), None)
    assert hit is not None
    assert hit.canonical == "BTC"       # watchlist wins
    assert hit.category == "crypto"


# ── match_tickers ──────────────────────────────────────────────────────────────


def test_match_tickers_returns_symbols_only():
    engine = _make_engine(watchlist=[_btc_entry(), _eth_entry()], keywords={"regulation"})
    tickers = engine.match_tickers("BTC and ETH react to new regulation")
    assert "BTC" in tickers
    assert "ETH" in tickers
    assert "regulation" not in tickers


# ── match_entities ────────────────────────────────────────────────────────────


def test_match_entities_excludes_tickers():
    engine = _make_engine(watchlist=[_btc_entry()], aliases=[_saylor_alias()])
    entities = engine.match_entities("Saylor buys more BTC")
    names = [e.canonical for e in entities]
    assert "Michael Saylor" in names
    assert "BTC" not in names


# ── Edge cases ────────────────────────────────────────────────────────────────


def test_empty_text():
    engine = _make_engine(watchlist=[_btc_entry()])
    assert engine.match("") == []


def test_no_matches():
    engine = _make_engine(watchlist=[_btc_entry()])
    assert engine.match("The weather in Berlin is nice today") == []


def test_results_sorted_by_occurrences_desc():
    engine = _make_engine(watchlist=[_btc_entry(), _eth_entry()])
    hits = engine.match("BTC BTC BTC ETH")
    assert hits[0].canonical == "BTC"
    assert hits[0].occurrences == 3


# ── from_monitor_dir ──────────────────────────────────────────────────────────


def test_from_monitor_dir_loads_real_files():
    """Smoke test: loads from actual monitor/ directory."""
    engine = KeywordEngine.from_monitor_dir("monitor")
    hits = engine.match("Bitcoin ETF approved — BTC surges past ATH")
    canonicals = {h.canonical for h in hits}
    assert "BTC" in canonicals


def test_from_monitor_dir_missing_files(tmp_path: Path):
    """Missing monitor files → empty engine, no crash."""
    engine = KeywordEngine.from_monitor_dir(tmp_path)
    assert engine.match("anything") == []


def test_from_monitor_dir_partial_files(tmp_path: Path):
    """Only watchlists.yml present — still loads cleanly."""
    watchlist_data = {
        "crypto": [{"symbol": "SOL", "name": "Solana", "aliases": ["solana"], "tags": []}]
    }
    (tmp_path / "watchlists.yml").write_text(yaml.dump(watchlist_data))
    engine = KeywordEngine.from_monitor_dir(tmp_path)
    hits = engine.match("Solana breaks $200")
    assert any(h.canonical == "SOL" for h in hits)


# ── Loader unit tests ─────────────────────────────────────────────────────────


def test_load_watchlist_missing_file(tmp_path: Path):
    assert load_watchlist(tmp_path / "nonexistent.yml") == []


def test_load_watchlist_parses_crypto(tmp_path: Path):
    data = {
        "crypto": [{"symbol": "BTC", "name": "Bitcoin", "aliases": ["bitcoin"], "tags": ["l1"]}]
    }
    p = tmp_path / "watchlists.yml"
    p.write_text(yaml.dump(data))
    entries = load_watchlist(p)
    assert len(entries) == 1
    assert entries[0].symbol == "BTC"
    assert "bitcoin" in entries[0].aliases


def test_load_entity_aliases_missing_file(tmp_path: Path):
    assert load_entity_aliases(tmp_path / "nonexistent.yml") == []


def test_load_entity_aliases_parses(tmp_path: Path):
    data = {
        "entity_aliases": [
            {
                "canonical": "Vitalik Buterin",
                "aliases": ["Vitalik", "vitalik"],
                "handles": {"twitter": "@VitalikButerin"},
                "category": "ethereum_founder",
            }
        ]
    }
    p = tmp_path / "entity_aliases.yml"
    p.write_text(yaml.dump(data))
    entities = load_entity_aliases(p)
    assert len(entities) == 1
    assert entities[0].canonical == "Vitalik Buterin"
    assert "vitalik" in entities[0].aliases
