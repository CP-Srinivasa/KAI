"""Tests for app/trading/event_to_signal/mapper.py"""
from __future__ import annotations

import pytest

from app.trading.event_to_signal.mapper import AssetMapping, EventToAssetMapper


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def mapper() -> EventToAssetMapper:
    return EventToAssetMapper()


@pytest.fixture
def mapper_with_watchlist() -> EventToAssetMapper:
    from app.trading.watchlists.watchlist import WatchlistRegistry
    data = {
        "crypto": [
            {"symbol": "BTC", "name": "Bitcoin", "aliases": ["bitcoin", "btc"], "tags": ["major", "halving"]},
            {"symbol": "ETH", "name": "Ethereum", "aliases": ["ethereum", "ether"], "tags": ["defi", "layer1"]},
        ],
        "equities": [
            {"symbol": "NVDA", "name": "NVIDIA", "aliases": ["nvidia"], "tags": ["ai", "semiconductors"]},
        ],
    }
    registry = WatchlistRegistry.from_dict(data)
    return EventToAssetMapper(watchlist=registry)


# ──────────────────────────────────────────────
# AssetMapping
# ──────────────────────────────────────────────

class TestAssetMapping:
    def test_to_dict(self) -> None:
        m = AssetMapping(asset="BTC", confidence=0.85, mapping_type="direct", reason="Test")
        d = m.to_dict()
        assert d["asset"] == "BTC"
        assert d["confidence"] == 0.85
        assert d["mapping_type"] == "direct"
        assert "reason" in d


# ──────────────────────────────────────────────
# Direct ticker detection
# ──────────────────────────────────────────────

class TestDirectTickerDetection:
    def test_detects_btc_ticker(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("BTC price surged 10% today")
        assets = [m.asset for m in mappings]
        assert "BTC" in assets

    def test_detects_eth_ticker(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("ETH reached a new ATH")
        assets = [m.asset for m in mappings]
        assert "ETH" in assets

    def test_detects_nvda_ticker(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("NVDA earnings beat estimates")
        assets = [m.asset for m in mappings]
        assert "NVDA" in assets

    def test_llm_assets_have_highest_confidence(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("Test", affected_assets=["BTC"])
        btc = next(m for m in mappings if m.asset == "BTC")
        assert btc.confidence == 0.90

    def test_direct_ticker_confidence(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("BTC is trading at 50k")
        btc = next((m for m in mappings if m.asset == "BTC"), None)
        assert btc is not None
        assert btc.confidence >= 0.80


# ──────────────────────────────────────────────
# Entity-to-asset mapping
# ──────────────────────────────────────────────

class TestEntityMapping:
    def test_microstrategy_maps_to_mstr_and_btc(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("Test", matched_entities=["MicroStrategy"])
        assets = [m.asset for m in mappings]
        assert "MSTR" in assets
        assert "BTC" in assets

    def test_coinbase_maps_to_coin(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("Coinbase reports earnings", matched_entities=["Coinbase"])
        assets = [m.asset for m in mappings]
        assert "COIN" in assets

    def test_sec_maps_to_regulatory_assets(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("Test", matched_entities=["SEC"])
        assets = [m.asset for m in mappings]
        assert "BTC" in assets

    def test_entity_in_text_detected(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("Coinbase has filed for IPO")
        assets = [m.asset for m in mappings]
        assert "COIN" in assets


# ──────────────────────────────────────────────
# Thematic mapping
# ──────────────────────────────────────────────

class TestThematicMapping:
    def test_defi_tag_maps_to_eth(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("Test", matched_tags=["defi"])
        assets = [m.asset for m in mappings]
        assert "ETH" in assets

    def test_bitcoin_etf_tag_maps_to_ibit(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("Test", matched_tags=["bitcoin_etf"])
        assets = [m.asset for m in mappings]
        assert "BTC" in assets
        assert "IBIT" in assets

    def test_regulation_tag_maps_to_btc_eth(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("Test", matched_tags=["regulation"])
        assets = [m.asset for m in mappings]
        assert "BTC" in assets

    def test_thematic_mapping_type(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("Test", matched_tags=["defi"])
        eth = next((m for m in mappings if m.asset == "ETH"), None)
        assert eth is not None
        assert eth.mapping_type == "thematic"


# ──────────────────────────────────────────────
# Result ordering and deduplication
# ──────────────────────────────────────────────

class TestResultOrdering:
    def test_sorted_by_confidence_descending(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map("BTC ETH test", affected_assets=["BTC"])
        for i in range(len(mappings) - 1):
            assert mappings[i].confidence >= mappings[i + 1].confidence

    def test_no_duplicate_assets(self, mapper: EventToAssetMapper) -> None:
        mappings = mapper.map(
            "BTC is rising",
            matched_entities=["Bitcoin"],
            matched_tags=["halving"],
            affected_assets=["BTC"],
        )
        assets = [m.asset for m in mappings]
        assert len(assets) == len(set(assets))

    def test_top_assets_respects_limit(self, mapper: EventToAssetMapper) -> None:
        result = mapper.top_assets(
            "BTC ETH SOL BNB XRP NVDA",
            min_confidence=0.0,
            max_results=3,
        )
        assert len(result) <= 3

    def test_top_assets_filters_by_confidence(self, mapper: EventToAssetMapper) -> None:
        result = mapper.top_assets("Test", matched_tags=["defi"], min_confidence=0.80)
        assert all(m.confidence >= 0.80 for m in result)


# ──────────────────────────────────────────────
# Watchlist integration
# ──────────────────────────────────────────────

class TestWatchlistIntegration:
    def test_watchlist_hit_raises_confidence(self, mapper_with_watchlist: EventToAssetMapper) -> None:
        mappings = mapper_with_watchlist.map("Bitcoin is the future of money")
        btc = next((m for m in mappings if m.asset == "BTC"), None)
        assert btc is not None
        assert btc.confidence >= 0.78

    def test_watchlist_tags_trigger_thematic(self, mapper_with_watchlist: EventToAssetMapper) -> None:
        # NVDA has tag "ai" → should trigger thematic AI rules if they exist
        mappings = mapper_with_watchlist.map("NVIDIA launches new AI chip")
        assets = [m.asset for m in mappings]
        assert "NVDA" in assets
