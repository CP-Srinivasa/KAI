"""Tests for asset_performance_score — the pure Mittelweg rotation verdict.

An asset is the "keep" verdict (healthy) when it has enough closes AND at least
one arm is positive: net-of-fee PnL > 0 OR a Wilson hit-rate >= floor. It is the
"rotate-candidate" verdict (weak) ONLY when BOTH arms fail. Below min_closes it
is insufficient — neither healthy nor weak (min-hold protects fresh assets).
"""

from __future__ import annotations

from app.learning.asset_performance_score import (
    AssetVerdict,
    AssetWindowStats,
    evaluate_asset,
)


def _stats(symbol: str, net: float, closes: int, wins: int) -> AssetWindowStats:
    return AssetWindowStats(symbol=symbol, net_pnl_usd=net, closes=closes, wins=wins)


class TestEvaluateAsset:
    def test_insufficient_closes_is_neither_healthy_nor_weak(self) -> None:
        v = evaluate_asset(_stats("A/USDT", -50.0, 2, 0), min_closes=5)
        assert v.sufficient is False
        assert v.healthy is False
        assert v.weak is False

    def test_both_arms_positive_is_healthy(self) -> None:
        # 9/10 wins → Wilson LB ~0.60 clears the 0.5 floor (8/10 would be ~0.49,
        # i.e. just under — Wilson is deliberately conservative at small n).
        v = evaluate_asset(_stats("A/USDT", 120.0, 10, 9), min_closes=5)
        assert v.pnl_positive is True
        assert v.wilson_ok is True
        assert v.healthy is True
        assert v.weak is False

    def test_pnl_positive_low_winrate_still_healthy(self) -> None:
        # A few big wins, many small losses: net > 0 but hit-rate poor → PnL arm carries.
        v = evaluate_asset(_stats("A/USDT", 80.0, 12, 3), min_closes=5)
        assert v.pnl_positive is True
        assert v.wilson_ok is False
        assert v.healthy is True
        assert v.weak is False

    def test_negative_pnl_high_winrate_still_healthy(self) -> None:
        # Many small wins, one huge loss: net < 0 but hit-rate strong → Wilson arm carries.
        v = evaluate_asset(_stats("A/USDT", -30.0, 20, 18), min_closes=5)
        assert v.pnl_positive is False
        assert v.wilson_ok is True
        assert v.healthy is True
        assert v.weak is False

    def test_both_arms_fail_is_weak(self) -> None:
        v = evaluate_asset(_stats("A/USDT", -90.0, 12, 3), min_closes=5)
        assert v.pnl_positive is False
        assert v.wilson_ok is False
        assert v.weak is True
        assert v.healthy is False

    def test_wins_clamped_to_closes(self) -> None:
        v = evaluate_asset(_stats("A/USDT", 10.0, 5, 99), min_closes=5)
        assert v.wilson_lb is not None
        assert 0.0 <= v.wilson_lb <= 1.0

    def test_nan_pnl_sanitized(self) -> None:
        v = evaluate_asset(_stats("A/USDT", float("nan"), 10, 2), min_closes=5)
        assert v.net_pnl_usd == 0.0
        assert v.pnl_positive is False

    def test_zero_closes_wilson_none(self) -> None:
        v = evaluate_asset(_stats("A/USDT", 0.0, 0, 0), min_closes=5)
        assert v.wilson_lb is None
        assert v.sufficient is False

    def test_returns_verdict_type(self) -> None:
        v = evaluate_asset(_stats("A/USDT", 1.0, 6, 4), min_closes=5)
        assert isinstance(v, AssetVerdict)
        assert v.symbol == "A/USDT"
        assert v.closes == 6

    def test_wilson_floor_is_configurable(self) -> None:
        # Borderline hit-rate: ok under a low floor, not ok under a high floor.
        lo = evaluate_asset(_stats("A/USDT", -1.0, 20, 12), min_closes=5, wilson_floor=0.3)
        hi = evaluate_asset(_stats("A/USDT", -1.0, 20, 12), min_closes=5, wilson_floor=0.8)
        assert lo.wilson_ok is True
        assert hi.wilson_ok is False


# ── B4 (Plan 08-08, PR-3): Wilson-Arm real erreichbar ────────────────────────
# Alt (min_closes=5, floor=0.5): LB(5/5)=0.566 war der EINZIGE passierbare
# Fall — der Zwei-Arm-Trigger degenerierte zum Ein-Arm net_pnl>0.


def _stats_b4(net: float, closes: int, wins: int):
    from app.learning.asset_performance_score import AssetWindowStats

    return AssetWindowStats(symbol="X/USDT", net_pnl_usd=net, closes=closes, wins=wins)


def test_wilson_arm_passable_below_100_percent() -> None:
    from app.learning.asset_performance_score import evaluate_asset

    # 6/8 bei negativem PnL: Wilson-Arm (LB 0.409 >= 0.35) rettet -> healthy.
    v = evaluate_asset(_stats_b4(net=-5.0, closes=8, wins=6))
    assert v.wilson_ok is True
    assert v.healthy is True
    assert v.weak is False


def test_five_of_eight_fails_wilson_and_negative_pnl_is_weak() -> None:
    from app.learning.asset_performance_score import evaluate_asset

    # 5/8 (LB 0.306 < 0.35) + negativer PnL: beide Arme scheitern -> weak.
    v = evaluate_asset(_stats_b4(net=-5.0, closes=8, wins=5))
    assert v.wilson_ok is False
    assert v.weak is True


def test_below_min_closes_is_insufficient() -> None:
    from app.learning.asset_performance_score import evaluate_asset

    v = evaluate_asset(_stats_b4(net=-5.0, closes=7, wins=1))
    assert v.sufficient is False
    assert v.weak is False  # Unreife ist kein Verdikt (Doktrin)
