"""Tests for V-DB5 P2 Vorschlag 2: Forward-Precision Watchdog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.forward_precision_watchdog import (
    StreakState,
    WatchdogConfig,
    evaluate_forward_precision_drift,
    load_config_from_env,
    load_state,
    save_state,
)


def _cfg(
    *,
    threshold: float = 60.0,
    ci_low: float = 50.0,
    hours: int = 6,
    cooldown: int = 12,
) -> WatchdogConfig:
    return WatchdogConfig(
        threshold_pct=threshold,
        ci_low_threshold_pct=ci_low,
        consecutive_hours=hours,
        cooldown_hours=cooldown,
    )


def _t(hour: int) -> datetime:
    return datetime(2026, 5, 9, hour, 0, tzinfo=UTC)


def test_above_threshold_keeps_streak_zero() -> None:
    result = evaluate_forward_precision_drift(
        forward_precision_pct=72.5,
        forward_precision_ci_low_pct=65.0,
        state=StreakState(),
        now=_t(10),
        config=_cfg(),
    )
    assert result.severity == "info"
    assert result.title == "forward_precision_ok"
    assert result.new_state.streak_hours == 0
    assert result.should_push is False


def test_first_below_starts_streak_no_push_yet() -> None:
    result = evaluate_forward_precision_drift(
        forward_precision_pct=58.0,
        forward_precision_ci_low_pct=52.0,
        state=StreakState(),
        now=_t(10),
        config=_cfg(),
    )
    assert result.severity == "info"
    assert result.title == "forward_precision_below_warming"
    assert result.new_state.streak_hours == 1
    assert result.new_state.first_below_at == _t(10).isoformat()
    assert result.should_push is False


def test_streak_crosses_threshold_pushes_drift() -> None:
    state = StreakState(streak_hours=5, first_below_at=_t(5).isoformat())
    result = evaluate_forward_precision_drift(
        forward_precision_pct=58.0,
        forward_precision_ci_low_pct=52.0,
        state=state,
        now=_t(11),
        config=_cfg(hours=6),
    )
    assert result.severity == "warn"
    assert result.title == "forward_precision_drift"
    assert result.new_state.streak_hours == 6
    assert result.should_push is True
    assert result.push_kind == "drift"
    assert result.new_state.last_push_kind == "drift"


def test_drift_does_not_double_push_within_cooldown() -> None:
    """Once we've pushed drift, don't push again until cooldown elapsed."""
    state = StreakState(
        streak_hours=6,
        first_below_at=_t(5).isoformat(),
        last_push_at=_t(11).isoformat(),
        last_push_kind="drift",
    )
    result = evaluate_forward_precision_drift(
        forward_precision_pct=55.0,
        forward_precision_ci_low_pct=48.0,
        state=state,
        now=_t(15),  # 4h later, cooldown is 12h
        config=_cfg(hours=6, cooldown=12),
    )
    assert result.severity == "warn"
    assert result.new_state.streak_hours == 7
    assert result.should_push is False  # cooldown active


def test_recovery_after_drift_pushes_recovery_message() -> None:
    state = StreakState(
        streak_hours=8,
        first_below_at=_t(5).isoformat(),
        last_push_at=_t(11).isoformat(),
        last_push_kind="drift",
    )
    result = evaluate_forward_precision_drift(
        forward_precision_pct=68.0,
        forward_precision_ci_low_pct=62.0,
        state=state,
        now=_t(11) + timedelta(hours=13),  # past cooldown
        config=_cfg(),
    )
    assert result.severity == "info"
    assert result.title == "forward_precision_recovered"
    assert result.new_state.streak_hours == 0
    assert result.new_state.last_push_kind == "recovery"
    assert result.should_push is True
    assert result.push_kind == "recovery"


def test_recovery_without_prior_drift_no_push() -> None:
    """We were below for a few hours but never crossed → no recovery push."""
    state = StreakState(
        streak_hours=3,
        first_below_at=_t(7).isoformat(),
    )
    result = evaluate_forward_precision_drift(
        forward_precision_pct=72.0,
        forward_precision_ci_low_pct=65.0,
        state=state,
        now=_t(10),
        config=_cfg(),
    )
    assert result.severity == "info"
    assert result.title == "forward_precision_ok"
    assert result.new_state.streak_hours == 0
    assert result.should_push is False


def test_only_ci_low_below_triggers_drift() -> None:
    """forward_precision_pct above threshold but ci_low_pct below — drift."""
    state = StreakState(streak_hours=5, first_below_at=_t(5).isoformat())
    result = evaluate_forward_precision_drift(
        forward_precision_pct=62.0,  # above 60
        forward_precision_ci_low_pct=45.0,  # below 50
        state=state,
        now=_t(11),
        config=_cfg(hours=6),
    )
    assert result.severity == "warn"
    assert result.should_push is True


def test_no_data_returns_info_keeps_streak() -> None:
    state = StreakState(streak_hours=3, first_below_at=_t(7).isoformat())
    result = evaluate_forward_precision_drift(
        forward_precision_pct=None,
        forward_precision_ci_low_pct=None,
        state=state,
        now=_t(10),
        config=_cfg(),
    )
    assert result.severity == "info"
    assert result.title == "forward_precision_no_data"
    assert result.new_state.streak_hours == 3  # unchanged


def test_state_round_trip_via_disk(tmp_path: Path) -> None:
    state = StreakState(
        streak_hours=4,
        first_below_at=_t(6).isoformat(),
        last_check_at=_t(10).isoformat(),
        last_push_at=_t(11).isoformat(),
        last_push_kind="drift",
    )
    path = tmp_path / "watchdog" / "forward_precision_streak.json"
    save_state(state, path)
    loaded = load_state(path)
    assert loaded == state


def test_state_load_missing_file_returns_fresh() -> None:
    fresh = load_state(Path("/nonexistent/path/should/be/missing.json"))
    assert fresh.streak_hours == 0
    assert fresh.first_below_at is None


def test_config_from_env() -> None:
    env = {
        "APP_WATCHDOG_FWD_PREC_THRESHOLD_PCT": "55.5",
        "APP_WATCHDOG_FWD_PREC_CI_LOW_THRESHOLD_PCT": "45.5",
        "APP_WATCHDOG_FWD_PREC_CONSECUTIVE_HOURS": "4",
        "APP_WATCHDOG_FWD_PREC_COOLDOWN_HOURS": "8",
    }
    cfg = load_config_from_env(env)
    assert cfg.threshold_pct == 55.5
    assert cfg.ci_low_threshold_pct == 45.5
    assert cfg.consecutive_hours == 4
    assert cfg.cooldown_hours == 8


def test_config_from_env_uses_defaults_when_missing() -> None:
    cfg = load_config_from_env({})
    assert cfg.threshold_pct == 60.0
    assert cfg.consecutive_hours == 6
