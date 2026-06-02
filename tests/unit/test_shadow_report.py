"""Phase-B shadow report + root-cause classification (pure, synthetic rows)."""

from __future__ import annotations

from app.observability.shadow_candidate_ledger import (
    CLASS_ADVERSE,
    CLASS_INSUFFICIENT,
    CLASS_PROFIT_NOT_HARVESTED,
    CLASS_TP_UNREACHABLE,
    build_shadow_report,
)


def _row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "ETH/USDT",
        "side": "long",
        "regime": "chop_quiet/vol_low",
        "stop_dist_bps": 72.0,
        "take_dist_bps": 125.0,
        "gate_would_reject": False,
        "fwd_60s_bps": 5.0,
        "fwd_300s_bps": 10.0,
        "fwd_900s_bps": -20.0,
        "fwd_3600s_bps": -40.0,
        "mae_bps": -80.0,
        "mfe_bps": 85.0,
        "mfe_before_mae": True,
        "reached_take": False,
        "reached_stop": True,
    }
    base.update(over)
    return base


def test_empty_report_is_insufficient() -> None:
    rep = build_shadow_report([], total_candidates=0)
    assert rep["n_resolved"] == 0
    assert rep["primary_class"] == CLASS_INSUFFICIENT
    assert rep["resolution_coverage_pct"] == 0.0


def test_coverage_and_pending_counts() -> None:
    rep = build_shadow_report([_row(), _row()], total_candidates=10)
    assert rep["n_resolved"] == 2
    assert rep["pending"] == 8
    assert rep["resolution_coverage_pct"] == 20.0


def test_classify_profit_not_harvested() -> None:
    # favourable run real (mfe 85 >= 0.5*take 125), mfe before mae, TP rarely hit,
    # late forward return negative → profit given back.
    rows = [_row() for _ in range(25)]
    rep = build_shadow_report(rows)
    assert rep["primary_class"] == CLASS_PROFIT_NOT_HARVESTED
    assert rep["reached_take_rate"] == 0.0
    assert rep["reached_stop_rate"] == 1.0


def test_classify_tp_unreachable() -> None:
    # small favourable run (< 0.5*take so PROFIT_NOT_HARVESTED is skipped), TP
    # rarely reached, stop hit often, mfe < take → TP set too far.
    rows = [
        _row(mfe_bps=40.0, mfe_before_mae=True, fwd_300s_bps=5.0, fwd_3600s_bps=5.0)
        for _ in range(25)
    ]
    rep = build_shadow_report(rows)
    assert rep["primary_class"] == CLASS_TP_UNREACHABLE


def test_classify_adverse_selection() -> None:
    # adverse comes first (mfe_before_mae False), tiny MFE, early forward negative.
    rows = [
        _row(
            mfe_bps=5.0,
            mae_bps=-120.0,
            mfe_before_mae=False,
            fwd_60s_bps=-30.0,
            fwd_300s_bps=-50.0,
            fwd_3600s_bps=-70.0,
            reached_take=False,
            reached_stop=True,
        )
        for _ in range(25)
    ]
    rep = build_shadow_report(rows)
    assert rep["primary_class"] == CLASS_ADVERSE


def test_splits_present_and_regime_bucketed() -> None:
    rows = [_row(symbol="BTC/USDT", side="long") for _ in range(3)]
    rows += [_row(symbol="ETH/USDT", side="short") for _ in range(2)]
    rep = build_shadow_report(rows)
    by_symbol = rep["by_symbol"]
    assert isinstance(by_symbol, dict)
    assert by_symbol["BTC/USDT"]["count"] == 3
    assert by_symbol["ETH/USDT"]["count"] == 2
    assert set(rep["by_side"]) == {"long", "short"}  # type: ignore[arg-type]
