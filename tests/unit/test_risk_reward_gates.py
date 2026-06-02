"""Sprint 2026-06-02 — reward/risk + risk-budget gates (Gate 10) and reason codes.

Grounded in the real incident: env ENV-TG-001275462917-23879-502ef70a
(US/USDT LONG 10x, entry 0.00833, SL 0.00798, targets
0.00837/0.008415/0.008455/0.008495) was only ever stopped by the
max_open_positions cap. These tests prove the new gates would catch it and that
the gates are a strict no-op when disabled (default).
"""

from __future__ import annotations

import pytest

from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.risk.reason_codes import RejectCode, map_violations_to_codes

# The exact signal from the forensic analysis.
US_ENTRY = 0.00833
US_SL = 0.00798
US_TARGETS = [0.00837, 0.008415, 0.008455, 0.008495]
US_LEVERAGE = 10.0


def _limits(**overrides: object) -> RiskLimits:
    """Build RiskLimits with permissive base + targeted overrides.

    Base disables every legacy gate that is irrelevant here (confidence /
    confluence / regime) so a test isolates exactly the reward/risk gate under
    test. round_trip_fee_pct defaults to the productive paper value (0.2%) so
    net-edge math matches production.
    """
    base: dict[str, object] = {
        "initial_equity": 10_000.0,
        "max_risk_per_trade_pct": 100.0,
        "max_daily_loss_pct": 100.0,
        "max_total_drawdown_pct": 100.0,
        "max_open_positions": 99,
        "max_leverage": 125.0,
        "require_stop_loss": True,
        "allow_averaging_down": True,
        "allow_martingale": True,
        "kill_switch_enabled": False,
        "min_signal_confidence": 0.0,
        "min_signal_confluence_count": 0,
        "regime_filter_enabled": False,
        "round_trip_fee_pct": 0.2,
    }
    base.update(overrides)
    return RiskLimits(**base)  # type: ignore[arg-type]


