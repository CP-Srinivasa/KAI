"""Symbol tradeability guard — rejects degenerate trading pairs.

Root cause 2026-06-25: the autonomous_generator emitted a ``USDT/USDT`` short
(price ~101.9, net -1.70 USD) — a self-pair that should never become an order.
There was no upstream symbol-sanity check, so the garbage propagated all the way
into the paper portfolio. This module is the canonical, dependency-free guard used
both at signal generation (skip early) and at the paper engine (open-side backstop).

Rejected:
- ``base == quote`` (e.g. USDT/USDT, BTC/BTC) — a self-pair, nonsensical.
- true-peg-stablecoin/true-peg-stablecoin (e.g. USDC/USDT) — no directional edge.

NOT rejected (SAT-C-465, 2026-06-26): depeg-capable tokens (MIM/FRAX/GHO/sUSD …)
are real directional markets and incur real fees — see ``DEPEG_CAPABLE_TOKENS``.

Permissive on parse failure: an unrecognized but well-formed pair is allowed
through (we only block the two clearly-degenerate classes), so this never
silently drops a valid exotic symbol.
"""

from __future__ import annotations

# True 1:1 USD pegs (and fiat USD) — a pair of two of these has no tradeable
# direction, so it is degenerate noise (USDC/USDT etc.) and never a real market.
#
# SAT-C-465 (security review 2026-06-26): this set is the SSOT for "phantom"
# (fees/PnL excluded from the honest, G2-relevant fee truth). It must therefore
# contain ONLY genuinely peg-locked coins. The 2026-06-25 expansion wrongly added
# DEPEG-CAPABLE tokens (MIM/FRAX/GHO/sUSD/LUSD/crvUSD/USDS/USDX/USDJ/USD0) — those
# have real directional markets (a depeg is a tradeable move) and real fees, so
# classifying them as phantom would HIDE real losses from the fee truth. They are
# kept OUT of STABLECOINS below and listed in ``DEPEG_CAPABLE_TOKENS`` for the record.
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
    }
)

# Documented, deliberately NOT part of STABLECOINS (SAT-C-465). These are
# USD-referenced but depeg-capable / algorithmically-backed tokens: they have real
# directional markets and incur real fees, so a pair like ``FRAX/USDT`` is a REAL
# (possibly loss-making) trade whose fees/PnL must count toward the honest fee
# truth — NOT be silently excluded as "phantom". Kept here purely as a reference so
# the knowledge is not lost; ``untradeable_reason`` intentionally does not use it.
DEPEG_CAPABLE_TOKENS: frozenset[str] = frozenset(
    {"MIM", "FRAX", "LUSD", "SUSD", "USDS", "GHO", "CRVUSD", "USD0", "USDX", "USDJ"}
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
