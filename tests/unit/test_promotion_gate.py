"""Fail-closed tests for the bleed-breaker promotion gate (Blocker #4 enforcement)."""

from __future__ import annotations

from typing import Any

from app.core.enums import EntryMode
from app.risk.promotion_gate import (
    DEFAULT_BLEED_USD_THRESHOLD,
    PROMOTION_BLOCKED_MISSING_ARTIFACT,
    PROMOTION_BLOCKED_POSITION_DATA_UNKNOWN,
    PROMOTION_BLOCKED_POSITION_SOURCE_STALE,
    PROMOTION_BLOCKED_RISK_OPEN,
    PROMOTION_BLOCKED_UNREALIZED_BLEED,
    STATUS_ALLOWED,
    STATUS_MANUAL_REVIEW,
    evaluate_promotion,
    is_risk_increasing,
)


def _report(
    *,
    overall: str = "no_risk",
    positions: list[dict[str, Any]] | None = None,
    total_unrealized: float = 0.0,
    available: bool = True,
) -> dict[str, Any]:
    if positions is None:
        positions = [
            {
                "symbol": "DOT/USDT",
                "risk_status": overall if overall != "no_risk" else "no_risk",
                "market_data_available": True,
                "market_data_stale": False,
            }
        ]
    return {
        "report_type": "open_positions_risk_snapshot",
        "available": available,
        "overall_risk_status": overall,
        "total_unrealized_pnl_usd": total_unrealized,
        "positions": positions,
    }


# --- ladder / direction -----------------------------------------------------


def test_ladder_direction() -> None:
    assert is_risk_increasing(EntryMode.DISABLED, EntryMode.PAPER)
    assert is_risk_increasing(EntryMode.PAPER, EntryMode.PROBE)
    assert is_risk_increasing(EntryMode.PROBE, EntryMode.LIVE_LIMITED)
    assert not is_risk_increasing(EntryMode.PROBE, EntryMode.DISABLED)
    assert not is_risk_increasing(EntryMode.PAPER, EntryMode.PAPER)


# --- de-risking is never gated (exits/risk-reduction must always pass) -------


def test_derisking_allowed_even_with_open_bleed() -> None:
    rep = _report(overall="risk_open", total_unrealized=-73.0)
    d = evaluate_promotion(EntryMode.PROBE, EntryMode.DISABLED, rep)
    assert d.allowed is True
    assert d.status == STATUS_ALLOWED
    assert d.reason_codes == []


def test_lateral_allowed() -> None:
    d = evaluate_promotion(EntryMode.PAPER, EntryMode.PAPER, _report(overall="risk_open"))
    assert d.allowed is True


# --- risk-increasing promotions are gated -----------------------------------


def test_promotion_blocked_when_position_bleeds() -> None:
    # -120 USD: clearly past DEFAULT_BLEED_USD_THRESHOLD so both the
    # per-position and the aggregate reason code must fire
    rep = _report(overall="risk_open", total_unrealized=-120.0)
    d = evaluate_promotion(EntryMode.DISABLED, EntryMode.PAPER, rep)
    assert d.allowed is False
    assert d.status == STATUS_MANUAL_REVIEW
    assert PROMOTION_BLOCKED_RISK_OPEN in d.reason_codes
    assert PROMOTION_BLOCKED_UNREALIZED_BLEED in d.reason_codes


def test_promotion_allowed_when_flat_no_risk() -> None:
    d = evaluate_promotion(EntryMode.DISABLED, EntryMode.PAPER, _report(overall="no_risk"))
    assert d.allowed is True
    assert d.status == STATUS_ALLOWED


# --- fail-closed posture ----------------------------------------------------


def test_missing_artifact_is_fail_closed() -> None:
    d = evaluate_promotion(EntryMode.DISABLED, EntryMode.PAPER, None)
    assert d.allowed is False
    assert PROMOTION_BLOCKED_MISSING_ARTIFACT in d.reason_codes


def test_unavailable_snapshot_is_fail_closed() -> None:
    d = evaluate_promotion(EntryMode.DISABLED, EntryMode.PAPER, _report(available=False))
    assert d.allowed is False
    assert PROMOTION_BLOCKED_MISSING_ARTIFACT in d.reason_codes


def test_data_unknown_blocks() -> None:
    rep = _report(
        overall="data_unknown",
        positions=[
            {
                "symbol": "X",
                "risk_status": "data_unknown",
                "market_data_available": False,
            }
        ],
    )
    d = evaluate_promotion(EntryMode.PAPER, EntryMode.PROBE, rep)
    assert d.allowed is False
    assert PROMOTION_BLOCKED_POSITION_DATA_UNKNOWN in d.reason_codes
    assert PROMOTION_BLOCKED_POSITION_SOURCE_STALE in d.reason_codes


def test_stale_source_blocks_even_if_named_no_risk() -> None:
    rep = _report(
        overall="no_risk",
        positions=[
            {
                "symbol": "X",
                "risk_status": "no_risk",
                "market_data_available": True,
                "market_data_stale": True,
            }
        ],
    )
    d = evaluate_promotion(EntryMode.DISABLED, EntryMode.PAPER, rep)
    assert d.allowed is False
    assert PROMOTION_BLOCKED_POSITION_SOURCE_STALE in d.reason_codes


