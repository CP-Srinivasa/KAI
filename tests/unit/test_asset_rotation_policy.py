"""Tests for the asset lifecycle FSM + rotation policy (pure logic).

FSM: only structurally-possible jumps are allowed. Policy: pinned protected,
healthy promotes/recovers, weak rotates only after sustained hysteresis, a single
bad window never rotates, insufficient holds.
"""

from __future__ import annotations

import pytest

from app.learning.asset_lifecycle import AssetStatus, can_transition, next_status
from app.learning.asset_performance_score import AssetVerdict
from app.learning.asset_rotation_policy import (
    ARCHIVE_AFTER_RUNS,
    FLAG_AFTER_RUNS,
    decide_asset_rotation,
)


def _verdict(*, healthy: bool = False, weak: bool = False, sufficient: bool = True) -> AssetVerdict:
    return AssetVerdict(
        symbol="A/USDT",
        closes=10,
        sufficient=sufficient,
        net_pnl_usd=1.0 if healthy else -1.0,
        pnl_positive=healthy,
        wilson_lb=0.6 if healthy else 0.2,
        wilson_ok=healthy,
        healthy=healthy,
        weak=weak,
    )


class TestFSM:
    def test_idempotent_allowed(self) -> None:
        assert can_transition(AssetStatus.ACTIVE, AssetStatus.ACTIVE) is True

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            (AssetStatus.CANDIDATE, AssetStatus.PROBATION),
            (AssetStatus.PROBATION, AssetStatus.ACTIVE),
            (AssetStatus.ACTIVE, AssetStatus.ROTATION_FLAGGED),
            (AssetStatus.ACTIVE, AssetStatus.PINNED),
            (AssetStatus.ROTATION_FLAGGED, AssetStatus.ACTIVE),
            (AssetStatus.ROTATION_FLAGGED, AssetStatus.ARCHIVED),
            (AssetStatus.PINNED, AssetStatus.ACTIVE),
            (AssetStatus.ARCHIVED, AssetStatus.CANDIDATE),
        ],
    )
    def test_legal_moves(self, a: AssetStatus, b: AssetStatus) -> None:
        assert can_transition(a, b) is True

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            (AssetStatus.CANDIDATE, AssetStatus.ACTIVE),  # must pass via probation
            (AssetStatus.ACTIVE, AssetStatus.CANDIDATE),
            (AssetStatus.PINNED, AssetStatus.ARCHIVED),
            (AssetStatus.ARCHIVED, AssetStatus.ACTIVE),
        ],
    )
    def test_illegal_moves(self, a: AssetStatus, b: AssetStatus) -> None:
        assert can_transition(a, b) is False

    def test_next_status_raises_on_illegal(self) -> None:
        with pytest.raises(ValueError):
            next_status(AssetStatus.CANDIDATE, AssetStatus.ACTIVE)


