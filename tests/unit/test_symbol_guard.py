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
        "MIM/USDT",  # the 2026-06-25 phantom: MIM is USD-pegged -> stablecoin pair
        "MIMUSDT",  # slash-less form
        "FRAX/USDT",
        "LUSD/USDC",
        "GHO/USDT",
        "CRVUSD/USDT",
        "USDS/USDT",
    ],
)
def test_extended_stablecoin_pairs_are_untradeable(symbol: str) -> None:
    # 2026-06-25: extended stablecoin set so MIM/USDT & co. are blocked at source
    # and never accrue phantom self-close fees.
    assert is_tradeable_symbol(symbol) is False
    assert untradeable_reason(symbol) == "stablecoin_pair"


def test_real_token_vs_stable_still_tradeable() -> None:
    # The expansion must NOT block legit directional pairs (no false positives).
    for sym in ("DEXE/USDT", "BTC/USDT", "SOL/USDC", "MKR/DAI"):
        assert is_tradeable_symbol(sym) is True
        assert untradeable_reason(sym) is None
