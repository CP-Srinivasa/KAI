"""Unit tests for the aligned-evidence shadow evaluator (the V5 core)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.research.shadow_evidence_eval import (
    evaluate_signal,
    index_evidence,
    nearest_aligned,
    render,
)

_T0 = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _outcome(sym, side, offset_s, **fwd):
    return {
        "symbol": sym,
        "side": side,
        "entry_ts": _T0 + timedelta(seconds=offset_s),
        "fwd": {
            60: fwd.get("h60"),
            300: fwd.get("h300"),
            900: fwd.get("h900"),
            3600: fwd.get("h3600"),
        },
    }


def _ev(sym, side, offset_s, aligned):
    return {
        "symbol": sym,
        "direction": side,
        "ts": (_T0 + timedelta(seconds=offset_s)).isoformat(),
        "evidence_direction_aligned": aligned,
    }


def test_index_and_nearest_aligned_within_tolerance():
    idx = index_evidence([_ev("BTC/USDT", "long", 0, 1), _ev("BTC/USDT", "long", 100, -1)])
    # outcome at +20s: nearest is the +0s (dt=20) not the +100s (dt=80)
    o = _outcome("BTC/USDT", "long", 20, h60=5.0)
    assert nearest_aligned(o, idx, tol_s=300) == 1
    # outside tolerance -> None
    assert nearest_aligned(_outcome("BTC/USDT", "long", 5000, h60=5.0), idx, tol_s=300) is None
    # unknown symbol -> None
    assert nearest_aligned(_outcome("ETH/USDT", "long", 0, h60=5.0), idx, tol_s=300) is None


def test_evaluate_signal_neutral_and_missing_dropped():
    outcomes = [_outcome("BTC/USDT", "long", 0, h60=5.0)]
    # aligned==0 evidence is dropped from the join
    idx = index_evidence([_ev("BTC/USDT", "long", 0, 0)])
    res = evaluate_signal(outcomes, idx, tol_s=300)
    assert res["n_joined"] == 0


def test_evaluate_signal_subcost_stays_shadow():
    # small positive spread but below the 20bps cost floor -> not actionable
    outcomes = [_outcome("BTC/USDT", "long", i, h3600=3.0) for i in range(10)]
    idx = index_evidence([_ev("BTC/USDT", "long", i, 1) for i in range(10)])
    res = evaluate_signal(outcomes, idx, tol_s=300, cost_bps=20.0)
    assert res["actionable"] is False
    assert "SHADOW_ONLY" in res["verdict"]
    h = res["horizons"][3600]
    assert h["n_plus"] == 10
    assert h["mean_plus_bps"] == 3.0


def test_evaluate_signal_actionable_when_clears_all_bars():
    # 12 aligned+1 outcomes of +100bps across 3 symbols: clears cost, P~1.0,
    # top-symbol share = 4/12 <= 0.8 -> ACTIONABLE
    syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    outcomes = [_outcome(syms[i % 3], "long", i, h3600=100.0) for i in range(12)]
    ev = [_ev(syms[i % 3], "long", i, 1) for i in range(12)]
    # a couple of aligned-1 (disagree) outcomes for the minus cohort
    outcomes += [_outcome("XRP/USDT", "long", 1000 + i, h3600=-50.0) for i in range(3)]
    ev += [_ev("XRP/USDT", "long", 1000 + i, -1) for i in range(3)]
    idx = index_evidence(ev)
    res = evaluate_signal(outcomes, idx, tol_s=300, cost_bps=20.0, max_concentration=0.8)
    h = res["horizons"][3600]
    assert h["n_plus"] == 12
    assert h["mean_plus_bps"] == 100.0
    assert h["p_plus_positive"] == 1.0
    assert h["top_symbol_share"] <= 0.8
    assert h["actionable"] is True
    assert res["actionable"] is True


def test_evaluate_signal_concentration_blocks_monoculture():
    # all 10 on ONE symbol -> share 1.0 > 0.8 -> not actionable despite big mean
    outcomes = [_outcome("BTC/USDT", "long", i, h3600=100.0) for i in range(10)]
    idx = index_evidence([_ev("BTC/USDT", "long", i, 1) for i in range(10)])
    res = evaluate_signal(outcomes, idx, tol_s=300, cost_bps=20.0, max_concentration=0.8)
    assert res["horizons"][3600]["top_symbol_share"] == 1.0
    assert res["actionable"] is False


def test_render_contains_table_header_and_verdict():
    outcomes = [_outcome("BTC/USDT", "long", i, h3600=3.0) for i in range(10)]
    idx = index_evidence([_ev("BTC/USDT", "long", i, 1) for i in range(10)])
    res = evaluate_signal(outcomes, idx)
    out = render("funding", res)
    assert out.startswith("### funding — n_joined=10")
    assert "| horizon | n+ | n- |" in out
    assert "3600s" in out
