"""Unit tests for the diversified candidate selector.

Covers: broadening beyond BTC/ETH, BTC/ETH scan cap, correlation-group spread
cap, concentration penalty steering away from crowded clusters, empty book,
reasons present.
"""

from __future__ import annotations

from pathlib import Path

from app.trading.asset_universe import AssetUniverse
from app.trading.candidate_selector import (
    select_short_term_candidates,
    selected_symbols,
)
from app.trading.diversification import PositionExposure


def test_empty_book_broadens_beyond_btc_eth() -> None:
    rankings = select_short_term_candidates(positions=[], limit=6)
    picks = selected_symbols(rankings)
    bases = {p.split("/")[0] for p in picks}
    # The pool must contain real alts, not just majors.
    assert bases - {"BTC", "ETH"}
    # BTC/ETH are capped: at most floor(6/3)=2 of them.
    assert len({b for b in bases if b in {"BTC", "ETH"}}) <= 2


def test_btc_heavy_book_steers_away_from_btc_beta() -> None:
    pos = [
        PositionExposure("BTC/USDT", 8000.0, "cost"),
        PositionExposure("ETH/USDT", 2000.0, "cost"),
    ]
    rankings = select_short_term_candidates(positions=pos, limit=6)
    picks = selected_symbols(rankings)
    bases = {p.split("/")[0] for p in picks}
    # With the book already saturated in BTC/ETH, neither should make the cut.
    assert "BTC" not in bases
    assert "ETH" not in bases
    # And the picks should be genuinely diversified names.
    assert len(bases) >= 4


def test_correlation_group_spread_cap() -> None:
    rankings = select_short_term_candidates(positions=[], limit=8, max_same_correlation_group=1)
    included = [c for c in rankings if c.included]
    groups = [c.correlation_group for c in included if c.correlation_group != "unknown"]
    # No correlation group appears more than once when cap = 1.
    assert len(groups) == len(set(groups))


def test_every_candidate_has_reasons() -> None:
    rankings = select_short_term_candidates(positions=[], limit=6)
    assert rankings
    for c in rankings:
        assert c.reasons  # non-empty explanation for include/skip


def test_exclude_btc_eth_when_flag_off() -> None:
    rankings = select_short_term_candidates(positions=[], limit=6, include_btc_eth=False)
    bases = {c.base for c in rankings}
    assert "BTC" not in bases
    assert "ETH" not in bases


def test_quote_currency_applied() -> None:
    rankings = select_short_term_candidates(positions=[], limit=3, quote="USDC")
    for c in rankings:
        assert c.symbol.endswith("/USDC")


_FOCUS_CAP_OVERLAY = """
version: 1
limits:
  max_single_asset_pct: 100.0
  max_sector_pct: 100.0
  max_correlation_group_pct: 100.0
  max_focus_field_pct: 50.0
assets:
  SOL:
    horizon: short_term
    sector: smart_contract_l1
    focus_field: blockchain
    risk_tier: high
    liquidity_tier: high
    volatility_tier: high
    data_quality: high
    tradable: true
    correlation_group: l1_alts
  LINK:
    horizon: short_term
    sector: oracle_infra
    focus_field: blockchain
    risk_tier: high
    liquidity_tier: high
    volatility_tier: high
    data_quality: high
    tradable: true
    correlation_group: defi_infra
  XRP:
    horizon: short_term
    sector: payments
    focus_field: fintech
    risk_tier: high
    liquidity_tier: high
    volatility_tier: high
    data_quality: high
    tradable: true
    correlation_group: payments_alts
"""


def test_focus_field_over_cap_penalises_scan(tmp_path: Path) -> None:
    """S3: when a focus_field is over its configured cap, blockchain candidates
    are penalised so the scan steers toward the under-represented field."""
    wl = tmp_path / "watchlists.yml"
    wl.write_text("crypto:\n  - symbol: SOL\n  - symbol: LINK\n  - symbol: XRP\n", encoding="utf-8")
    ov = tmp_path / "asset_universe.yaml"
    ov.write_text(_FOCUS_CAP_OVERLAY, encoding="utf-8")
    universe = AssetUniverse.load(watchlist_path=wl, overlay_path=ov)

    # Book is 100% blockchain (SOL+LINK) → focus_field blockchain over the 50 cap.
    pos = [
        PositionExposure("SOL/USDT", 6000.0, "cost"),
        PositionExposure("LINK/USDT", 4000.0, "cost"),
    ]
    rankings = select_short_term_candidates(positions=pos, universe=universe, limit=3)
    by_base = {c.base: c for c in rankings}
    # blockchain names carry the over-cap penalty reason; XRP (fintech) does not.
    assert any("focus_field blockchain already over cap" in r for r in by_base["SOL"].reasons)
    assert not any("focus_field" in r for r in by_base["XRP"].reasons)
    # the fintech name should out-rank the penalised blockchain names on adjusted score.
    assert by_base["XRP"].adjusted_score > by_base["SOL"].adjusted_score
