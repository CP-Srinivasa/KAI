"""Asset (ticker) detector for crypto and key equities.

Detects mentions of crypto assets and crypto-adjacent stocks in text.
Returns canonical asset names for downstream scoring and alert routing.

Scope:
- Major crypto assets by ticker symbol and name
- Key crypto-adjacent equities (MSTR, COIN, RIOT, MARA, etc.)
- ETF names (IBIT, FBTC, GBTC, etc.)

Detection uses whole-word matching to avoid false positives
(e.g. "ADA" must not match inside "Kanada").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Asset registry ────────────────────────────────────────────────────────────
# Format: canonical_name → list[alias/ticker]
# Order matters for ties: first canonical wins display.

_CRYPTO_ASSETS: dict[str, list[str]] = {
    "Bitcoin": ["Bitcoin", "BTC", "WBTC", "Satoshi", "Satoshis"],
    "Ethereum": ["Ethereum", "ETH", "Ether", "EVM"],
    "Solana": ["Solana", "SOL"],
    "XRP": ["XRP", "Ripple"],
    "Cardano": ["Cardano", "ADA"],
    "Polkadot": ["Polkadot", "DOT"],
    "Chainlink": ["Chainlink", "LINK"],
    "Uniswap": ["Uniswap", "UNI"],
    "Aave": ["Aave", "AAVE"],
    "Litecoin": ["Litecoin", "LTC"],
    "Dogecoin": ["Dogecoin", "DOGE"],
    "Avalanche": ["Avalanche", "AVAX"],
    "Polygon": ["Polygon", "MATIC", "POL"],
    "BNB": ["BNB", "Binance Coin", "Binance Smart Chain", "BSC"],
    "Tron": ["Tron", "TRX"],
    "Stellar": ["Stellar", "XLM"],
    "Cosmos": ["Cosmos", "ATOM"],
    "Near Protocol": ["NEAR", "Near Protocol"],
    "Aptos": ["Aptos", "APT"],
    "Arbitrum": ["Arbitrum", "ARB"],
    "Optimism": ["Optimism", "OP"],
    "Sui": ["Sui", "SUI"],
    "Toncoin": ["Toncoin", "TON"],
    "Monero": ["Monero", "XMR"],
    "Internet Computer": ["ICP", "Internet Computer"],
    "Stablecoin (USDT)": ["USDT", "Tether"],
    "Stablecoin (USDC)": ["USDC"],
    "Stablecoin (DAI)": ["DAI"],
    "DeFi": ["DeFi", "Decentralized Finance"],
    "NFT": ["NFT", "Non-fungible Token"],
    "Web3": ["Web3"],
}

_EQUITY_ASSETS: dict[str, list[str]] = {
    "MicroStrategy": ["MicroStrategy", "MSTR"],
    "Coinbase": ["Coinbase", "COIN"],
    "Riot Platforms": ["Riot Platforms", "RIOT"],
    "Marathon Digital": ["Marathon Digital", "MARA"],
    "CleanSpark": ["CleanSpark", "CLSK"],
    "Bit Digital": ["Bit Digital", "BTBT"],
    "Galaxy Digital": ["Galaxy Digital", "BRPHF"],
}

_ETF_ASSETS: dict[str, list[str]] = {
    "Bitcoin ETF (IBIT)": ["IBIT"],
    "Bitcoin ETF (FBTC)": ["FBTC"],
    "Bitcoin ETF (GBTC)": ["GBTC"],
    "Bitcoin ETF (ARKB)": ["ARKB"],
    "Bitcoin ETF (BITB)": ["BITB"],
}


def _build_lookup() -> dict[str, tuple[str, re.Pattern[str]]]:
    """Build alias → (canonical, compiled_pattern) lookup."""
    lookup: dict[str, tuple[str, re.Pattern[str]]] = {}
    all_registries = [_CRYPTO_ASSETS, _EQUITY_ASSETS, _ETF_ASSETS]
    for registry in all_registries:
        for canonical, aliases in registry.items():
            for alias in aliases:
                escaped = re.escape(alias)
                pat = re.compile(rf"\b{escaped}\b", re.IGNORECASE | re.UNICODE)
                # Prefer first registry entry if alias appears multiple times
                if alias not in lookup:
                    lookup[alias] = (canonical, pat)
    return lookup


_LOOKUP: dict[str, tuple[str, re.Pattern[str]]] = _build_lookup()


@dataclass(frozen=True)
class AssetMatch:
    canonical: str
    alias: str  # which alias triggered the match
    in_title: bool
    in_text: bool
    count: int


def detect_assets(title: str, text: str | None = None) -> list[AssetMatch]:
    """Detect crypto assets and key equities in title and text.

    Returns one AssetMatch per canonical asset (deduplicated by canonical name).
    Title matches are ranked higher in the returned list.
    """
    text = text or ""
    found: dict[str, AssetMatch] = {}  # canonical → best match so far

    for alias, (canonical, pat) in _LOOKUP.items():
        title_hits = len(pat.findall(title))
        text_hits = len(pat.findall(text))
        total = title_hits + text_hits
        if total == 0:
            continue

        new_match = AssetMatch(
            canonical=canonical,
            alias=alias,
            in_title=title_hits > 0,
            in_text=text_hits > 0,
            count=total,
        )

        existing = found.get(canonical)
        if existing is None or new_match.count > existing.count:
            found[canonical] = new_match

    # Sort: title matches first, then by count
    return sorted(found.values(), key=lambda m: (not m.in_title, -m.count))


def canonical_names(matches: list[AssetMatch]) -> list[str]:
    """Extract just the canonical names from a list of AssetMatch."""
    return [m.canonical for m in matches]
