"""Unit tests for the diversification / concentration guard.

Covers: BTC/ETH short-term overweight detection, single-asset cap, sector /
correlation-group clusters, candidate reject vs allow vs limit, diversified
alternatives, unknown candidate → not_evaluable, unpriced positions excluded
(not estimated), short-term vs reserve separation, empty book.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.trading.asset_universe import AssetUniverse
from app.trading.diversification import (
    DiversificationGuard,
    PositionExposure,
    classify_position_horizon,
)

WATCHLIST_YML = """
crypto:
  - symbol: BTC
  - symbol: ETH
  - symbol: SOL
  - symbol: LINK
  - symbol: XRP
"""

OVERLAY_YML = """
version: 1
limits:
  max_single_asset_pct: 25.0
  max_btc_eth_short_term_pct: 40.0
  max_sector_pct: 45.0
  max_narrative_pct: 45.0
  max_correlation_group_pct: 50.0
assets:
  BTC:
    horizon: short_term
    sector: store_of_value
    narrative: digital_gold
    risk_tier: medium
    liquidity_tier: very_high
    volatility_tier: medium
    data_quality: high
    tradable: true
    correlation_group: btc_beta
  ETH:
    horizon: short_term
    sector: smart_contract_l1
    narrative: programmable_settlement
    risk_tier: medium
    liquidity_tier: very_high
    volatility_tier: high
    data_quality: high
    tradable: true
    correlation_group: eth_beta
  SOL:
    horizon: short_term
    sector: smart_contract_l1
    narrative: high_throughput_l1
    risk_tier: high
    liquidity_tier: high
    volatility_tier: high
    data_quality: high
    tradable: true
    correlation_group: l1_alts
  LINK:
    horizon: short_term
    sector: oracle_infra
    narrative: data_oracle
    risk_tier: high
    liquidity_tier: high
    volatility_tier: high
    data_quality: high
    tradable: true
    correlation_group: defi_infra
  XRP:
    horizon: short_term
    sector: payments
    narrative: cross_border
    risk_tier: high
    liquidity_tier: high
    volatility_tier: high
    data_quality: high
    tradable: true
    correlation_group: payments_alts
