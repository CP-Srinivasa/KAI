"""Tests for app.release.readiness — LiveReadinessGate / ReleaseClassification.

Covers the fail-closed contract: default postures are never live candidates,
every hard gate must pass for candidacy, negative/unproven edge and
mypy-ignored trading-core modules are hard live-blockers, and blockers are
machine-readable.
"""

from __future__ import annotations

from app.core.enums import ExecutionMode
from app.release.readiness import (
    POSSIBLE_BLOCKER_CODES,
    TRADING_CRITICAL_MODULES,
    ExecutionPosture,
    LiveReadinessEvidence,
    ReleaseClassification,
    classify_release,
    compute_net_edge_bps,
    default_ignored_mypy_modules,
)

PAPER = ExecutionPosture(
    mode=ExecutionMode.PAPER, live_enabled=False, dry_run=True, approval_required=True
)
LIVE_GUARDED = ExecutionPosture(
    mode=ExecutionMode.LIVE, live_enabled=True, dry_run=False, approval_required=True
)


def _all_pass_evidence() -> LiveReadinessEvidence:
    """Evidence where every hard gate is satisfied."""
    return LiveReadinessEvidence(
        cost_model_single_source=True,
        pnl_fee_separation=True,
        net_edge_bps=12.5,
        posterior_prob=0.62,
        churn_limits_active=True,
        remote_ci_green=True,
        out_of_sample_edge_positive=True,
        operator_live_approval=True,
        trading_core_mypy_clean=True,
    )


# --- Negative / default (fail-closed) ---------------------------------------


def test_default_paper_no_evidence_is_not_live_candidate() -> None:
    status = classify_release(PAPER)
    assert status.is_live_candidate is False
    assert status.classification is ReleaseClassification.OPERATOR_PAPER_READY
    assert status.blockers  # there must be machine-readable reasons


def test_default_paper_without_approval_is_paper_only() -> None:
    posture = ExecutionPosture(
        mode=ExecutionMode.PAPER, live_enabled=False, dry_run=True, approval_required=False
    )
    status = classify_release(posture)
    assert status.classification is ReleaseClassification.PAPER_ONLY
    assert status.is_live_candidate is False


def test_contradictory_paper_config_is_live_blocked() -> None:
    # live_enabled while mode is not LIVE == internally contradictory
    posture = ExecutionPosture(
        mode=ExecutionMode.PAPER, live_enabled=True, dry_run=True, approval_required=True
    )
    status = classify_release(posture)
    assert status.classification is ReleaseClassification.LIVE_BLOCKED
    assert any(b.code == "PAPER_CONFIG_CONTRADICTORY" for b in status.blockers)


def test_live_attempted_but_gates_fail_is_live_blocked() -> None:
    status = classify_release(LIVE_GUARDED, LiveReadinessEvidence())
    assert status.classification is ReleaseClassification.LIVE_BLOCKED
    assert status.is_live_candidate is False


def test_unproven_edge_blocks_live() -> None:
    ev = _all_pass_evidence()
    ev = LiveReadinessEvidence(**{**ev.__dict__, "net_edge_bps": None})
    status = classify_release(LIVE_GUARDED, ev)
    assert status.is_live_candidate is False
    assert any(b.code == "EDGE_UNPROVEN" for b in status.blockers)


def test_negative_edge_is_below_threshold() -> None:
    ev = LiveReadinessEvidence(**{**_all_pass_evidence().__dict__, "net_edge_bps": -3.0})
    status = classify_release(LIVE_GUARDED, ev)
    assert status.is_live_candidate is False
    assert any(b.code == "EDGE_BELOW_LIVE_THRESHOLD" for b in status.blockers)


def test_zero_edge_blocks_live() -> None:
    ev = LiveReadinessEvidence(**{**_all_pass_evidence().__dict__, "net_edge_bps": 0.0})
    status = classify_release(LIVE_GUARDED, ev)
    assert status.is_live_candidate is False
    assert any(b.code == "EDGE_BELOW_LIVE_THRESHOLD" for b in status.blockers)


def test_marginal_positive_edge_below_threshold_blocks() -> None:
    # A marginal positive edge below the required live threshold is NOT negative,
    # but still blocks (precise code, no false "negative" diagnosis).
    ev = LiveReadinessEvidence(**{**_all_pass_evidence().__dict__, "net_edge_bps": 1.0})
    status = classify_release(LIVE_GUARDED, ev, live_edge_threshold_bps=5.0)
    assert status.is_live_candidate is False
    assert any(b.code == "EDGE_BELOW_LIVE_THRESHOLD" for b in status.blockers)


def test_weak_posterior_blocks_live() -> None:
    ev = LiveReadinessEvidence(**{**_all_pass_evidence().__dict__, "posterior_prob": 0.5})
    status = classify_release(LIVE_GUARDED, ev)
    assert status.is_live_candidate is False
    assert any(b.code == "EDGE_POSTERIOR_TOO_WEAK" for b in status.blockers)


def test_missing_posterior_blocks_live() -> None:
    ev = LiveReadinessEvidence(**{**_all_pass_evidence().__dict__, "posterior_prob": None})
    status = classify_release(LIVE_GUARDED, ev)
    assert any(b.code == "EDGE_POSTERIOR_TOO_WEAK" for b in status.blockers)


