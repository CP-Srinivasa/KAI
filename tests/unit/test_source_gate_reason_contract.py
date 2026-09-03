"""STAB-2026-09-01 §4/§7/§9 — a source gate verdict must carry its own reason.

The producers emitted three raw booleans (``n_threshold_met``,
``wilson_low_threshold_met``, ``passes_gate``) and the dashboard re-derived a human
reason from them in TypeScript. The reason was therefore not part of the truth
contract, and the ``(False, False)`` corner is genuinely ambiguous in live data:
per-source stability shows bitcoin_magazine, coindesk, cointelegraph, cryptoslate,
decrypt, newsbtc, theblock and youtube with ``resolved: 0``, next to cryptobriefing
with ``resolved: 5``. "No evidence yet" and "thin evidence" rendered identically,
and both rendered like failure.

§7 requires the distinction to reach the surface, and §9 requires
INSUFFICIENT_HISTORY never to be shown as UNSTABLE.
"""

from __future__ import annotations

import pytest

from app.alerts.hold_metrics import (
    GATE_IMMATURE_REASONS,
    GATE_INSUFFICIENT_N,
    GATE_LOW_WILSON,
    GATE_NO_HARD_OUTCOMES,
    GATE_NO_RESOLVED_OUTCOMES,
    GATE_PASS,
    GATE_SOURCE_DISABLED,
    GATE_WINDOW_INCOMPLETE,
    classify_gate_reason,
)

MIN_N = 30
MIN_W = 65.0


def _reason(**kw) -> str:
    base = {"resolved": 0, "ci_low_pct": None, "min_n": MIN_N, "min_wilson_pct": MIN_W}
    base.update(kw)
    return classify_gate_reason(**base)


# --------------------------------------------------------------------------
# The four states the live data actually demands
# --------------------------------------------------------------------------
def test_no_resolved_outcomes_is_not_thin_evidence() -> None:
    """The eight sources sitting at resolved=0 are unmeasured, not weak."""
    assert _reason(resolved=0) == GATE_NO_RESOLVED_OUTCOMES


def test_thin_evidence_is_insufficient_n() -> None:
    """cryptobriefing at n=5 is a different statement from n=0."""
    assert _reason(resolved=5, ci_low_pct=23.0) == GATE_INSUFFICIENT_N


def test_mature_sample_below_the_bound_is_low_wilson() -> None:
    """NEGATIVE CONTROL: a matured source that underperformed is NOT immature.

    This is the misclassification that matters — tradingview_webhook carries
    n=2570 and must never be excused as "not enough data".
    """
    reason = _reason(resolved=2570, ci_low_pct=57.2)
    assert reason == GATE_LOW_WILSON
    assert reason not in GATE_IMMATURE_REASONS


def test_a_passing_source_passes() -> None:
    assert _reason(resolved=2570, ci_low_pct=57.2, min_wilson_pct=55.0) == GATE_PASS


def test_resolved_without_a_bound_is_no_hard_outcomes() -> None:
    assert _reason(resolved=7, ci_low_pct=None) == GATE_NO_HARD_OUTCOMES


def test_an_incomplete_window_is_not_a_failure() -> None:
    assert _reason(resolved=0, window_complete=False) == GATE_WINDOW_INCOMPLETE


def test_a_disabled_source_is_named_as_such() -> None:
    assert _reason(resolved=100, ci_low_pct=90.0, source_enabled=False) == GATE_SOURCE_DISABLED


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("resolved", "ci_low"),
    [(0, None), (1, 0.0), (29, 100.0), (30, 64.9), (30, 65.0), (5000, 12.3)],
)
def test_pass_never_coexists_with_a_failed_boolean(resolved: int, ci_low: float | None) -> None:
    """PASS must be equivalent to (n_ok AND wilson_ok) — no drift, ever."""
    n_ok = resolved >= MIN_N
    wilson_ok = ci_low is not None and ci_low >= MIN_W
    reason = _reason(resolved=resolved, ci_low_pct=ci_low)
    assert (reason == GATE_PASS) == (n_ok and wilson_ok)


def test_immature_set_excludes_measured_failure() -> None:
    """§7: only genuine immaturity is INFO. A measured miss stays a miss."""
    assert GATE_LOW_WILSON not in GATE_IMMATURE_REASONS
    assert GATE_PASS not in GATE_IMMATURE_REASONS
    assert GATE_INSUFFICIENT_N in GATE_IMMATURE_REASONS
    assert GATE_NO_RESOLVED_OUTCOMES in GATE_IMMATURE_REASONS


def test_every_reason_is_a_declared_value() -> None:
    """No blank, no free text — SOURCE_GATE_FAILURE_REASON_MISSING = 0."""
    declared = {
        GATE_PASS,
        GATE_INSUFFICIENT_N,
        GATE_LOW_WILSON,
        GATE_NO_HARD_OUTCOMES,
        GATE_NO_RESOLVED_OUTCOMES,
        GATE_WINDOW_INCOMPLETE,
        GATE_SOURCE_DISABLED,
    }
    for resolved in (0, 1, 5, 29, 30, 2570):
        for ci in (None, 0.0, 23.0, 57.2, 65.0, 99.9):
            for complete in (True, False):
                for enabled in (True, False):
                    reason = classify_gate_reason(
                        resolved=resolved,
                        ci_low_pct=ci,
                        min_n=MIN_N,
                        min_wilson_pct=MIN_W,
                        window_complete=complete,
                        source_enabled=enabled,
                    )
                    assert reason in declared
                    assert reason
