"""Unit tests for the asset universe loader (app/trading/asset_universe.py).

Covers: watchlist+overlay merge, asset categories, missing-data → not_evaluable
(never estimated), tradability tri-state, reserve/stablecoin flags, base-symbol
normalisation, empty/missing config robustness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.trading.asset_universe import (
    UNKNOWN,
    AssetUniverse,
    UniverseLimits,
    base_symbol,
    get_asset_universe,
)

WATCHLIST_YML = """
crypto:
  - symbol: BTC
    name: Bitcoin
    tags: [store_of_value, major]
  - symbol: SOL
    name: Solana
    tags: [layer1]
  - symbol: ZZZ
    name: NoOverlayCoin
    tags: []
equities:
  - symbol: NVDA
    name: NVIDIA
    tags: [ai]
persons:
  - name: Satoshi Nakamoto
    tags: [bitcoin]
topics:
  - name: DeFi
    tags: [ethereum]
"""

OVERLAY_YML = """
version: 1
defaults:
  horizon: unknown
  tradable: unknown
limits:
  max_single_asset_pct: 20.0
  max_btc_eth_short_term_pct: 35.0
assets:
  BTC:
    horizon: long_term_reserve
    sector: store_of_value
    risk_tier: medium
    liquidity_tier: very_high
    volatility_tier: medium
    data_quality: high
    tradable: true
    correlation_group: btc_beta
  SOL:
    horizon: short_term
    sector: smart_contract_l1
    risk_tier: high
    liquidity_tier: high
    volatility_tier: high
    data_quality: high
    tradable: true
    correlation_group: l1_alts
  NVDA:
    horizon: long_term_reserve
    sector: semiconductors
    risk_tier: medium
    liquidity_tier: very_high
    volatility_tier: high
    data_quality: high
    tradable: false
    correlation_group: ai_equity
"""


@pytest.fixture()
def universe(tmp_path: Path) -> AssetUniverse:
    wl = tmp_path / "watchlists.yml"
    wl.write_text(WATCHLIST_YML, encoding="utf-8")
    ov = tmp_path / "asset_universe.yaml"
    ov.write_text(OVERLAY_YML, encoding="utf-8")
    return AssetUniverse.load(watchlist_path=wl, overlay_path=ov)


def test_base_symbol_normalises_pairs() -> None:
    assert base_symbol("BTC/USDT") == "BTC"
    assert base_symbol("eth-usdt") == "ETH"
    assert base_symbol("SOLUSDT") == "SOL"
    assert base_symbol("BTC") == "BTC"
    assert base_symbol("") == ""


def test_persons_and_topics_excluded(universe: AssetUniverse) -> None:
    symbols = {m.symbol for m in universe.all()}
    assert symbols == {"BTC", "SOL", "ZZZ", "NVDA"}
    assert "SATOSHI NAKAMOTO" not in symbols
    assert "DEFI" not in symbols


def test_overlay_merge_enriches_dimensions(universe: AssetUniverse) -> None:
    btc = universe.get("BTC/USDT")
    assert btc is not None
    assert btc.horizon == "long_term_reserve"
    assert btc.sector == "store_of_value"
    assert btc.correlation_group == "btc_beta"
    assert btc.is_reserve is True
    assert btc.is_tradable is True
    assert btc.evaluable is True
    assert btc.score is not None


def test_missing_overlay_is_not_evaluable_not_estimated(universe: AssetUniverse) -> None:
    """ZZZ has no overlay → all tiers unknown, no score invented."""
    zzz = universe.get("ZZZ")
    assert zzz is not None
    assert zzz.horizon == UNKNOWN
    assert zzz.risk_tier == UNKNOWN
    assert zzz.evaluable is False
    assert zzz.score is None
    assert zzz.is_tradable is False  # unknown tradability is NOT tradable


def test_unknown_symbol_returns_unknown_stub(universe: AssetUniverse) -> None:
    assert universe.get("FOO/USDT") is None
    stub = universe.get_or_unknown("FOO/USDT")
    assert stub.symbol == "FOO"
    assert stub.evaluable is False
    assert stub.score is None
    assert stub.is_tradable is False


def test_equity_is_not_short_term_tradable(universe: AssetUniverse) -> None:
    """NVDA is research-only on the crypto venue (tradable=false)."""
    nvda = universe.get("NVDA")
    assert nvda is not None
    assert nvda.category == "equity"
    assert nvda.is_tradable is False
    assert nvda not in universe.tradable_short_term()


def test_tradable_short_term_pool(universe: AssetUniverse) -> None:
    pool = {m.symbol for m in universe.tradable_short_term()}
    # SOL is tradable + short_term + evaluable; BTC is reserve; NVDA not tradable;
    # ZZZ not evaluable.
    assert pool == {"SOL"}


def test_limits_loaded_from_overlay(universe: AssetUniverse) -> None:
    assert universe.limits.max_single_asset_pct == 20.0
    assert universe.limits.max_btc_eth_short_term_pct == 35.0


def test_missing_files_degrade_gracefully(tmp_path: Path) -> None:
    """No watchlist + no overlay → empty universe, no crash, default limits."""
    u = AssetUniverse.load(
        watchlist_path=tmp_path / "nope.yml",
        overlay_path=tmp_path / "nope.yaml",
    )
    assert u.all() == []
    assert isinstance(u.limits, UniverseLimits)
    assert u.limits.max_single_asset_pct == UniverseLimits().max_single_asset_pct


def test_default_universe_loads_real_config() -> None:
    """The shipped config/asset_universe.yaml + watchlist load and score BTC."""
    u = get_asset_universe(reload=True)
    btc = u.get("BTC")
    assert btc is not None
    assert btc.is_reserve is True
    # Real config must yield a non-empty diversified short-term pool.
    pool = u.tradable_short_term()
    assert len(pool) >= 4
    assert "BTC" not in {m.symbol for m in pool}  # majors are reserve, not the pool
