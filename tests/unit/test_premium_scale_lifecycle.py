"""BUG-1 / BUG-3 / V-1 — scale reason, scale lifecycle persist, terminal stabilization.

Replays the SKYAI 2026-06-07 envelope geometry (raw entry 24800, SL 23800,
targets 24925..25295; real spot ~0.356; integer-tick factor 1e5 → entry 0.248).
"""

from __future__ import annotations

from app.execution.premium_scale_lifecycle import (
    PENDING_BAD_TICK_STAGE,
    analyze_bridge_history,
    build_scale_resolution_patch,
    decide_terminal_or_ignore,
)
from app.execution.scale_resolver import (
    SCALE_UNRESOLVED_REASON,
    classify_scale_failure,
    is_structural_scale_reason,
)

RAW_ENTRY = 24800.0
RAW_SL = 23800.0
RAW_TARGETS = [24925.0, 25050.0, 25170.0, 25295.0]
GARBAGE_SPOT = 101.94
GOOD_SPOT = 0.35609
FACTOR = 1e5


# ---- BUG-1: scale_unresolved_or_bad_price -----------------------------------


def test_bug1_raw_entry_vs_garbage_spot_is_scale_unresolved() -> None:
    # scale stayed 1.0 (detection failed on garbage spot), raw 24800 vs 101.94
    reason = classify_scale_failure(entry=RAW_ENTRY, spot=GARBAGE_SPOT, scale_factor_applied=1.0)
    assert reason == SCALE_UNRESOLVED_REASON
    assert is_structural_scale_reason(reason) is True  # terminal everywhere


def test_bug1_not_triggered_when_scale_was_applied() -> None:
    # entry already rescaled to 0.248, spot 0.356 — factor !=1.0 path
    reason = classify_scale_failure(entry=0.248, spot=GOOD_SPOT, scale_factor_applied=FACTOR)
    assert reason is None


def test_bug1_not_triggered_for_normal_already_usd_signal() -> None:
    reason = classify_scale_failure(entry=60000.0, spot=60500.0, scale_factor_applied=1.0)
    assert reason is None


# ---- BUG-3: scale lifecycle persistence -------------------------------------


def test_bug3_patch_clears_scale_unknown_and_persists_resolved() -> None:
    patch = build_scale_resolution_patch(
        scale_factor=FACTOR,
        scaled_entry=0.248,
        scaled_stop_loss=0.238,
        scaled_targets=[0.24925, 0.2505, 0.2517, 0.25295],
    )
    assert patch["scale_unknown"] is False
    assert patch["scale_factor"] == FACTOR
    assert patch["scaled_entry"] == 0.248
    assert patch["scaled_stop_loss"] == 0.238
    assert patch["scaled_targets"][0] == 0.24925
    assert "scale_resolved_at" in patch
    assert patch["scale_source"]


def test_bug3_no_patch_when_factor_is_one() -> None:
    assert (
        build_scale_resolution_patch(
            scale_factor=1.0,
            scaled_entry=24800.0,
            scaled_stop_loss=23800.0,
            scaled_targets=RAW_TARGETS,
        )
        == {}
    )


def test_bug3_envelope_scale_unknown_flips_false_after_resolution() -> None:
    envelope_payload = {"scale_unknown": True, "entry_value": RAW_ENTRY}
    patch = build_scale_resolution_patch(
        scale_factor=FACTOR, scaled_entry=0.248, scaled_stop_loss=0.238, scaled_targets=[0.24925]
    )
    envelope_payload.update(patch)
    assert envelope_payload["scale_unknown"] is False
    assert envelope_payload["scaled_entry"] == 0.248


# ---- V-1: terminal stabilization --------------------------------------------


def test_v1_single_bad_tick_after_valid_pending_is_ignored() -> None:
    decision = decide_terminal_or_ignore(prior_consecutive_bad=0, had_prior_valid_pending=True)
    assert decision.action == "ignore"
    assert decision.consecutive_bad == 1


def test_v1_terminates_after_n_consecutive_bad_ticks() -> None:
    decision = decide_terminal_or_ignore(
        prior_consecutive_bad=2, had_prior_valid_pending=True, threshold=3
    )
    assert decision.action == "terminate"
    assert decision.consecutive_bad == 3


def test_v1_structural_from_first_tick_terminates_immediately() -> None:
    decision = decide_terminal_or_ignore(prior_consecutive_bad=0, had_prior_valid_pending=False)
    assert decision.action == "terminate"


def test_v1_analyze_skyai_history_protects_pending() -> None:
    # SKYAI replay order: good pending (0.356, scaled) -> no_market_data ->
    # good pending (0.356) -> [current = garbage rejected_scale_review]
    history = [
        {"stage": "pending", "reason": "price_outside_tolerance"},
        {"stage": "pending", "reason": "no_market_data"},
        {"stage": "pending", "reason": "price_outside_tolerance"},
    ]
    prior_bad, had_valid = analyze_bridge_history(history)
    assert had_valid is True
    assert prior_bad == 0
    decision = decide_terminal_or_ignore(
        prior_consecutive_bad=prior_bad, had_prior_valid_pending=had_valid
    )
    assert decision.action == "ignore"  # garbage tick must NOT terminate


def test_v1_history_counts_consecutive_bad_after_pending() -> None:
    history = [
        {"stage": "pending", "reason": "price_outside_tolerance"},
        {"stage": "rejected_scale_review", "reason": SCALE_UNRESOLVED_REASON},
        {"stage": PENDING_BAD_TICK_STAGE, "reason": SCALE_UNRESOLVED_REASON},
    ]
    prior_bad, had_valid = analyze_bridge_history(history)
    assert had_valid is True
    assert prior_bad == 2