class TestRotationPolicy:
    def test_pinned_is_protected(self) -> None:
        d = decide_asset_rotation(
            AssetStatus.ACTIVE, _verdict(weak=True), pinned=True, prior_flagged_runs=9
        )
        assert d.target is None
        assert d.reason == "protected_pinned"

    def test_pinned_state_protected(self) -> None:
        d = decide_asset_rotation(
            AssetStatus.PINNED, _verdict(weak=True), pinned=False, prior_flagged_runs=9
        )
        assert d.target is None

    def test_probation_healthy_promotes(self) -> None:
        d = decide_asset_rotation(
            AssetStatus.PROBATION, _verdict(healthy=True), pinned=False, prior_flagged_runs=0
        )
        assert d.target == AssetStatus.ACTIVE
        assert d.reason == "promote_healthy"
        assert d.flagged_runs == 0

    def test_flagged_healthy_recovers(self) -> None:
        d = decide_asset_rotation(
            AssetStatus.ROTATION_FLAGGED,
            _verdict(healthy=True),
            pinned=False,
            prior_flagged_runs=2,
        )
        assert d.target == AssetStatus.ACTIVE
        assert d.flagged_runs == 0

    def test_active_healthy_holds_and_resets(self) -> None:
        d = decide_asset_rotation(
            AssetStatus.ACTIVE, _verdict(healthy=True), pinned=False, prior_flagged_runs=1
        )
        assert d.target is None
        assert d.flagged_runs == 0

    def test_single_weak_window_does_not_rotate(self) -> None:
        d = decide_asset_rotation(
            AssetStatus.ACTIVE, _verdict(weak=True), pinned=False, prior_flagged_runs=0
        )
        assert d.target is None
        assert d.flagged_runs == 1
        assert d.flagged_runs < FLAG_AFTER_RUNS

    def test_sustained_weak_flags_active(self) -> None:
        d = decide_asset_rotation(
            AssetStatus.ACTIVE,
            _verdict(weak=True),
            pinned=False,
            prior_flagged_runs=FLAG_AFTER_RUNS - 1,
        )
        assert d.target == AssetStatus.ROTATION_FLAGGED
        assert d.reason == "flag_sustained_weak"

    def test_sustained_weak_archives_flagged(self) -> None:
        d = decide_asset_rotation(
            AssetStatus.ROTATION_FLAGGED,
            _verdict(weak=True),
            pinned=False,
            prior_flagged_runs=ARCHIVE_AFTER_RUNS - 1,
        )
        assert d.target == AssetStatus.ARCHIVED
        assert d.reason == "rotate_archive_sustained_weak"

    def test_insufficient_holds_counter_unchanged(self) -> None:
        d = decide_asset_rotation(
            AssetStatus.ACTIVE,
            _verdict(sufficient=False),
            pinned=False,
            prior_flagged_runs=1,
        )
        assert d.target is None
        assert d.reason == "insufficient_hold"
        assert d.flagged_runs == 1


# ── B3 (Plan 08-08, PR-2): Hysterese zählt Evidenz, nicht Timer-Ticks ────────
# Der Shadow-Lauf rechnet jeden Tick über DIESELBEN letzten 200 Closes: ohne
# neue Trades kletterte flagged_runs pro Kalendertag und archivierte Symbole
# nach 3 ruhigen Tagen auf Basis von null neuer Evidenz. archived war zudem
# eine Einbahnstraße (kein Pfad zurück).


def _weak_verdict():
    from app.learning.asset_performance_score import AssetVerdict

    return AssetVerdict(
        symbol="X/USDT",
        closes=8,
        sufficient=True,
        net_pnl_usd=-10.0,
        pnl_positive=False,
        wilson_lb=0.1,
        wilson_ok=False,
        healthy=False,
        weak=True,
    )


def _healthy_verdict():
    from app.learning.asset_performance_score import AssetVerdict

    return AssetVerdict(
        symbol="X/USDT",
        closes=8,
        sufficient=True,
        net_pnl_usd=25.0,
        pnl_positive=True,
        wilson_lb=0.8,
        wilson_ok=True,
        healthy=True,
        weak=False,
    )


def test_weak_without_new_evidence_freezes_counter() -> None:
    from app.learning.asset_lifecycle import AssetStatus
    from app.learning.asset_rotation_policy import decide_asset_rotation

    d = decide_asset_rotation(
        AssetStatus.ACTIVE,
        _weak_verdict(),
        pinned=False,
        prior_flagged_runs=1,
        has_new_evidence=False,
    )
    assert d.target is None
    assert d.flagged_runs == 1  # eingefroren, NICHT inkrementiert
    assert d.reason == "weak_no_new_evidence_hold"


def test_weak_with_new_evidence_still_increments() -> None:
    from app.learning.asset_lifecycle import AssetStatus
    from app.learning.asset_rotation_policy import decide_asset_rotation

    d = decide_asset_rotation(
        AssetStatus.ACTIVE,
        _weak_verdict(),
        pinned=False,
        prior_flagged_runs=1,
        has_new_evidence=True,
    )
    assert d.target is AssetStatus.ROTATION_FLAGGED
    assert d.flagged_runs == 2


def test_archived_with_healthy_verdict_proposes_candidate_rearm() -> None:
    from app.learning.asset_lifecycle import AssetStatus
    from app.learning.asset_rotation_policy import decide_asset_rotation

    d = decide_asset_rotation(
        AssetStatus.ARCHIVED,
        _healthy_verdict(),
        pinned=False,
        prior_flagged_runs=3,
        has_new_evidence=True,
    )
    assert d.target is AssetStatus.CANDIDATE
    assert d.reason == "rearm_healthy"
    assert d.flagged_runs == 0
