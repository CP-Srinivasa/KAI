"""Symbol tradeability guard — rejects degenerate trading pairs.

Root cause 2026-06-25: the autonomous_generator emitted a ``USDT/USDT`` short
(price ~101.9, net -1.70 USD) — a self-pair that should never become an order.
There was no upstream symbol-sanity check, so the garbage propagated all the way
into the paper portfolio. This module is the canonical, dependency-free guard used
both at signal generation (skip early) and at the paper engine (open-side backstop).

Rejected:
- ``base == quote`` (e.g. USDT/USDT, BTC/BTC) — a self-pair, nonsensical.
- stablecoin/stablecoin (e.g. USDC/USDT) — no directional edge, pure noise.

Permissive on parse failure: an unrecognized but well-formed pair is allowed
through (we only block the two clearly-degenerate classes), so this never
silently drops a valid exotic symbol.
"""

from __future__ import annotations

# Stablecoins (and fiat USD) — a pair of two of these has no tradeable direction.
# 2026-06-25: extended with further well-known USD-pegged stablecoins after a
# ``MIM/USDT`` short (entry ~101.95, a phantom price for a ~$1 token) reached the
# paper book and incurred a self-close fee. MIM (Magic Internet Money) and the
# others below are USD-pegged → a pair of two of them is not a real directional
# market, so the open is now blocked at source and its fees never accrue.
STABLECOINS: frozenset[str] = frozenset(
    {
        "USDT",
        "USDC",
        "DAI",
        "TUSD",
        "BUSD",
        "FDUSD",
        "USDD",
        "USDP",
        "GUSD",
        "USDE",
        "PYUSD",
        "USD",
        # 2026-06-25 additions — all USD-pegged stablecoins:
        "MIM",
        "FRAX",
        "LUSD",
        "SUSD",
        "USDS",
        "GHO",
        "CRVUSD",
        "USD0",
        "USDX",
        "USDJ",
    }
)

# Known quote assets for slash-less symbols ("BTCUSDT" -> base BTC, quote USDT).
# Longest-first so "USDT" matches before "USD".
_KNOWN_QUOTES: tuple[str, ...] = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USD", "BTC", "ETH")


def split_symbol(symbol: str) -> tuple[str, str] | None:
    """Split a symbol into (base, quote), upper-cased. None if unparseable.

    Handles "BASE/QUOTE", "BASE-QUOTE" and slash-less "BASEQUOTE" (via known quotes).
    """
    if not symbol or not symbol.strip():
        return None
    s = symbol.strip().upper()
    for sep in ("/", "-", "_"):
        if sep in s:
            base, _, quote = s.partition(sep)
            if base and quote:
                return base, quote
            return None
    for quote in _KNOWN_QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)], quote
    return None


def untradeable_reason(symbol: str) -> str | None:
    """Return a reason string if the symbol is degenerate, else None."""
    parsed = split_symbol(symbol)
    if parsed is None:
        return None  # permissive: unknown format is not our concern
    base, quote = parsed
    if base == quote:
        return "self_pair_base_eq_quote"
    if base in STABLECOINS and quote in STABLECOINS:
        return "stablecoin_pair"
    return None


def is_tradeable_symbol(symbol: str) -> bool:
    """True unless the symbol is a self-pair or a stablecoin/stablecoin pair."""
    return untradeable_reason(symbol) is None
