"""Unit-Tests für premium_pipeline_health (P0 #4).

Tests stub the four DBus/FS probe functions via the ``_*_check_fn`` hooks
on ``compute_pipeline_health`` so a real systemd / filesystem is never
required. Heartbeat- + bridge-audit-checks are additionally exercised
against a temp directory so the file-mtime arithmetic is real.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability import premium_pipeline_health as pph


def _fake_service_check_factory(state: str):
    def _check(unit: str, now: datetime | None = None) -> pph.CheckResult:
        return pph.CheckResult(
            name=f"systemd:{unit}",
            ok=(state == "active"),
            detail=f"ActiveState={state}",
        )

    return _check


def _fake_timer_check_factory(*, ok: bool, age_seconds: float):
    def _check(max_age_seconds: int, now: datetime | None = None) -> pph.CheckResult:
        return pph.CheckResult(
            name="paper_timer_last_trigger",
            ok=ok,
            detail=f"fake age={age_seconds}s",
            age_seconds=age_seconds,
        )

    return _check


def _fake_hb_check_factory(*, ok: bool):
    def _check(
        max_age_seconds: int,
        now: datetime | None = None,
        path: Path | None = None,
    ) -> pph.CheckResult:
        return pph.CheckResult(name="heartbeat", ok=ok, detail="fake")

    return _check


def _fake_audit_check() -> pph.CheckResult:
    return pph.CheckResult(name="bridge_audit_last_event", ok=True, detail="fake")


def _fake_audit_check_fn(
    info_age_seconds: int,
    now: datetime | None = None,
    path: Path | None = None,
):
    return _fake_audit_check()


def _fake_canary_check_fn(
    max_age_seconds: int,
    now: datetime | None = None,
    path: Path | None = None,
):
    return pph.CheckResult(name="semantic_canary", ok=True, detail="fake")


def _fake_hmac_check_fn():
    return pph.CheckResult(name="approval_hmac", ok=True, detail="fake")


def _health_report(**kwargs):
    defaults = {
        "_semantic_canary_check_fn": _fake_canary_check_fn,
        "_approval_hmac_check_fn": _fake_hmac_check_fn,
    }
    defaults.update(kwargs)
    return pph.compute_pipeline_health(**defaults)


def test_all_green_yields_healthy_true():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    report = _health_report(
        now=now,
        _service_check_fn=_fake_service_check_factory("active"),
        _paper_timer_check_fn=_fake_timer_check_factory(ok=True, age_seconds=60),
        _heartbeat_check_fn=_fake_hb_check_factory(ok=True),
        _bridge_audit_check_fn=_fake_audit_check_fn,
    )
    assert report.healthy is True
    assert report.failure_modes == []
    # 3 services + timer + heartbeat + semantic-canary + HMAC + audit-info.
    assert len(report.checks) == 8


def test_any_service_inactive_marks_pipeline_unhealthy():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    report = _health_report(
        now=now,
        _service_check_fn=_fake_service_check_factory("inactive"),
        _paper_timer_check_fn=_fake_timer_check_factory(ok=True, age_seconds=60),
        _heartbeat_check_fn=_fake_hb_check_factory(ok=True),
        _bridge_audit_check_fn=_fake_audit_check_fn,
    )
    assert report.healthy is False
    # All three CRITICAL_SERVICES contribute one failure_mode each.
    assert len(report.failure_modes) == 3
    assert all(name.startswith("systemd:") for name in report.failure_modes)


def test_stale_paper_timer_marks_unhealthy_but_audit_stays_informational():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    report = _health_report(
        now=now,
        _service_check_fn=_fake_service_check_factory("active"),
        _paper_timer_check_fn=_fake_timer_check_factory(ok=False, age_seconds=99 * 60),
        _heartbeat_check_fn=_fake_hb_check_factory(ok=True),
        _bridge_audit_check_fn=_fake_audit_check_fn,
    )
    assert report.healthy is False
    assert report.failure_modes == ["paper_timer_last_trigger"]


def test_stale_heartbeat_marks_unhealthy():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    report = _health_report(
        now=now,
        _service_check_fn=_fake_service_check_factory("active"),
        _paper_timer_check_fn=_fake_timer_check_factory(ok=True, age_seconds=60),
        _heartbeat_check_fn=_fake_hb_check_factory(ok=False),
        _bridge_audit_check_fn=_fake_audit_check_fn,
    )
    assert report.healthy is False
    assert "heartbeat" in report.failure_modes


def test_real_heartbeat_check_with_fresh_file(tmp_path: Path):
    hb = tmp_path / "telegram_listener_heartbeat"
    hb.write_text("test")
    # mtime now ≈ wall-clock → age ≈ 0
    result = pph._check_heartbeat(max_age_seconds=90, path=hb)
    assert result.ok is True
    assert result.age_seconds is not None
    assert result.age_seconds < 5


def test_real_heartbeat_check_with_stale_file(tmp_path: Path):
    hb = tmp_path / "telegram_listener_heartbeat"
    hb.write_text("test")
    # Reach back 200s — older than the 90s default ceiling.
    stale_ts = (datetime.now(UTC) - timedelta(seconds=200)).timestamp()
    import os

    os.utime(hb, (stale_ts, stale_ts))
    result = pph._check_heartbeat(max_age_seconds=90, path=hb)
    assert result.ok is False
    assert result.age_seconds is not None
    assert result.age_seconds > 90


def test_real_heartbeat_check_with_missing_file(tmp_path: Path):
    hb = tmp_path / "does_not_exist"
    result = pph._check_heartbeat(max_age_seconds=90, path=hb)
    assert result.ok is False
    assert "missing" in result.detail


def test_real_semantic_canary_check_with_converged_checkpoint(tmp_path: Path):
    canary = tmp_path / "telegram_channel_semantic_canary.json"
    canary.write_text(
        json.dumps(
            {
                "checked_at": datetime.now(UTC).isoformat(),
                "checkpoint_message_id": 23878,
                "latest_message_id": 23878,
                "gap": 0,
            }
        )
    )
    result = pph._check_semantic_canary(max_age_seconds=180, path=canary)
    assert result.ok is True


def test_real_semantic_canary_check_with_gap_fails(tmp_path: Path):
    canary = tmp_path / "telegram_channel_semantic_canary.json"
    canary.write_text(
        json.dumps(
            {
                "checked_at": datetime.now(UTC).isoformat(),
                "checkpoint_message_id": 23878,
                "latest_message_id": 23880,
                "gap": 2,
            }
        )
    )
    result = pph._check_semantic_canary(max_age_seconds=180, path=canary)
    assert result.ok is False
    assert "gap=2" in result.detail


def test_semantic_canary_tolerates_single_floodwait_backoff(tmp_path: Path):
    """A converged canary stale by one FloodWait backoff (300s flood + 90s tick)
    MUST pass under the default threshold — the backstop's MTProto calls run with
    flood_sleep_threshold=300, so a healthy iteration can legitimately delay the
    write past the old 3min limit (the 22:40 false-positive). Regression for that.
    """
    canary = tmp_path / "telegram_channel_semantic_canary.json"
    canary.write_text(
        json.dumps(
            {
                "checked_at": (datetime.now(UTC) - timedelta(seconds=390)).isoformat(),
                "checkpoint_message_id": 23920,
                "latest_message_id": 23920,
                "gap": 0,
            }
        )
    )
    result = pph._check_semantic_canary(
        max_age_seconds=pph.DEFAULT_SEMANTIC_CANARY_MAX_AGE_SEC, path=canary
    )
    assert result.ok is True


def test_semantic_canary_still_catches_unbounded_loop_hang(tmp_path: Path):
    """Beyond a bounded FloodWait the write gap implies the backstop loop hung
    (no timeout, never raises → no self-heal). That MUST still fail — the canary
    is the sole liveness guard for the poll-backstop loop.
    """
    canary = tmp_path / "telegram_channel_semantic_canary.json"
    canary.write_text(
        json.dumps(
            {
                "checked_at": (datetime.now(UTC) - timedelta(minutes=12)).isoformat(),
                "checkpoint_message_id": 23920,
                "latest_message_id": 23920,
                "gap": 0,
            }
        )
    )
    result = pph._check_semantic_canary(
        max_age_seconds=pph.DEFAULT_SEMANTIC_CANARY_MAX_AGE_SEC, path=canary
    )
    assert result.ok is False


def test_real_audit_check_with_stale_log_stays_ok(tmp_path: Path):
    """Stale audit log MUST NOT trip failure — silent ticks don't write."""
    log = tmp_path / "bridge_pending_orders.jsonl"
    old_event = {
        "timestamp_utc": (datetime.now(UTC) - timedelta(hours=10)).isoformat(),
        "stage": "filled",
    }
    log.write_text(json.dumps(old_event) + "\n")
    result = pph._check_bridge_audit_freshness(info_age_seconds=15 * 60, path=log)
    assert result.ok is True
    assert "stale_but_not_a_failure" in result.detail


