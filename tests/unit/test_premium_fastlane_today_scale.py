"""Scale-resolution + structural/market reason classification (Goal §10/§16).

Pins the expected scale factors for the four signals against a representative
spot, the long geometry, and that market-plausibility reasons are non-structural
(so fastlane paper keeps them pending instead of terminally rejecting).
"""

from __future__ import annotations

import pytest

from app.execution.scale_resolver import (
    detect_scale_factor,
    is_structural_scale_reason,
    validate_scaled_signal,
)

# (raw_entry, spot, expected_factor, scaled_entry)
CASES = {
    "TAC": (19000.0, 0.018455, 1e6, 0.019000),
    "CLO": (16860.0, 0.15995, 1e5, 0.16860),
    "BEAT": (1.6810, 1.7451, 1.0, 1.6810),
    "4": (9440.0, 0.008983, 1e6, 0.009440),
}


@pytest.mark.parametrize("name", list(CASES))
def test_scale_factor_resolves(name: str) -> None:
    raw_entry, spot, expected_factor, scaled = CASES[name]
    factor = detect_scale_factor(raw_entry, spot)
    assert factor == expected_factor, (name, factor)
    assert raw_entry / factor == pytest.approx(scaled, rel=1e-3)


def test_long_geometry_holds_after_scale() -> None:
    # SL < Entry < TP1 < TP2 < TP3 < TP4 for each scaled signal.
    raw = {
        "TAC": (19000, 18240, [19095, 19190, 19285, 19380], 1e6),
        "CLO": (16860, 16185, [16945, 17030, 17110, 17195], 1e5),
        "BEAT": (1.6810, 1.6130, [1.6895, 1.6980, 1.7060, 1.7145], 1.0),
        "4": (9440, 9060, [9485, 9535, 9580, 9630], 1e6),
    }
    for name, (e, sl, tgts, f) in raw.items():
        entry, stop = e / f, sl / f
        ts = [t / f for t in tgts]
        ordered = [stop, entry, *ts]
        assert ordered == sorted(ordered), f"{name}: geometry not strictly increasing"


def test_market_plausibility_reasons_are_non_structural() -> None:
    for r in ("long_sl_at_or_above_spot", "short_sl_at_or_below_spot", "entry_far_from_spot"):
        assert is_structural_scale_reason(r) is False, r


def test_structural_reasons_stay_terminal() -> None:
    for r in (
        "scale_collapses_to_zero",
        "long_sl_at_or_above_entry",
        "long_targets_at_or_below_entry",
    ):
        assert is_structural_scale_reason(r) is True, r
    # an unknown reason fails closed → structural
    assert is_structural_scale_reason("something_new") is True


def test_clo_sl_above_spot_is_market_not_structural() -> None:
    # CLO scaled: entry 0.1686, SL 0.16185, spot 0.15995 → SL >= spot.
    reason = validate_scaled_signal(
        direction="long", entry=0.16860, stop_loss=0.16185, targets=[0.16945], spot=0.15995
    )
    assert reason == "long_sl_at_or_above_spot"
    assert is_structural_scale_reason(reason) is False