"""


@pytest.fixture()
def guard(tmp_path: Path) -> DiversificationGuard:
    wl = tmp_path / "watchlists.yml"
    wl.write_text(WATCHLIST_YML, encoding="utf-8")
    ov = tmp_path / "asset_universe.yaml"
    ov.write_text(OVERLAY_YML, encoding="utf-8")
    universe = AssetUniverse.load(watchlist_path=wl, overlay_path=ov)
    return DiversificationGuard(universe=universe, mode="shadow")


def _btc_eth_heavy() -> list[PositionExposure]:
    return [
        PositionExposure("BTC/USDT", 6000.0, "cost"),
        PositionExposure("ETH/USDT", 4000.0, "cost"),
        PositionExposure("SOL/USDT", 500.0, "cost"),
    ]


def test_detects_btc_eth_overweight(guard: DiversificationGuard) -> None:
    report = guard.analyze_portfolio(_btc_eth_heavy())
    assert report.btc_eth_short_term_pct is not None
    assert report.btc_eth_short_term_pct > 90
    assert any("btc_eth_short_term_overweight" in w for w in report.warnings)


def test_detects_single_asset_and_cluster(guard: DiversificationGuard) -> None:
    report = guard.analyze_portfolio(_btc_eth_heavy())
    over = {(b.dimension, b.key) for b in report.over_limit_buckets()}
    assert ("asset", "BTC") in over
    assert ("correlation_group", "btc_beta") in over


def test_reject_more_btc_with_alternatives(guard: DiversificationGuard) -> None:
    d = guard.evaluate_candidate(
        _btc_eth_heavy(), candidate_symbol="BTC/USDT", notional_usd=2000.0
    )
    assert d.action == "reject"
    assert d.projected_btc_eth_pct is not None and d.projected_btc_eth_pct > 90
    alt_symbols = {a.symbol for a in d.alternatives}
    # alternatives must NOT be BTC/ETH and must come from a different group
    assert alt_symbols  # non-empty
    assert "BTC" not in alt_symbols and "ETH" not in alt_symbols


def test_allow_diversifying_candidate(guard: DiversificationGuard) -> None:
    d = guard.evaluate_candidate(
        _btc_eth_heavy(), candidate_symbol="LINK/USDT", notional_usd=500.0
    )
    assert d.action == "allow"
    assert not d.breached


def test_unknown_candidate_not_evaluable(guard: DiversificationGuard) -> None:
    d = guard.evaluate_candidate(
        _btc_eth_heavy(), candidate_symbol="FOO/USDT", notional_usd=500.0
    )
    assert d.action == "not_evaluable"
    assert not d.blocks


def test_missing_notional_not_evaluable(guard: DiversificationGuard) -> None:
    d = guard.evaluate_candidate(
        _btc_eth_heavy(), candidate_symbol="LINK/USDT", notional_usd=None
    )
    assert d.action == "not_evaluable"


def test_unpriced_positions_excluded_not_estimated(guard: DiversificationGuard) -> None:
    pos = [
        PositionExposure("BTC/USDT", 1000.0, "cost"),
        PositionExposure("SOL/USDT", None, "none"),  # unpriced
    ]
    report = guard.analyze_portfolio(pos)
    assert report.unpriced_position_count == 1
    assert report.priced_position_count == 1
    # SOL contributes nothing to the math (not estimated)
    assert any("unpriced_positions" in w for w in report.warnings)


def test_empty_book_is_not_evaluable(guard: DiversificationGuard) -> None:
    report = guard.analyze_portfolio([])
    assert report.evaluable is False
    assert report.short_term_gross_usd == 0.0


def test_reserve_positions_excluded_from_short_term_caps(guard: DiversificationGuard) -> None:
    """A position tagged reserve via source is split out of the short-term sleeve."""
    pos = [
        PositionExposure("BTC/USDT", 9000.0, "cost", source="long_term_reserve"),
        PositionExposure("SOL/USDT", 1000.0, "cost", source="cron"),
    ]
    report = guard.analyze_portfolio(pos)
    assert report.reserve_gross_usd == 9000.0
    assert report.short_term_gross_usd == 1000.0
    # BTC is in the reserve sleeve → no BTC short-term cluster
    over = {(b.dimension, b.key) for b in report.over_limit_buckets()}
    assert ("asset", "BTC") not in over


def test_classify_position_horizon() -> None:
    assert (
        classify_position_horizon(source="cron", asset_horizon="long_term_reserve")
        == "short_term"
    )
    assert (
        classify_position_horizon(source="reserve_alloc", asset_horizon="short_term")
        == "long_term_reserve"
    )
    assert classify_position_horizon(source="", asset_horizon="unknown") == "short_term"


def test_enforce_mode_blocks(tmp_path: Path) -> None:
    wl = tmp_path / "watchlists.yml"
    wl.write_text(WATCHLIST_YML, encoding="utf-8")
    ov = tmp_path / "asset_universe.yaml"
    ov.write_text(OVERLAY_YML, encoding="utf-8")
    universe = AssetUniverse.load(watchlist_path=wl, overlay_path=ov)
    enforce_guard = DiversificationGuard(universe=universe, mode="enforce")
    d = enforce_guard.evaluate_candidate(
        _btc_eth_heavy(), candidate_symbol="BTC/USDT", notional_usd=2000.0
    )
    assert d.action == "reject"
    assert d.enforced is True
    assert d.blocks is True


def test_shadow_mode_never_blocks(guard: DiversificationGuard) -> None:
    d = guard.evaluate_candidate(
        _btc_eth_heavy(), candidate_symbol="BTC/USDT", notional_usd=2000.0
    )
    assert d.action == "reject"
    assert d.enforced is False
    assert d.blocks is False