def test_trading_core_mypy_ignored_blocks_live() -> None:
    ev = LiveReadinessEvidence(**{**_all_pass_evidence().__dict__, "trading_core_mypy_clean": None})
    status = classify_release(
        LIVE_GUARDED,
        ev,
        ignored_mypy_modules=["app.execution.paper_engine", "app.alerts.daily_briefing"],
    )
    assert status.is_live_candidate is False
    assert any(b.code == "MYPY_TRADING_CORE_IGNORED" for b in status.blockers)


def test_non_trading_mypy_ignored_does_not_block_on_that_gate() -> None:
    ev = LiveReadinessEvidence(**{**_all_pass_evidence().__dict__, "trading_core_mypy_clean": None})
    status = classify_release(
        LIVE_GUARDED, ev, ignored_mypy_modules=["app.alerts.daily_briefing", "app.cli.main"]
    )
    assert status.hard_gates["trading_core_mypy_clean"] is True


def test_live_mode_unguarded_blocks() -> None:
    posture = ExecutionPosture(
        mode=ExecutionMode.LIVE, live_enabled=True, dry_run=True, approval_required=True
    )  # dry_run True under LIVE is unguarded
    status = classify_release(posture, _all_pass_evidence())
    assert status.hard_gates["live_enablement_guarded"] is False
    assert status.is_live_candidate is False


# --- Positive (earned candidacy) --------------------------------------------


def test_all_gates_pass_is_live_normal_candidate() -> None:
    status = classify_release(LIVE_GUARDED, _all_pass_evidence())
    assert status.classification is ReleaseClassification.LIVE_NORMAL_CANDIDATE
    assert status.is_live_candidate is True
    assert status.blockers == ()


def test_all_gates_pass_with_limited_caps_is_limited_candidate() -> None:
    ev = LiveReadinessEvidence(**{**_all_pass_evidence().__dict__, "limited_caps_only": True})
    status = classify_release(LIVE_GUARDED, ev)
    assert status.classification is ReleaseClassification.LIVE_LIMITED_CANDIDATE
    assert status.is_live_candidate is True


# --- Edge formula -----------------------------------------------------------


def test_compute_net_edge_bps_subtracts_all_costs() -> None:
    # 30 gross - 6 fees - 4 spread - 5 slippage - 5 margin = 10
    assert compute_net_edge_bps(30.0, 6.0, 4.0, 5.0, 5.0) == 10.0


def test_low_fees_are_not_edge() -> None:
    # tiny fees but gross return below total costs -> negative net edge
    net = compute_net_edge_bps(2.0, 0.2, 1.5, 1.0, 5.0)
    assert net < 0


# --- Machine-readable output ------------------------------------------------


def test_to_dict_is_machine_readable() -> None:
    status = classify_release(LIVE_GUARDED, LiveReadinessEvidence())
    d = status.to_dict()
    assert d["classification"] == "live_blocked"
    assert d["is_live_candidate"] is False
    assert isinstance(d["active_blockers"], list)
    for b in d["active_blockers"]:
        assert set(b) == {"code", "gate", "message"}
        assert b["code"] and b["code"].isupper()
    # every active blocker code is part of the published catalogue
    assert set(d["active_blocker_codes"]) <= set(d["possible_blocker_codes"])


def test_possible_vs_active_blockers_separated() -> None:
    # Remote CI green => REMOTE_CI_NOT_GREEN must NOT be an active blocker, but it
    # stays in the possible-codes catalogue.
    ev = LiveReadinessEvidence(**{**_all_pass_evidence().__dict__, "remote_ci_green": True})
    status = classify_release(LIVE_GUARDED, ev)
    d = status.to_dict()
    assert "REMOTE_CI_NOT_GREEN" in d["possible_blocker_codes"]
    assert "REMOTE_CI_NOT_GREEN" not in d["active_blocker_codes"]


def test_possible_blocker_codes_cover_all_emitted() -> None:
    # Drive a fully-blocked posture and assert every emitted code is catalogued.
    status = classify_release(
        ExecutionPosture(
            mode=ExecutionMode.PAPER, live_enabled=True, dry_run=True, approval_required=True
        ),
        LiveReadinessEvidence(),
        ignored_mypy_modules=["app.execution.paper_engine"],
    )
    assert {b.code for b in status.blockers} <= POSSIBLE_BLOCKER_CODES


# --- Reality acceptance: current repo state is fail-closed ------------------


def test_current_repo_default_is_not_live_candidate() -> None:
    """With the real default execution posture + the real mypy ignore list,
    KAI must classify as not-live-candidate (fail-closed)."""
    from app.core.settings import ExecutionSettings

    posture = ExecutionPosture.from_settings(ExecutionSettings())
    status = classify_release(posture, ignored_mypy_modules=default_ignored_mypy_modules())
    assert status.is_live_candidate is False


def test_default_ignored_modules_reads_real_pyproject() -> None:
    mods = default_ignored_mypy_modules()
    # envelope_to_paper_bridge is a trading-critical module still ignored today
    assert "app.execution.envelope_to_paper_bridge" in mods
    assert TRADING_CRITICAL_MODULES & set(mods)  # at least one trading-core still ignored