def _check(limits: RiskLimits, **kw: object):
    engine = RiskEngine(limits)
    defaults: dict[str, object] = {
        "symbol": "US/USDT",
        "side": "buy",
        "signal_confidence": 1.0,
        "signal_confluence_count": 99,
        "stop_loss_price": US_SL,
        "current_open_positions": 0,
        "entry_price": US_ENTRY,
        "take_profit_targets": US_TARGETS,
        "leverage": US_LEVERAGE,
    }
    defaults.update(kw)
    return engine.check_order(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Geometry diagnostics
# --------------------------------------------------------------------------- #


def test_geometry_matches_hand_computed_values() -> None:
    res = _check(_limits())
    geom = res.details["signal_geometry"]
    assert geom is not None
    assert geom["stop_distance_pct"] == pytest.approx(4.20168, abs=1e-3)
    assert geom["leveraged_risk_pct"] == pytest.approx(42.0168, abs=1e-2)
    assert geom["rr_t1"] == pytest.approx(0.11429, abs=1e-3)
    assert geom["avg_rr"] == pytest.approx(0.29643, abs=1e-3)
    # net edge T1 at 0.2% round-trip: (0.48019 - 0.2) * 100 = ~28.0 bps
    assert geom["net_edge_bps_t1"] == pytest.approx(28.02, abs=0.2)


def test_gates_off_by_default_signal_passes() -> None:
    """With all reward/risk gates disabled (defaults) the bad signal is NOT
    rejected by Gate 10 — proving the change is a strict no-op by default."""
    res = _check(_limits())
    assert res.approved is True
    assert res.violations == []
    # diagnostics still present
    assert res.details["signal_geometry"] is not None


# --------------------------------------------------------------------------- #
# Individual gates catch the real signal
# --------------------------------------------------------------------------- #


def test_max_leveraged_risk_blocks_us_signal() -> None:
    res = _check(_limits(max_leveraged_risk_pct=35.0))
    assert res.approved is False
    assert any(v.startswith("leveraged_risk_too_high") for v in res.violations)
    assert RejectCode.RISK_TOO_HIGH.value in res.reason_codes


def test_min_rr_blocks_us_signal() -> None:
    res = _check(_limits(min_rr=0.5))
    assert res.approved is False
    assert any(v.startswith("rr_too_low") for v in res.violations)
    assert RejectCode.RR_TOO_LOW.value in res.reason_codes


def test_min_avg_rr_blocks_us_signal() -> None:
    res = _check(_limits(min_avg_rr=0.8))
    assert res.approved is False
    assert RejectCode.AVG_RR_TOO_LOW.value in res.reason_codes


def test_max_signal_risk_pct_threshold() -> None:
    # 4.2% stop passes an 8% cap, fails a 3% cap.
    assert _check(_limits(max_signal_risk_pct=8.0)).approved is True
    res = _check(_limits(max_signal_risk_pct=3.0))
    assert res.approved is False
    assert RejectCode.RISK_TOO_HIGH.value in res.reason_codes


def test_min_target_distance_threshold() -> None:
    # T1 distance 0.48% passes 0.3% floor, fails 1.0% floor.
    assert _check(_limits(min_target_distance_pct=0.3)).approved is True
    res = _check(_limits(min_target_distance_pct=1.0))
    assert res.approved is False
    assert RejectCode.TARGET_TOO_CLOSE.value in res.reason_codes


def test_min_net_edge_blocks_when_below_threshold() -> None:
    # net edge ~28 bps; require 30 -> reject.
    res = _check(_limits(min_net_edge_bps=30.0))
    assert res.approved is False
    assert RejectCode.NET_EDGE_TOO_LOW.value in res.reason_codes
    # require 25 -> pass
    assert _check(_limits(min_net_edge_bps=25.0)).approved is True


# --------------------------------------------------------------------------- #
# Fail-closed behaviour
# --------------------------------------------------------------------------- #


def test_enabled_gate_without_targets_is_fail_closed() -> None:
    res = _check(_limits(min_rr=0.5), take_profit_targets=None, take_profit_price=None)
    assert res.approved is False
    assert any("insufficient_data" in v for v in res.violations)


def test_enabled_gate_without_entry_is_fail_closed() -> None:
    res = _check(_limits(min_rr=0.5), entry_price=None)
    assert res.approved is False
    assert any("insufficient_data" in v for v in res.violations)


# --------------------------------------------------------------------------- #
# Diagnostics are logged even when an earlier gate blocks
# --------------------------------------------------------------------------- #


def test_geometry_present_even_when_max_open_blocks() -> None:
    res = _check(_limits(max_open_positions=6), current_open_positions=6)
    assert res.approved is False
    assert any(v.startswith("max_open_positions_reached") for v in res.violations)
    assert RejectCode.MAX_OPEN_POSITIONS.value in res.reason_codes
    # geometry diagnostics still computed
    assert res.details["signal_geometry"] is not None
    assert res.details["signal_geometry"]["rr_t1"] == pytest.approx(0.11429, abs=1e-3)


# --------------------------------------------------------------------------- #
# Short-side geometry
# --------------------------------------------------------------------------- #


def test_short_side_reward_is_favourable_direction() -> None:
    # short: entry 100, sl 110 (10% stop), targets below entry are favourable.
    res = _check(
        _limits(min_rr=0.5),
        side="sell",
        entry_price=100.0,
        stop_loss_price=110.0,
        take_profit_targets=[95.0, 90.0],
        leverage=1.0,
    )
    geom = res.details["signal_geometry"]
    assert geom["t1_reward_pct"] == pytest.approx(5.0, abs=1e-6)
    assert geom["rr_t1"] == pytest.approx(0.5, abs=1e-6)


# --------------------------------------------------------------------------- #
# Reason-code mapper
# --------------------------------------------------------------------------- #


def test_reason_code_mapper_is_total_and_dedupes() -> None:
    codes = map_violations_to_codes(
        ["max_open_positions_reached:6>=6", "rr_too_low:0.1<0.5", "something_new:42"]
    )
    assert RejectCode.MAX_OPEN_POSITIONS.value in codes
    assert RejectCode.RR_TOO_LOW.value in codes
    assert RejectCode.UNCLASSIFIED.value in codes
    # de-dup: two max_open violations collapse to one code
    codes2 = map_violations_to_codes(
        ["max_open_positions_reached:6>=6", "max_open_positions_reached:7>=6"]
    )
    assert codes2.count(RejectCode.MAX_OPEN_POSITIONS.value) == 1
