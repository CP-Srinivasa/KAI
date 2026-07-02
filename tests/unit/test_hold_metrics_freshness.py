"""Tests for V-DB5 P2 Vorschlag 6: Hold-Report-Snapshot Freshness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.alerts.hold_metrics_freshness import (
    annotate_report,
    evaluate_snapshot_freshness,
    freshness_for_report,
    load_thresholds_from_env,
    telegram_warning_suffix,
)


def _now() -> datetime:
    return datetime(2026, 5, 9, 14, 0, tzinfo=UTC)


def test_fresh_snapshot_returns_fresh() -> None:
    generated_at = (_now() - timedelta(hours=2)).isoformat()
    result = evaluate_snapshot_freshness(generated_at=generated_at, now=_now())
    assert result.level == "fresh"
    assert result.is_stale is False
    assert result.age_hours is not None
    assert 1.9 < result.age_hours < 2.1
    assert result.message == ""


def test_warn_threshold_triggers_warn() -> None:
    generated_at = (_now() - timedelta(hours=40)).isoformat()
    result = evaluate_snapshot_freshness(generated_at=generated_at, now=_now())
    assert result.level == "warn"
    assert result.is_stale is True
    assert "40" in result.message


def test_critical_threshold_triggers_critical() -> None:
    generated_at = (_now() - timedelta(days=16)).isoformat()
    result = evaluate_snapshot_freshness(generated_at=generated_at, now=_now())
    assert result.level == "critical"
    assert result.is_stale is True
    assert "Tage" in result.message
    assert "kai-hold-report.timer" in result.message


def test_missing_generated_at_returns_missing() -> None:
    result = evaluate_snapshot_freshness(generated_at=None, now=_now())
    assert result.level == "missing"
    assert result.is_stale is True
    assert result.age_hours is None


def test_unparseable_timestamp() -> None:
    result = evaluate_snapshot_freshness(generated_at="garbage-timestamp", now=_now())
    assert result.level == "unparseable"
    assert result.is_stale is True
    assert result.age_hours is None


def test_freshness_for_report_with_dict() -> None:
    generated_at = (_now() - timedelta(hours=1)).isoformat()
    report = {"generated_at": generated_at, "some_field": "x"}
    result = freshness_for_report(report, now=_now())
    assert result.level == "fresh"


def test_freshness_for_report_with_none() -> None:
    result = freshness_for_report(None, now=_now())
    assert result.level == "missing"


def test_annotate_report_adds_freshness_field() -> None:
    generated_at = (_now() - timedelta(hours=2)).isoformat()
    report = {"generated_at": generated_at, "forward_simulation": {"precision_pct": 65.0}}
    annotated = annotate_report(report, now=_now())
    assert annotated["forward_simulation"] == {"precision_pct": 65.0}  # untouched
    assert "_freshness" in annotated
    f = annotated["_freshness"]
    assert f["level"] == "fresh"
    assert f["is_stale"] is False
    assert f["generated_at"] == generated_at


def test_annotate_does_not_mutate_input() -> None:
    report = {"generated_at": _now().isoformat()}
    snapshot_keys = set(report.keys())
    annotate_report(report, now=_now())
    assert set(report.keys()) == snapshot_keys  # original untouched


def test_telegram_warning_suffix_for_fresh() -> None:
    generated_at = (_now() - timedelta(hours=1)).isoformat()
    result = evaluate_snapshot_freshness(generated_at=generated_at, now=_now())
    assert telegram_warning_suffix(result) == ""


def test_telegram_warning_suffix_for_warn() -> None:
    generated_at = (_now() - timedelta(hours=40)).isoformat()
    result = evaluate_snapshot_freshness(generated_at=generated_at, now=_now())
    suffix = telegram_warning_suffix(result)
    assert suffix.startswith("\n\n⚠️")
    assert "40h alt" in suffix


def test_telegram_warning_suffix_for_critical() -> None:
    generated_at = (_now() - timedelta(days=16)).isoformat()
    result = evaluate_snapshot_freshness(generated_at=generated_at, now=_now())
    suffix = telegram_warning_suffix(result)
    assert suffix.startswith("\n\n🔴")


def test_thresholds_from_env() -> None:
    env = {
        "APP_HOLD_REPORT_STALE_WARN_HOURS": "12",
        "APP_HOLD_REPORT_STALE_CRIT_HOURS": "72",
    }
    warn, crit = load_thresholds_from_env(env)
    assert warn == 12
    assert crit == 72


def test_thresholds_default_when_env_missing() -> None:
    warn, crit = load_thresholds_from_env({})
    assert warn == 30
    assert crit == 168


def test_naive_now_is_treated_as_utc() -> None:
    """Operator may pass datetime.now() without tzinfo — must not crash."""
    generated_at = "2026-05-09T13:00:00+00:00"
    naive_now = datetime(2026, 5, 9, 14, 0)  # no tzinfo
    result = evaluate_snapshot_freshness(generated_at=generated_at, now=naive_now)
    assert result.level == "fresh"
    assert result.age_hours is not None
    assert 0.9 < result.age_hours < 1.1