def test_aggregate_bleed_blocks_with_threshold() -> None:
    # no per-position risk_open, but aggregate unrealized below -50 USD
    rep = _report(
        overall="no_risk",
        positions=[
            {
                "symbol": "X",
                "risk_status": "no_risk",
                "market_data_available": True,
                "market_data_stale": False,
            }
        ],
        total_unrealized=-60.0,
    )
    d = evaluate_promotion(EntryMode.DISABLED, EntryMode.PAPER, rep, bleed_usd_threshold=50.0)
    assert d.allowed is False
    assert PROMOTION_BLOCKED_UNREALIZED_BLEED in d.reason_codes


def test_aggregate_bleed_within_threshold_passes() -> None:
    rep = _report(
        overall="no_risk",
        positions=[
            {
                "symbol": "X",
                "risk_status": "no_risk",
                "market_data_available": True,
                "market_data_stale": False,
            }
        ],
        total_unrealized=-10.0,
    )
    d = evaluate_promotion(EntryMode.DISABLED, EntryMode.PAPER, rep, bleed_usd_threshold=50.0)
    assert d.allowed is True


def test_default_threshold_ignores_entry_slippage_noise() -> None:
    """V2-Befund 2026-06-12: paper fills carry +5bps adverse slippage, so a
    fresh book is always a few USD red. The observed false-positive block
    (-23.93 USD on ~7.8k notional) must pass under the calibrated default."""
    rep = _report(total_unrealized=-23.93)
    d = evaluate_promotion(EntryMode.PAPER_LEARNING, EntryMode.PAPER, rep)
    assert d.allowed is True
    assert d.status == STATUS_ALLOWED


def test_default_threshold_blocks_genuine_bleed() -> None:
    rep = _report(total_unrealized=-120.0)
    d = evaluate_promotion(EntryMode.PAPER_LEARNING, EntryMode.PAPER, rep)
    assert d.allowed is False
    assert PROMOTION_BLOCKED_UNREALIZED_BLEED in d.reason_codes


def test_default_threshold_boundary_blocks_inclusively() -> None:
    # the gate uses <= -threshold: exactly -75.0 must still block (fail-closed)
    rep = _report(total_unrealized=-DEFAULT_BLEED_USD_THRESHOLD)
    d = evaluate_promotion(EntryMode.PAPER_LEARNING, EntryMode.PAPER, rep)
    assert d.allowed is False
    assert PROMOTION_BLOCKED_UNREALIZED_BLEED in d.reason_codes


def test_explicit_zero_threshold_restores_strict_behaviour() -> None:
    rep = _report(total_unrealized=-0.01)
    d = evaluate_promotion(EntryMode.PAPER_LEARNING, EntryMode.PAPER, rep, bleed_usd_threshold=0.0)
    assert d.allowed is False
    assert PROMOTION_BLOCKED_UNREALIZED_BLEED in d.reason_codes


def test_cli_default_matches_gate_constant() -> None:
    """trading.py must use a typer literal (deferred app.* imports); this pins
    the literal to DEFAULT_BLEED_USD_THRESHOLD so they cannot drift apart."""
    import inspect

    from app.cli.commands.trading import trading_promotion_check

    param = inspect.signature(trading_promotion_check).parameters["bleed_usd_threshold"]
    assert param.default.default == DEFAULT_BLEED_USD_THRESHOLD


def test_decision_to_dict_shape() -> None:
    d = evaluate_promotion(EntryMode.DISABLED, EntryMode.PAPER, _report(overall="risk_open"))
    out = d.to_dict()
    assert out["report_type"] == "promotion_gate_decision"
    assert out["status"] == STATUS_MANUAL_REVIEW
    assert out["current_mode"] == "disabled"
    assert out["target_mode"] == "paper"
    assert out["risk_increasing"] is True
    assert isinstance(out["reason_codes"], list)


def test_every_entry_mode_is_rankable_and_ladder_order_holds() -> None:
    """D-233 follow-up (2026-06-11): the hand-copied ladder missed the limited
    paper modes — _rank(paper_learning) raised ValueError, so the gate could
    not even evaluate the mode the Pi actually runs. The ladder is now derived
    from the enum; this pins (a) every mode is rankable, (b) the limited modes
    sit strictly between disabled and paper, (c) live stays on top."""
    from app.risk.promotion_gate import _rank

    for mode in EntryMode:
        _rank(mode)  # must never raise

    assert is_risk_increasing(EntryMode.DISABLED, EntryMode.PAPER_PREMIUM_LIMITED)
    assert is_risk_increasing(EntryMode.PAPER_PREMIUM_LIMITED, EntryMode.PAPER_LEARNING)
    assert is_risk_increasing(EntryMode.PAPER_LEARNING, EntryMode.PAPER)
    assert is_risk_increasing(EntryMode.PAPER_LEARNING, EntryMode.LIVE_LIMITED)
    # de-risking directions are never gated
    assert not is_risk_increasing(EntryMode.PAPER_LEARNING, EntryMode.DISABLED)
    assert not is_risk_increasing(EntryMode.PAPER, EntryMode.PAPER_LEARNING)
