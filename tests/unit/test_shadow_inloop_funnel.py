"""In-loop funnel axes for the real-generator shadow path (#175).

Pins that real_resolved=0 stays explainable:
- each terminal CycleStatus maps to the correct in-loop axis
- the funnel builds cumulative axes + a rejected_funnel breakdown
- zero-candidate, each-rejected-axis, and successful paths
- the shadow report surfaces rejected_funnel without changing primary_class
  (real_resolved=0 stays INSUFFICIENT_DATA, never EDGE_NEGATIVE)
"""

from __future__ import annotations

from app.observability.shadow_candidate_ledger import (
    CLASS_INSUFFICIENT,
    build_shadow_report,
)
from app.observability.shadow_inloop_funnel import (
    INLOOP_AXES,
    build_inloop_funnel,
    classify_cycle,
)


# --- classifier -------------------------------------------------------------


def test_classify_priority_rejected() -> None:
    assert classify_cycle("priority_rejected") == "priority_rejected"
    assert classify_cycle("CycleStatus.PRIORITY_REJECTED") == "priority_rejected"


def test_classify_generator_returned_none() -> None:
    assert classify_cycle("no_signal") == "generator_returned_none"


def test_classify_no_signal_sentiment_note() -> None:
    assert classify_cycle("no_signal", ["sentiment below threshold"]) == "sentiment_rejected"


def test_classify_no_signal_non_directional_note() -> None:
    assert classify_cycle("no_signal", ["neutral / non_directional"]) == "non_directional"


def test_classify_shadow_written() -> None:
    assert classify_cycle("entry_mode_blocked") == "shadow_candidate_written"
    assert classify_cycle("completed") == "shadow_candidate_written"


def test_classify_downstream_rejected() -> None:
    for s in ("risk_rejected", "size_rejected", "kyt_rejected", "churn_rejected"):
        assert classify_cycle(s) == "downstream_rejected"


def test_classify_no_market_data() -> None:
    assert classify_cycle("no_market_data") == "no_market_data"
    assert classify_cycle("stale_data") == "no_market_data"


def test_classify_unknown_fails_to_error() -> None:
    assert classify_cycle("some_new_status") == "error"
    assert classify_cycle("error") == "error"
    assert classify_cycle("order_failed") == "error"


# --- funnel builder ---------------------------------------------------------


def test_zero_candidates_funnel_is_all_zero() -> None:
    f = build_inloop_funnel([])
    for axis in INLOOP_AXES:
        assert f[axis] == 0, axis
    assert sum(f["rejected_funnel"].values()) == 0


def test_funnel_priority_rejected_path() -> None:
    f = build_inloop_funnel([("priority_rejected", [])])
    assert f["real_analyses_seen"] == 1
    assert f["priority_rejected"] == 1
    assert f["reached_signal_generator"] == 0
    assert f["shadow_candidate_written"] == 0
    assert f["rejected_funnel"]["priority_rejected"] == 1


def test_funnel_generator_none_reached_generator() -> None:
    f = build_inloop_funnel([("no_signal", [])])
    assert f["reached_signal_generator"] == 1
    assert f["generator_returned_none"] == 1
    assert f["shadow_candidate_written"] == 0
    assert f["rejected_funnel"]["generator_returned_none"] == 1


def test_funnel_shadow_written_success_path() -> None:
    f = build_inloop_funnel([("entry_mode_blocked", [])])
    assert f["shadow_candidate_written"] == 1
    assert f["reached_signal_generator"] == 1
    assert f["directional_accepted"] == 1
    # success terminal is NOT in the rejected funnel
    assert f["rejected_funnel"]["generator_returned_none"] == 0


def test_funnel_mixed_path_counts() -> None:
    cycles = [
        ("priority_rejected", []),
        ("no_signal", []),
        ("entry_mode_blocked", []),
        ("risk_rejected", []),
        ("no_signal", ["sentiment weak"]),
    ]
    f = build_inloop_funnel(cycles, resolver_resolved_real=1)
    assert f["real_analyses_seen"] == 5
    assert f["priority_rejected"] == 1
    assert f["sentiment_rejected"] == 1
    assert f["generator_returned_none"] == 1
    assert f["shadow_candidate_written"] == 1
    # reached generator = none + written + downstream + sentiment = 4
    assert f["reached_signal_generator"] == 4
    assert f["resolver_resolved_real"] == 1
    assert f["rejected_funnel"]["downstream_rejected"] == 1


# --- report surfacing -------------------------------------------------------


def test_report_surfaces_rejected_funnel_and_stays_insufficient() -> None:
    inloop = build_inloop_funnel([("priority_rejected", []), ("no_signal", [])])
    # no resolved real candidates → real_resolved=0
    report = build_shadow_report([], inloop_funnel=inloop)
    assert report["real_resolved"] == 0
    # the headline must stay INSUFFICIENT_DATA, never EDGE_NEGATIVE
    assert report["primary_class"] == CLASS_INSUFFICIENT
    # rejected_funnel is surfaced for explainability
    assert "rejected_funnel" in report
    assert report["rejected_funnel"]["priority_rejected"] == 1
    assert report["rejected_funnel"]["generator_returned_none"] == 1
    assert "in_loop_funnel" in report


def test_report_without_inloop_funnel_is_unchanged() -> None:
    report = build_shadow_report([])
    assert "rejected_funnel" not in report
    assert "in_loop_funnel" not in report
    assert report["primary_class"] == CLASS_INSUFFICIENT