def test_real_audit_check_with_no_records_stays_ok(tmp_path: Path):
    log = tmp_path / "bridge_pending_orders.jsonl"
    log.write_text("")
    result = pph._check_bridge_audit_freshness(info_age_seconds=15 * 60, path=log)
    assert result.ok is True


def _usec(now: datetime, *, seconds_ago: float) -> int:
    return int((now - timedelta(seconds=seconds_ago)).timestamp() * 1_000_000)


def test_cycling_service_recent_inactive_is_healthy():
    """kai-entry-watch in its ~5s RestartSec gap must NOT trip a FAIL."""
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    result = pph._check_service_active(
        "kai-entry-watch.service",
        now=now,
        _state_fn=lambda unit: ("/u/entry", "inactive"),
        _inactive_usec_fn=lambda path: _usec(now, seconds_ago=4),
    )
    assert result.ok is True
    assert "cycling" in result.detail
    assert result.age_seconds is not None and result.age_seconds < 90


def test_cycling_service_stale_inactive_fails():
    """A genuine outage (operator stop) dwells inactive past the cycle tolerance."""
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    result = pph._check_service_active(
        "kai-entry-watch.service",
        now=now,
        _state_fn=lambda unit: ("/u/entry", "inactive"),
        _inactive_usec_fn=lambda path: _usec(now, seconds_ago=300),
    )
    assert result.ok is False
    assert "tolerance" in result.detail


