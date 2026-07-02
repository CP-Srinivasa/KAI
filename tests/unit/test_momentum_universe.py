"""Tests for momentum_universe — pure universe ranking (no I/O).

Covers behavior, not implementation: ordering by combined score, top-N
truncation, bounded [0,1] scores, outlier robustness (percentile not raw
magnitude), deterministic tie-break, missing-window renormalization, NaN/Inf
sanitization, and single-candidate midpoint scoring.
"""

from __future__ import annotations

import math

import pytest

from app.observability.momentum_universe import (
    RankedSymbol,
    UniverseCandidate,
    rank_universe,
)


def _c(sym: str, turnover: float, **windows: float) -> UniverseCandidate:
    return UniverseCandidate(symbol=sym, turnover_24h=turnover, window_returns_pct=dict(windows))


class TestRankUniverse:
    def test_empty_returns_empty(self) -> None:
        assert rank_universe([], top_n=5) == []

    def test_orders_by_combined_score_desc(self) -> None:
        cands = [
            _c("WORST/USDT", 1_000, **{"24h": -5.0, "7d": -10.0}),
            _c("BEST/USDT", 100_000, **{"24h": 8.0, "7d": 20.0}),
            _c("MID/USDT", 10_000, **{"24h": 1.0, "7d": 2.0}),
        ]
        ranked = rank_universe(cands, top_n=3)
        assert [r.symbol for r in ranked] == ["BEST/USDT", "MID/USDT", "WORST/USDT"]
        assert [r.rank for r in ranked] == [1, 2, 3]
        assert all(isinstance(r, RankedSymbol) for r in ranked)

    def test_top_n_truncates(self) -> None:
        cands = [_c(f"S{i}/USDT", i * 1000, **{"24h": float(i)}) for i in range(1, 6)]
        ranked = rank_universe(cands, top_n=2)
        assert [r.symbol for r in ranked] == ["S5/USDT", "S4/USDT"]

    def test_scores_bounded_0_1(self) -> None:
        cands = [_c("A/USDT", 5, **{"24h": 1.0}), _c("B/USDT", 10, **{"24h": 2.0})]
        for r in rank_universe(cands, top_n=2):
            assert 0.0 <= r.volume_score <= 1.0
            assert 0.0 <= r.momentum_score <= 1.0
            assert 0.0 <= r.universe_score <= 1.0

    def test_outlier_turnover_does_not_dominate(self) -> None:
        # B has astronomically high turnover but the worst momentum. With the
        # momentum-weighted default, the percentile-normalized volume must NOT
        # let that single outlier top a momentum-strong coin.
        cands = [
            _c("A/USDT", 10_000, **{"24h": 9.0, "7d": 15.0}),
            _c("B/USDT", 10**12, **{"24h": -9.0, "7d": -20.0}),
        ]
        ranked = rank_universe(cands, top_n=2, volume_weight=0.4, momentum_weight=0.6)
        assert ranked[0].symbol == "A/USDT"
        # volume_score is a percentile (0.0 for the lower turnover), not the raw magnitude.
        assert ranked[0].volume_score == 0.0
        assert ranked[1].volume_score == 1.0

    def test_tie_break_is_deterministic(self) -> None:
        # Identical score + turnover → symbol ascending.
        cands = [_c("ZZZ/USDT", 5_000, **{"24h": 3.0}), _c("AAA/USDT", 5_000, **{"24h": 3.0})]
        ranked = rank_universe(cands, top_n=2)
        assert [r.symbol for r in ranked] == ["AAA/USDT", "ZZZ/USDT"]

    def test_missing_window_renormalizes(self) -> None:
        cands = [
            _c("A/USDT", 5_000, **{"24h": 1.0, "7d": 1.0}),
            _c("B/USDT", 5_000, **{"24h": 5.0}),
        ]
        ranked = rank_universe(cands, top_n=2)
        by = {r.symbol: r for r in ranked}
        # B is the strongest in the only window it has → its momentum must not be
        # penalized for the absent 7d window.
        assert by["B/USDT"].momentum_score >= by["A/USDT"].momentum_score

    def test_nan_inf_return_is_sanitized(self) -> None:
        cands = [
            _c("A/USDT", 5_000, **{"24h": float("nan"), "7d": float("inf")}),
            _c("B/USDT", 6_000, **{"24h": 2.0, "7d": 3.0}),
        ]
        ranked = rank_universe(cands, top_n=2)
        for r in ranked:
            assert math.isfinite(r.universe_score)
            assert math.isfinite(r.momentum_score)
            assert math.isfinite(r.volume_score)

    def test_nan_inf_turnover_is_sanitized(self) -> None:
        cands = [
            _c("A/USDT", float("inf"), **{"24h": 1.0}),
            _c("B/USDT", 6_000, **{"24h": 2.0}),
        ]
        ranked = rank_universe(cands, top_n=2)
        for r in ranked:
            assert math.isfinite(r.volume_score)
            assert math.isfinite(r.universe_score)

    def test_single_candidate_midpoint_scores(self) -> None:
        ranked = rank_universe([_c("A/USDT", 5_000, **{"24h": 1.0})], top_n=5)
        assert len(ranked) == 1
        assert ranked[0].rank == 1
        assert ranked[0].volume_score == 0.5
        assert ranked[0].momentum_score == 0.5
        assert ranked[0].universe_score == 0.5

    def test_components_exposed_for_transparency(self) -> None:
        ranked = rank_universe(
            [
                _c("A/USDT", 5_000, **{"24h": 1.0, "7d": 2.0}),
                _c("B/USDT", 1_000, **{"24h": -1.0, "7d": -2.0}),
            ],
            top_n=2,
        )
        comp = ranked[0].components
        assert "volume_score" in comp
        assert "momentum_score" in comp
        assert any(k.startswith("ret_") for k in comp)

    def test_invalid_top_n_raises(self) -> None:
        with pytest.raises(ValueError):
            rank_universe([_c("A/USDT", 1, **{"24h": 1.0})], top_n=0)

    def test_zero_weights_raise(self) -> None:
        with pytest.raises(ValueError):
            rank_universe(
                [_c("A/USDT", 1, **{"24h": 1.0})],
                top_n=1,
                volume_weight=0.0,
                momentum_weight=0.0,
            )

    def test_pure_momentum_weighting_ignores_volume(self) -> None:
        # volume_weight=0 → ordering driven purely by momentum percentile.
        cands = [
            _c("HIVOL_LOWMOM/USDT", 10**9, **{"24h": -3.0}),
            _c("LOVOL_HIMOM/USDT", 10, **{"24h": 7.0}),
        ]
        ranked = rank_universe(cands, top_n=2, volume_weight=0.0, momentum_weight=1.0)
        assert ranked[0].symbol == "LOVOL_HIMOM/USDT"
