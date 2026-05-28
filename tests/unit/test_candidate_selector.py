"""Unit tests for the diversified candidate selector.

Covers: broadening beyond BTC/ETH, BTC/ETH scan cap, correlation-group spread
cap, concentration penalty steering away from crowded clusters, empty book,
reasons present.
"""

from __future__ import annotations

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