def test_cycling_service_failed_state_always_fails():
    """ActiveState=failed (StartLimitBurst exhausted) is never tolerated."""
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    result = pph._check_service_active(
        "kai-entry-watch.service",
        now=now,
        _state_fn=lambda unit: ("/u/entry", "failed"),
        _inactive_usec_fn=lambda path: _usec(now, seconds_ago=1),
    )
    assert result.ok is False
    assert result.detail == "ActiveState=failed"


def test_non_cycling_service_inactive_still_fails():
    """The cycling tolerance MUST NOT leak to the always-on listener service."""
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    result = pph._check_service_active(
        "kai-tg-listener.service",
        now=now,
        _state_fn=lambda unit: ("/u/listener", "inactive"),
        _inactive_usec_fn=lambda path: _usec(now, seconds_ago=1),
    )
    assert result.ok is False
    assert result.detail == "ActiveState=inactive"


def test_cycling_service_active_is_healthy():
    result = pph._check_service_active(
        "kai-entry-watch.service",
        _state_fn=lambda unit: ("/u/entry", "active"),
        _inactive_usec_fn=lambda path: 0,
    )
    assert result.ok is True
    assert result.detail == "ActiveState=active"


def test_report_to_dict_roundtrip():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    report = _health_report(
        now=now,
        _service_check_fn=_fake_service_check_factory("active"),
        _paper_timer_check_fn=_fake_timer_check_factory(ok=True, age_seconds=60),
        _heartbeat_check_fn=_fake_hb_check_factory(ok=True),
        _bridge_audit_check_fn=_fake_audit_check_fn,
    )
    payload = report.to_dict()
    assert payload["healthy"] is True
    assert payload["timestamp_utc"] == now.isoformat()
    assert isinstance(payload["checks"], list)
    assert isinstance(payload["failure_modes"], list)


# ── _check_paper_timer_last_trigger: timer-OR-service liveness (2026-06-24) ──

_NOW = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)


def _usec_ago(seconds: float) -> int:
    return int((_NOW - timedelta(seconds=seconds)).timestamp() * 1_000_000)


def _fake_path_fn(unit: str):
    return (f"/org/freedesktop/systemd1/unit/{unit}", "active")


def test_paper_timer_fresh_trigger_is_ok() -> None:
    """Primary proof: a recent timer trigger passes (service not even consulted)."""

    def _no_service(_path: str) -> int:
        raise AssertionError("service fallback must not be consulted when timer is fresh")

    result = pph._check_paper_timer_last_trigger(
        900,
        now=_NOW,
        _path_fn=_fake_path_fn,
        _timer_trigger_fn=lambda _p: _usec_ago(120),
        _service_inactive_fn=_no_service,
    )
    assert result.ok is True
    assert "timer fired" in result.detail


def test_stale_timer_but_recent_service_run_is_ok() -> None:
    """The fix: non-timer start (manual / entry-watch) lagged LastTriggerUSec to
    ~18min, but the SERVICE ran 2min ago → live, no false-positive FAIL."""
    result = pph._check_paper_timer_last_trigger(
        900,
        now=_NOW,
        _path_fn=_fake_path_fn,
        _timer_trigger_fn=lambda _p: _usec_ago(18 * 60),
        _service_inactive_fn=lambda _p: _usec_ago(120),
    )
    assert result.ok is True
    assert "service ran" in result.detail


def test_both_timer_and_service_stale_fails() -> None:
    """Genuine stall: timer trigger AND service run both stale → FAIL (real outage
    still caught within the window)."""
    result = pph._check_paper_timer_last_trigger(
        900,
        now=_NOW,
        _path_fn=_fake_path_fn,
        _timer_trigger_fn=lambda _p: _usec_ago(48 * 3600),
        _service_inactive_fn=lambda _p: _usec_ago(40 * 60),
    )
    assert result.ok is False
    assert "no tick" in result.detail


def test_timer_never_fired_but_service_recent_is_ok() -> None:
    """LastTriggerUSec=0 (never timer-fired since boot) but the service ran 1min
    ago via a manual/entry-watch start → live."""
    result = pph._check_paper_timer_last_trigger(
        900,
        now=_NOW,
        _path_fn=_fake_path_fn,
        _timer_trigger_fn=lambda _p: 0,
        _service_inactive_fn=lambda _p: _usec_ago(60),
    )
    assert result.ok is True
    assert "service ran" in result.detail
