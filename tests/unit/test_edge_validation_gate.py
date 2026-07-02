"""Tests for the statistical edge-validation gate (Plan PR C).

Behaviour under test (kai-testing-regeln — behaviour, not implementation):
  * A strong, large, single-trial cohort is READY.
  * Each hard criterion fails the gate in isolation (sample floor, cost-net,
    DSR via trials-deflation, outlier-robustness).
  * The SAME cohort flips ready→not-ready as ``trials`` rises (DSR deflation works).
  * The real n=51 canonical size is NOT ready (n<100) — the gate refuses correctly.
  * HARD INVARIANT: the gate is NEVER imported by the entry/execution path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.observability.edge_validation_gate import (
    TrialCountUnavailableError,
    evaluate_edge_validation,
    resolve_trial_count,
)


def _crit(v, name: str) -> bool:
    return next(c.passed for c in v.criteria if c.name == name)


# --- honest trial-count resolution (Component 1) -------------------------------
# The ledger's distinct-hypothesis count is the floor for the DSR deflation; an
# explicit --trials override may only RAISE it (untracked ad-hoc searches the
# ledger never saw), never lower it — a too-low count fakes a passing DSR.


def test_trial_count_defaults_to_ledger_when_no_override() -> None:
    r = resolve_trial_count(ledger_count=42, override=None)
    assert r.trials == 42
    assert r.source == "ledger"
    assert r.clamped is False


def test_trial_count_override_above_ledger_wins() -> None:
    r = resolve_trial_count(ledger_count=42, override=100)
    assert r.trials == 100
    assert r.source == "override"
    assert r.clamped is False


def test_trial_count_override_below_ledger_is_clamped_up() -> None:
    r = resolve_trial_count(ledger_count=42, override=10)
    assert r.trials == 42  # ledger floor wins — you cannot under-report trials
    assert r.source == "ledger"
    assert r.clamped is True


def test_trial_count_empty_ledger_with_override_uses_override() -> None:
    r = resolve_trial_count(ledger_count=0, override=7)
    assert r.trials == 7
    assert r.source == "override_no_ledger"
    assert r.clamped is False


def test_trial_count_empty_ledger_no_override_fails_closed() -> None:
    # The worst case: no honest record AND no explicit count → never silently
    # default to 1 (which would maximally inflate the DSR). Refuse.
    with pytest.raises(TrialCountUnavailableError):
        resolve_trial_count(ledger_count=0, override=None)


def test_strong_single_trial_cohort_is_ready() -> None:
    # n=200, mean 30, modest spread → high Sharpe; one trial → no deflation.
    net = [25.0, 35.0] * 100
    v = evaluate_edge_validation(net, trials=1, min_n=100)
    assert v.ready is True
    assert _crit(v, "sample_floor") and _crit(v, "cost_net_positive")
    assert _crit(v, "deflated_sharpe") and _crit(v, "min_track_record")
    assert _crit(v, "outlier_robust")
    assert v.sharpe is not None and v.sharpe > 3.0


def test_below_sample_floor_is_not_ready() -> None:
    net = [25.0, 35.0] * 25  # n=50 < 100
    v = evaluate_edge_validation(net, trials=1, min_n=100)
    assert v.ready is False
    assert _crit(v, "sample_floor") is False


def test_negative_net_is_not_ready() -> None:
    net = [-5.0, -15.0] * 100  # mean -10
    v = evaluate_edge_validation(net, trials=1, min_n=100)
    assert v.ready is False
    assert _crit(v, "cost_net_positive") is False


def test_deflation_flips_ready_with_more_trials() -> None:
    # mean 2, std ~10 → modest Sharpe ~0.2, n=120.
    net = [12.0, -8.0] * 60
    ready_one = evaluate_edge_validation(net, trials=1, min_n=100)
    deflated = evaluate_edge_validation(net, trials=1000, min_n=100)
    # One trial: the modest edge clears PSR(0); many trials lift the bar (SR0) so
    # the SAME data no longer clears the Deflated Sharpe.
    assert ready_one.ready is True
    assert deflated.ready is False
    assert _crit(deflated, "deflated_sharpe") is False
    assert deflated.deflated_sharpe is not None
    assert ready_one.deflated_sharpe is not None
    assert deflated.deflated_sharpe < ready_one.deflated_sharpe


def test_outlier_carried_edge_is_not_robust() -> None:
    # 119 small losers + 1 huge winner → mean>0 but it hangs on one trade.
    net = [-1.0] * 119 + [10000.0]
    v = evaluate_edge_validation(net, trials=1, min_n=100)
    assert _crit(v, "outlier_robust") is False
    assert v.ready is False


def test_real_canonical_size_is_not_ready() -> None:
    # n=51 (the current canonical cohort size) — even if positive, n<100 → refuse.
    net = [10.0, 20.0] * 25 + [15.0]  # n=51, positive
    v = evaluate_edge_validation(net, trials=10, min_n=100)
    assert v.trade_count == 51
    assert v.ready is False
    assert _crit(v, "sample_floor") is False


def test_insufficient_sample_is_honest() -> None:
    v = evaluate_edge_validation([5.0], trials=1, min_n=100)
    assert v.ready is False
    assert v.sharpe is None


def test_gate_is_never_imported_by_entry_or_execution_path() -> None:
    """HARD INVARIANT: a LIVE-promotion gate must never touch the paper entry path
    (the paper-learning directive forbids gating paper trading)."""
    root = Path(__file__).resolve().parents[2]
    guarded = [
        root / "app" / "orchestrator" / "trading_loop.py",
        *(root / "app" / "execution").glob("*.py"),
    ]
    for f in guarded:
        if f.exists():
            assert "edge_validation_gate" not in f.read_text(encoding="utf-8"), (
                f"{f.name} must not import the edge-validation gate (it would gate paper trading)"
            )
