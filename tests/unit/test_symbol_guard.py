"""Unit tests for the symbol tradeability guard (DQ-Fix 2026-06-25)."""

from __future__ import annotations

import pytest

from app.core.symbol_guard import is_tradeable_symbol, split_symbol, untradeable_reason


@pytest.mark.parametrize(
    "symbol",
    [
        "USDT/USDT",  # the actual generator leak
        "USDTUSDT",  # slash-less form
        "BTC/BTC",  # self-pair
        "USDC/USDT",  # stablecoin/stablecoin
        "DAI/USDT",
        "USDCUSDT",
    ],
)
def test_untradeable_symbols_rejected(symbol: str) -> None:
    assert is_tradeable_symbol(symbol) is False
    assert untradeable_reason(symbol) in {"self_pair_base_eq_quote", "stablecoin_pair"}


@pytest.mark.parametrize(
    "symbol",
    [
        "BTC/USDT",
        "ETH/USDT",
        "BTCUSDT",
        "SOL/USDC",
        "XRP-USDT",
        "DOGE/USDT",
    ],
)
def test_tradeable_symbols_pass(symbol: str) -> None:
    assert is_tradeable_symbol(symbol) is True
    assert untradeable_reason(symbol) is None


def test_unparseable_symbol_is_permissive() -> None:
    # Unknown format must NOT be silently dropped — only the two degenerate
    # classes are blocked.
    assert is_tradeable_symbol("WEIRDTOKEN") is True
    assert split_symbol("WEIRDTOKEN") is None


def test_split_symbol_forms() -> None:
    assert split_symbol("BTC/USDT") == ("BTC", "USDT")
    assert split_symbol("btc-usdt") == ("BTC", "USDT")
    assert split_symbol("BTCUSDT") == ("BTC", "USDT")
    assert split_symbol("USDTUSDT") == ("USDT", "USDT")
    assert split_symbol("") is None


@pytest.mark.parametrize(
    "symbol",
    [
        "MIM/USDT",  # SAT-C-465: depeg-capable -> a REAL directional market
        "MIMUSDT",  # slash-less form
        "FRAX/USDT",
        "LUSD/USDC",
        "GHO/USDT",
        "CRVUSD/USDT",
        "USDS/USDT",
        "SUSD/USDT",
        "USDX/USDT",
        "USDJ/USDT",
    ],
)
def test_depeg_capable_tokens_are_tradeable(symbol: str) -> None:
    # SAT-C-465 (security review 2026-06-26): the 2026-06-25 expansion wrongly
    # marked depeg-capable USD-referenced tokens (MIM/FRAX/GHO/sUSD/LUSD/crvUSD/
    # USDS/USDX/USDJ/USD0) as untradeable stablecoin pairs. They have real
    # directional markets and incur real fees → must NOT be phantom-excluded
    # (which would hide real losses from the honest, G2-relevant fee truth).
    assert is_tradeable_symbol(symbol) is True
    assert untradeable_reason(symbol) is None


def test_true_peg_pairs_still_untradeable() -> None:
    # The narrowing must STILL block genuine 1:1-peg/peg pairs (no directional edge).
    for sym in ("USDC/USDT", "DAI/USDT", "TUSD/USDC", "USDP/DAI"):
        assert is_tradeable_symbol(sym) is False
        assert untradeable_reason(sym) == "stablecoin_pair"


def test_depeg_set_disjoint_from_stablecoins() -> None:
    # Invariant lock (SAT-C-465): the documented depeg-capable set must never leak
    # back into the phantom STABLECOINS set, or real fees get hidden again.
    from app.core.symbol_guard import DEPEG_CAPABLE_TOKENS, STABLECOINS

    assert DEPEG_CAPABLE_TOKENS.isdisjoint(STABLECOINS)


def test_real_token_vs_stable_still_tradeable() -> None:
    # The guard must NOT block legit directional pairs (no false positives).
    for sym in ("DEXE/USDT", "BTC/USDT", "SOL/USDC", "MKR/DAI"):
        assert is_tradeable_symbol(sym) is True
        assert untradeable_reason(sym) is None
