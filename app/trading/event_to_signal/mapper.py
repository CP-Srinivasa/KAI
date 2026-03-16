"""
Event-to-Asset Mapper
======================
Maps news events / document scores to specific tradeable assets with confidence values.

Three mapping layers (applied in order, combined by max confidence):
  1. Direct ticker detection   — BTC, ETH, AAPL appear literally in text
  2. Entity-to-asset mapping   — "MicroStrategy" → MSTR, "Coinbase" → COIN
  3. Thematic mapping          — DeFi topic → ETH, LINK; Regulation → BTC, ETH

Each mapping produces an AssetMapping with a confidence score (0–1).
Multiple mappings for the same asset are merged, keeping the highest confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.enums import WatchlistCategory
from app.core.logging import get_logger
from app.trading.watchlists.watchlist import WatchlistRegistry

logger = get_logger(__name__)


# ─────────────────────────────────────────────
# Thematic mapping rules
# tag on WatchlistItem → list of asset symbols that are directly affected
# ─────────────────────────────────────────────

THEMATIC_RULES: list[dict[str, Any]] = [
    # Bitcoin-specific topics
    {
        "topic_tags": ["halving"],
        "assets": ["BTC"],
        "confidence": 0.85,
        "reason": "Bitcoin halving directly affects BTC supply/price",
    },
    {
        "topic_tags": ["bitcoin_etf"],
        "assets": ["BTC", "IBIT", "FBTC", "GBTC"],
        "confidence": 0.90,
        "reason": "Bitcoin ETF news directly affects BTC and BTC ETF products",
    },
    {
        "topic_tags": ["bitcoin_proxy", "corporate_bitcoin"],
        "assets": ["BTC", "MSTR"],
        "confidence": 0.75,
        "reason": "Corporate Bitcoin holding affects BTC proxies",
    },
    {
        "topic_tags": ["bitcoin_mining", "miner"],
        "assets": ["BTC", "MARA", "RIOT"],
        "confidence": 0.80,
        "reason": "Mining news affects miners and BTC",
    },
    # Ethereum/DeFi
    {
        "topic_tags": ["defi"],
        "assets": ["ETH", "LINK"],
        "confidence": 0.70,
        "reason": "DeFi activity primarily affects ETH and key DeFi tokens",
    },
    {
        "topic_tags": ["layer2", "scaling"],
        "assets": ["ETH", "MATIC"],
        "confidence": 0.72,
        "reason": "L2 scaling news directly relevant to ETH ecosystem",
    },
    {
        "topic_tags": ["smart_contracts"],
        "assets": ["ETH", "SOL", "ADA"],
        "confidence": 0.65,
        "reason": "Smart contract news affects major L1 platforms",
    },
    # Stablecoins
    {
        "topic_tags": ["stablecoin", "liquidity_risk"],
        "assets": ["USDT", "USDC", "BTC", "ETH"],
        "confidence": 0.75,
        "reason": "Stablecoin risk/depeg triggers broad crypto selling",
    },
    # Regulatory risk (broad)
    {
        "topic_tags": ["regulation", "risk", "legal"],
        "assets": ["BTC", "ETH", "COIN"],
        "confidence": 0.68,
        "reason": "Regulatory actions affect major coins and exchanges",
    },
    # Macro
    {
        "topic_tags": ["macro", "monetary_policy"],
        "assets": ["BTC", "ETH", "NVDA"],
        "confidence": 0.55,
        "reason": "Macro shifts affect risk assets broadly",
    },
    # AI tech
    {
        "topic_tags": ["ai", "innovation"],
        "assets": ["NVDA"],
        "confidence": 0.70,
        "reason": "AI news directly relevant to NVIDIA",
    },
    # Exchange / brokerage
    {
        "topic_tags": ["exchange", "cex"],
        "assets": ["BNB", "COIN"],
        "confidence": 0.72,
        "reason": "Exchange news affects exchange tokens and stocks",
    },
    # Institutional adoption
    {
        "topic_tags": ["institutional", "adoption", "mainstream"],
        "assets": ["BTC", "ETH"],
        "confidence": 0.70,
        "reason": "Institutional adoption news is most bullish for major assets",
    },
    # Security / hacks
    {
        "topic_tags": ["security", "hack_exploit"],
        "assets": ["BTC", "ETH"],
        "confidence": 0.60,
        "reason": "Security exploits trigger broad risk-off sentiment",
    },
]

# Entity → directly linked asset symbols (when the entity appears in text)
ENTITY_TO_ASSET: dict[str, list[str]] = {
    "microstrategy": ["MSTR", "BTC"],
    "coinbase": ["COIN", "BTC"],
    "marathon digital": ["MARA", "BTC"],
    "riot platforms": ["RIOT", "BTC"],
    "nvidia": ["NVDA"],
    "robinhood": ["HOOD"],
    "block": ["SQ", "BTC"],
    "paypal": ["PYPL"],
    "grayscale": ["GBTC", "BTC"],
    "blackrock": ["IBIT", "BTC"],
    "fidelity": ["FBTC", "BTC"],
    "proshares": ["BITO"],
    "ark invest": ["ARKW", "BTC"],
    "sec": ["BTC", "ETH", "COIN"],       # SEC news is regulatory
    "federal reserve": ["BTC", "NVDA"],
    "binance": ["BNB", "BTC"],
    "ftx": ["BTC", "ETH"],               # FTX collapse affects whole market
    "tether": ["USDT", "BTC"],
    "circle": ["USDC", "ETH"],
}


@dataclass
class AssetMapping:
    """A single asset → confidence mapping derived from an event."""

    asset: str                            # Symbol: BTC, ETH, NVDA, etc.
    confidence: float                     # 0.0–1.0
    mapping_type: str                     # direct | entity | thematic
    reason: str = ""                      # Human-readable explanation
    source_trigger: str = ""              # What triggered this mapping

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "confidence": round(self.confidence, 3),
            "mapping_type": self.mapping_type,
            "reason": self.reason,
            "source_trigger": self.source_trigger,
        }


class EventToAssetMapper:
    """
    Maps a text (title + body) to a ranked list of affected assets.

    Uses three layers:
      1. direct    — ticker symbols appear literally in text
      2. entity    — entity names map to known assets
      3. thematic  — tags/topics trigger asset groups

    Results are merged by asset symbol, keeping highest confidence per asset.
    """

    def __init__(self, watchlist: WatchlistRegistry | None = None) -> None:
        self._watchlist = watchlist

    def map(
        self,
        text: str,
        matched_entities: list[str] | None = None,
        matched_tags: list[str] | None = None,
        affected_assets: list[str] | None = None,
    ) -> list[AssetMapping]:
        """
        Map a document to affected assets.

        Args:
            text: Combined title + summary text.
            matched_entities: Entities already detected by KeywordMatcher.
            matched_tags: Tags from LLM analysis or WatchlistItems.
            affected_assets: Assets already provided by LLM output (confidence boost).

        Returns:
            List of AssetMapping, sorted by confidence (highest first), deduplicated.
        """
        all_mappings: dict[str, AssetMapping] = {}

        def _add(mapping: AssetMapping) -> None:
            existing = all_mappings.get(mapping.asset)
            if existing is None or mapping.confidence > existing.confidence:
                all_mappings[mapping.asset] = mapping

        # Layer 1: LLM-provided assets (highest confidence — LLM saw full context)
        for asset in (affected_assets or []):
            _add(AssetMapping(
                asset=asset.upper(),
                confidence=0.90,
                mapping_type="direct",
                reason="Provided by LLM analysis",
                source_trigger=asset,
            ))

        # Layer 2: Direct ticker detection in text
        for mapping in self._detect_direct_tickers(text):
            _add(mapping)

        # Layer 3: Entity-to-asset mapping
        for entity in (matched_entities or []):
            for mapping in self._map_entity(entity):
                _add(mapping)

        # Also scan text for known entity names
        text_lower = text.lower()
        for entity_name, assets in ENTITY_TO_ASSET.items():
            if entity_name in text_lower:
                for asset in assets:
                    _add(AssetMapping(
                        asset=asset,
                        confidence=0.75,
                        mapping_type="entity",
                        reason=f"Entity '{entity_name}' detected in text",
                        source_trigger=entity_name,
                    ))

        # Layer 4: Thematic mapping via tags
        for tag in (matched_tags or []):
            for mapping in self._map_tag(tag):
                _add(mapping)

        # Also map via watchlist matches if registry is available
        if self._watchlist:
            for match in self._watchlist.find_by_text(text):
                item = match.item
                if item.category in (WatchlistCategory.CRYPTO, WatchlistCategory.EQUITIES, WatchlistCategory.ETFS):
                    _add(AssetMapping(
                        asset=item.identifier,
                        confidence=0.80,
                        mapping_type="direct",
                        reason=f"Watchlist match: {item.display_name}",
                        source_trigger=match.matched_alias,
                    ))
                for tag in item.tags:
                    for mapping in self._map_tag(tag):
                        _add(mapping)

        result = sorted(all_mappings.values(), key=lambda m: m.confidence, reverse=True)
        logger.debug("event_to_asset_mapped", total=len(result), assets=[m.asset for m in result])
        return result

    def _detect_direct_tickers(self, text: str) -> list[AssetMapping]:
        """Find uppercase ticker symbols (≥2 chars) appearing in text."""
        # Match standalone uppercase tokens like BTC, ETH, NVDA
        tickers = re.findall(r"\b([A-Z]{2,6})\b", text)
        known_tickers = {
            "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "LINK", "DOT",
            "MATIC", "DOGE", "USDT", "USDC",
            "MSTR", "COIN", "MARA", "RIOT", "NVDA", "HOOD", "SQ", "PYPL",
            "IBIT", "FBTC", "GBTC", "BITO", "ARKW",
        }
        result = []
        for ticker in set(tickers):
            if ticker in known_tickers:
                result.append(AssetMapping(
                    asset=ticker,
                    confidence=0.88,
                    mapping_type="direct",
                    reason=f"Ticker '{ticker}' found in text",
                    source_trigger=ticker,
                ))
        return result

    def _map_entity(self, entity: str) -> list[AssetMapping]:
        """Map a named entity to associated assets."""
        entity_lower = entity.lower()
        for key, assets in ENTITY_TO_ASSET.items():
            if key in entity_lower or entity_lower in key:
                return [
                    AssetMapping(
                        asset=a,
                        confidence=0.78,
                        mapping_type="entity",
                        reason=f"Entity '{entity}' maps to {a}",
                        source_trigger=entity,
                    )
                    for a in assets
                ]
        return []

    def _map_tag(self, tag: str) -> list[AssetMapping]:
        """Apply thematic mapping rules for a single tag."""
        result = []
        tag_lower = tag.lower()
        for rule in THEMATIC_RULES:
            if any(t in tag_lower or tag_lower in t for t in rule["topic_tags"]):
                for asset in rule["assets"]:
                    result.append(AssetMapping(
                        asset=asset,
                        confidence=rule["confidence"],
                        mapping_type="thematic",
                        reason=rule["reason"],
                        source_trigger=tag,
                    ))
        return result

    def top_assets(
        self,
        text: str,
        matched_entities: list[str] | None = None,
        matched_tags: list[str] | None = None,
        affected_assets: list[str] | None = None,
        min_confidence: float = 0.60,
        max_results: int = 5,
    ) -> list[AssetMapping]:
        """Convenience: return top N assets above confidence threshold."""
        mappings = self.map(text, matched_entities, matched_tags, affected_assets)
        return [m for m in mappings if m.confidence >= min_confidence][:max_results]
