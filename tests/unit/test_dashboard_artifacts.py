from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from app.api.routers.dashboard_artifacts import (
    artifact_stale_status,
    artifact_updated_at,
    first_present_ts,
    parse_iso_utc,
    stale_status_for_age_hours,
)


def test_parse_iso_utc_accepts_z_and_normalizes_to_utc() -> None:
    parsed = parse_iso_utc("2026-08-27T12:34:56Z")

    assert parsed == datetime(2026, 8, 27, 12, 34, 56, tzinfo=UTC)


def test_parse_iso_utc_treats_naive_values_as_utc() -> None:
    parsed = parse_iso_utc("2026-08-27T12:34:56")

    assert parsed == datetime(2026, 8, 27, 12, 34, 56, tzinfo=UTC)


def test_parse_iso_utc_returns_none_for_unparseable_values() -> None:
    assert parse_iso_utc("not-a-date") is None
    assert parse_iso_utc(None) is None


def test_first_present_ts_uses_first_parseable_key() -> None:
    row = {"created_at": "not-a-date", "updated_at": "2026-08-27T12:34:56Z"}

    assert first_present_ts(row, ("created_at", "updated_at")) == datetime(
        2026,
        8,
        27,
        12,
        34,
        56,
        tzinfo=UTC,
    )


def test_artifact_freshness_statuses_follow_existing_thresholds(tmp_path) -> None:
    artifact = tmp_path / "artifact.jsonl"
    artifact.write_text("{}", encoding="utf-8")
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

    os.utime(artifact, (now.timestamp(), now.timestamp()))
    assert artifact_updated_at(artifact) is not None
    assert artifact_stale_status(artifact, now=now) == "ok"

    warning_time = now - timedelta(hours=4)
    os.utime(artifact, (warning_time.timestamp(), warning_time.timestamp()))
    assert artifact_stale_status(artifact, now=now) == "warning"

    stale_time = now - timedelta(hours=25)
    os.utime(artifact, (stale_time.timestamp(), stale_time.timestamp()))
    assert artifact_stale_status(artifact, now=now) == "stale"


def test_stale_status_for_age_hours_matches_existing_thresholds() -> None:
    assert stale_status_for_age_hours(None) == "unverified"
    assert stale_status_for_age_hours(1.0) == "ok"
    assert stale_status_for_age_hours(4.0) == "warning"
    assert stale_status_for_age_hours(25.0) == "stale"


def test_missing_artifact_is_unverified(tmp_path) -> None:
    assert artifact_stale_status(tmp_path / "missing.jsonl") == "unverified"
