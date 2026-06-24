"""Tests für die autonome Rotations-Policy (pure Entscheidung, Phase 1)."""

from __future__ import annotations

from app.core.enums import SourceStatus
from app.learning.source_lifecycle import can_transition
from app.learning.source_rotation_policy import DISABLE_AFTER_RUNS, decide_rotation


def test_pinned_is_never_rotated() -> None:
    # pinned-Flag schützt …
    d = decide_rotation(
        SourceStatus.ACTIVE,
        silent=True,
        reliability_tier="low",
        pinned=True,
        prior_flagged_runs=5,
    )
    assert d.target is None
    assert d.reason == "protected_pinned"
    # … und auch der DB-Status PINNED.
    d2 = decide_rotation(
        SourceStatus.PINNED,
        silent=False,
        reliability_tier="low",
        pinned=False,
        prior_flagged_runs=9,
    )
    assert d2.target is None


def test_active_silent_goes_silent() -> None:
    d = decide_rotation(
        SourceStatus.ACTIVE,
        silent=True,
        reliability_tier="neutral",
        pinned=False,
        prior_flagged_runs=0,
    )
    assert d.target == SourceStatus.SILENT
    assert d.reason == "auto_silence"
    assert can_transition(SourceStatus.ACTIVE, d.target)


def test_watch_tier_is_never_disabled() -> None:
    # 'watch' (z. B. eine 60-%-Quelle auf Rang 2) bleibt aktiv — nur 'low' fliegt.
    for runs in range(DISABLE_AFTER_RUNS + 3):
        d = decide_rotation(
            SourceStatus.ACTIVE,
            silent=False,
            reliability_tier="watch",
            pinned=False,
            prior_flagged_runs=runs,
        )
        assert d.target is None
        assert d.reason == "no_change"
        assert d.flagged_runs == 0


def test_low_tier_disables_only_after_sustained_runs() -> None:
    # Unter der Schwelle: kein Wechsel, Zähler steigt.
    d1 = decide_rotation(
        SourceStatus.ACTIVE,
        silent=False,
        reliability_tier="low",
        pinned=False,
        prior_flagged_runs=0,
    )
    assert d1.target is None
    assert d1.flagged_runs == 1
    assert d1.reason == f"flagged_low_1/{DISABLE_AFTER_RUNS}"

    # An der Schwelle: DISABLED.
    d_thresh = decide_rotation(
        SourceStatus.ACTIVE,
        silent=False,
        reliability_tier="low",
        pinned=False,
        prior_flagged_runs=DISABLE_AFTER_RUNS - 1,
    )
    assert d_thresh.target == SourceStatus.DISABLED
    assert d_thresh.reason == "auto_rotate_disable_sustained_low"
    assert can_transition(SourceStatus.ACTIVE, d_thresh.target)


def test_silent_source_recovers_when_producing_again() -> None:
    d = decide_rotation(
        SourceStatus.SILENT,
        silent=False,
        reliability_tier="neutral",
        pinned=False,
        prior_flagged_runs=0,
    )
    assert d.target == SourceStatus.ACTIVE
    assert d.reason == "auto_recover"
    assert can_transition(SourceStatus.SILENT, d.target)


def test_silent_source_that_is_low_does_not_recover() -> None:
    d = decide_rotation(
        SourceStatus.SILENT,
        silent=False,
        reliability_tier="low",
        pinned=False,
        prior_flagged_runs=0,
    )
    assert d.target is None


def test_disabled_is_not_auto_recovered() -> None:
    # DISABLED kann Operator-gesetzt sein → nie automatisch reaktivieren.
    d = decide_rotation(
        SourceStatus.DISABLED,
        silent=False,
        reliability_tier="neutral",
        pinned=False,
        prior_flagged_runs=0,
    )
    assert d.target is None
    assert d.reason == "no_change"


def test_already_disabled_low_source_is_idempotent_no_op() -> None:
    d = decide_rotation(
        SourceStatus.DISABLED,
        silent=False,
        reliability_tier="low",
        pinned=False,
        prior_flagged_runs=2,
    )
    assert d.target is None  # kein Re-Disable, kein Audit-Spam
