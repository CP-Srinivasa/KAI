"""V1: canary-probe attribution must not pollute the real-edge shadow report."""

from __future__ import annotations

from app.observability.shadow_candidate_ledger import (
    CLASS_ADVERSE,
    build_shadow_report,
)


def _real_row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "ETH/USDT",
        "side": "long",
        "regime": "chop_quiet/vol_low",
        "source": "autonomous_loop",
        "stop_dist_bps": 72.0,
        "take_dist_bps": 125.0,
        "gate_would_reject": False,
        "fwd_60s_bps": -5.0,
        "fwd_300s_bps": -12.0,
        "fwd_900s_bps": -20.0,
        "fwd_3600s_bps": -40.0,
        "mae_bps": -90.0,
        "mfe_bps": 5.0,
        "mfe_before_mae": False,
        "reached_take": False,
        "reached_stop": True,
    }
    base.update(over)
    return base


def _canary_row(**over: object) -> dict[str, object]:
    # A constant-confidence probe that, if counted, would look "fine".
    return _real_row(
        source="canary_probe",
        signal_confidence=0.85,
        mae_bps=-10.0,
        mfe_bps=200.0,
        mfe_before_mae=True,
        reached_take=True,
        reached_stop=False,
        fwd_300s_bps=50.0,
        fwd_3600s_bps=80.0,
        **over,
    )


def test_canary_excluded_from_headline_counts() -> None:
    rep = build_shadow_report([_real_row(), _real_row(), _canary_row()], total_candidates=3)
    assert rep["real_resolved"] == 2
    assert rep["canary_probe_resolved"] == 1
    assert rep["n_resolved"] == 2  # headline counts REAL only


def test_canary_does_not_flip_primary_class() -> None:
    # 25 adverse real rows -> ADVERSE; pile on canary "winners" that must NOT rescue it.
    rows = [_real_row() for _ in range(25)] + [_canary_row() for _ in range(25)]
    rep = build_shadow_report(rows, total_candidates=50)
    assert rep["primary_class"] == CLASS_ADVERSE
    assert rep["canary_probe_resolved"] == 25
    assert rep["real_resolved"] == 25


def test_by_source_split_present() -> None:
    rep = build_shadow_report([_real_row(), _canary_row()], total_candidates=2)
    by_source = rep["by_source"]
    assert isinstance(by_source, dict)
    assert "canary_probe" in by_source
    assert "autonomous_loop" in by_source


def test_missing_source_treated_as_real_backward_compat() -> None:
    # legacy rows without a source field stay in the headline (real)
    legacy = _real_row()
    del legacy["source"]
    rep = build_shadow_report([legacy, legacy], total_candidates=10)
    assert rep["n_resolved"] == 2
    assert rep["real_resolved"] == 2
    assert rep["canary_probe_resolved"] == 0
    assert rep["pending"] == 8
