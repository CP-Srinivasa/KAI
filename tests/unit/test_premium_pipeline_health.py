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
    def _check(unit: str) -> pph.CheckResult:
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
    def _check(max_age_seconds: int, now: datetime | None = None, path: Path | None = None) -> pph.CheckResult:
        return pph.CheckResult(name="heartbeat", ok=ok, detail="fake")
    return _check


def _fake_audit_check() -> "pph.CheckResult":
    return pph.CheckResult(name="bridge_audit_last_event", ok=True, detail="fake")


def _fake_audit_check_fn(info_age_seconds: int, now: datetime | None = None, path: Path | None = None):
    return _fake_audit_check()


def test_all_green_yields_healthy_true():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    report = pph.compute_pipeline_health(
        now=now,
        _service_check_fn=_fake_service_check_factory("active"),
        _paper_timer_check_fn=_fake_timer_check_factory(ok=True, age_seconds=60),
        _heartbeat_check_fn=_fake_hb_check_factory(ok=True),
        _bridge_audit_check_fn=_fake_audit_check_fn,
    )
    assert report.healthy is True
    assert report.failure_modes == []
    # 3 services + timer + heartbeat + audit-info = 6 checks
    assert len(report.checks) == 6


def test_any_service_inactive_marks_pipeline_unhealthy():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    report = pph.compute_pipeline_health(
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
    report = pph.compute_pipeline_health(
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
    report = pph.compute_pipeline_health(
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


def test_report_to_dict_roundtrip():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    report = pph.compute_pipeline_health(
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
