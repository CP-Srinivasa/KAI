"""Unit tests for premium_latency.compute_latency_stats (P2 #11 trigger-watch)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.observability import premium_latency as pl


_NOW = datetime(2026, 5, 21, 6, 5, tzinfo=UTC)


def _write_audit(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _fill_record(*, fill_offset_sec: int, origin_offset_sec: int) -> dict:
    fill_ts = (_NOW - timedelta(seconds=fill_offset_sec)).isoformat()
    origin_ts = (_NOW - timedelta(seconds=origin_offset_sec)).isoformat()
    return {
        "timestamp_utc": fill_ts,
        "stage": "filled",
        "origin_envelope_timestamp": origin_ts,
    }


@pytest.fixture
def baseline_path(tmp_path: Path) -> Path:
    """Per-test isolated baseline, pre-written far before _NOW so tests
    only exercise the lookback-window cutoff (not the baseline cutoff).

    Tests that specifically exercise baseline-suppression manage their own
    fresh baseline file inside the test body.
    """
    path = tmp_path / "baseline.json"
    payload = {
        "baseline_at": (_NOW - timedelta(days=365)).isoformat(),
        "rationale": "test fixture — pre-_NOW so lookback drives the cutoff",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_no_audit_yields_zero_stats(tmp_path: Path, baseline_path: Path):
    stats = pl.compute_latency_stats(
        audit_path=tmp_path / "missing.jsonl",
        baseline_path=baseline_path,
        now=_NOW,
    )
    assert stats.sample_size == 0
    assert stats.p95_seconds is None
    assert stats.trigger_fired is False


def test_single_fill_yields_one_sample(tmp_path: Path, baseline_path: Path):
    log = tmp_path / "bridge.jsonl"
    _write_audit(log, [_fill_record(fill_offset_sec=1000, origin_offset_sec=1300)])
    stats = pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=_NOW)
    assert stats.sample_size == 1
    assert stats.p50_seconds == 300
    assert stats.p95_seconds == 300
    assert stats.max_seconds == 300


def test_below_min_samples_does_not_trigger(tmp_path: Path, baseline_path: Path):
    log = tmp_path / "bridge.jsonl"
    # 4 samples all 30 min — high latency but n < min_samples
    _write_audit(log, [
        _fill_record(fill_offset_sec=100 + i, origin_offset_sec=100 + i + 1800)
        for i in range(4)
    ])
    stats = pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=_NOW)
    assert stats.sample_size == 4
    assert stats.p95_seconds >= 20 * 60
    assert stats.trigger_fired is False


def test_high_p95_with_enough_samples_fires_trigger(tmp_path: Path, baseline_path: Path):
    log = tmp_path / "bridge.jsonl"
    # 5 fills, all 30 min latency → p95 = 1800s > 20min, n=5 >= 5 → fires
    _write_audit(log, [
        _fill_record(fill_offset_sec=100 + i, origin_offset_sec=100 + i + 1800)
        for i in range(5)
    ])
    stats = pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=_NOW)
    assert stats.sample_size == 5
    assert stats.p95_seconds == 1800
    assert stats.trigger_fired is True
    assert "p95=" in stats.trigger_reason
    assert "samples=5" in stats.trigger_reason


def test_low_p95_does_not_trigger_even_with_many_samples(tmp_path: Path, baseline_path: Path):
    log = tmp_path / "bridge.jsonl"
    # 20 fills, all 5 min latency
    _write_audit(log, [
        _fill_record(fill_offset_sec=100 + i, origin_offset_sec=100 + i + 300)
        for i in range(20)
    ])
    stats = pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=_NOW)
    assert stats.sample_size == 20
    assert stats.p95_seconds == 300
    assert stats.trigger_fired is False


def test_records_outside_lookback_excluded(tmp_path: Path, baseline_path: Path):
    log = tmp_path / "bridge.jsonl"
    # 10 fills 30 days ago — outside 7-day lookback
    ts_old = (_NOW - timedelta(days=30)).isoformat()
    _write_audit(log, [
        {
            "timestamp_utc": ts_old,
            "stage": "filled",
            "origin_envelope_timestamp": (_NOW - timedelta(days=30, seconds=1800)).isoformat(),
        }
        for _ in range(10)
    ])
    stats = pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=_NOW)
    assert stats.sample_size == 0


def test_expired_records_counted_separately(tmp_path: Path, baseline_path: Path):
    log = tmp_path / "bridge.jsonl"
    records = [_fill_record(fill_offset_sec=100, origin_offset_sec=300)]
    records.extend([
        {
            "timestamp_utc": (_NOW - timedelta(seconds=200 + i)).isoformat(),
            "stage": "expired",
        }
        for i in range(3)
    ])
    _write_audit(log, records)
    stats = pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=_NOW)
    assert stats.sample_size == 1
    assert stats.expired_count == 3
    assert stats.expired_pct == 75.0  # 3 expired / (3+1 terminal) * 100


def test_filled_duplicate_suppressed_counts_as_sample(tmp_path: Path, baseline_path: Path):
    log = tmp_path / "bridge.jsonl"
    _write_audit(log, [
        _fill_record(fill_offset_sec=100, origin_offset_sec=400),
        {
            "timestamp_utc": (_NOW - timedelta(seconds=50)).isoformat(),
            "stage": "filled_duplicate_suppressed",
            "origin_envelope_timestamp": (_NOW - timedelta(seconds=200)).isoformat(),
        },
    ])
    stats = pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=_NOW)
    assert stats.sample_size == 2


def test_malformed_lines_tolerated(tmp_path: Path, baseline_path: Path):
    log = tmp_path / "bridge.jsonl"
    valid = _fill_record(fill_offset_sec=100, origin_offset_sec=400)
    log.write_text(
        "not-json\n"
        + json.dumps(valid)
        + "\n"
        + "{broken\n"
        + json.dumps({"stage": "filled"})  # missing required fields
        + "\n",
        encoding="utf-8",
    )
    stats = pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=_NOW)
    assert stats.sample_size == 1


def test_negative_latency_dropped_clock_skew(tmp_path: Path, baseline_path: Path):
    log = tmp_path / "bridge.jsonl"
    # Fill timestamp BEFORE origin — impossible without clock skew
    _write_audit(log, [{
        "timestamp_utc": (_NOW - timedelta(seconds=500)).isoformat(),
        "stage": "filled",
        "origin_envelope_timestamp": (_NOW - timedelta(seconds=100)).isoformat(),
    }])
    stats = pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=_NOW)
    assert stats.sample_size == 0


def test_baseline_suppresses_pre_fix_outliers(tmp_path: Path):
    """Records older than the audit baseline are excluded regardless of lookback.

    Real-world driver (2026-05-14): the 12.5.-14.5. bridge-cron outage
    produced 7 fills with 10h latency. Without baseline cutoff, the
    trigger would permanently fire on those even after the fix.

    Uses a fresh baseline path (NOT the autouse fixture) so the init
    code path runs and writes baseline_at=_NOW.
    """
    # Pre-fix outliers — 5h ago, 8h latency each → would fire trigger
    log = tmp_path / "bridge.jsonl"
    fresh_baseline = tmp_path / "fresh-baseline.json"  # not pre-written
    pre_fix_records = [
        _fill_record(fill_offset_sec=18000 + i, origin_offset_sec=18000 + i + 8 * 3600)
        for i in range(7)
    ]
    _write_audit(log, pre_fix_records)
    # First call writes baseline = _NOW → all pre-fix records get cut
    stats = pl.compute_latency_stats(
        audit_path=log, baseline_path=fresh_baseline, now=_NOW
    )
    assert stats.sample_size == 0
    assert stats.trigger_fired is False
    assert fresh_baseline.exists()


def test_baseline_file_persists_across_runs(tmp_path: Path, baseline_path: Path):
    """First call writes the baseline; second call reads the same baseline.

    Idempotency contract: the baseline anchor never shifts without operator
    action (manual delete of the marker file).
    """
    log = tmp_path / "bridge.jsonl"
    _write_audit(log, [_fill_record(fill_offset_sec=100, origin_offset_sec=400)])
    pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=_NOW)
    assert baseline_path.exists()
    first_payload = baseline_path.read_text(encoding="utf-8")
    # Second run at a later "now" must keep the original baseline timestamp
    later = _NOW + timedelta(days=2)
    pl.compute_latency_stats(audit_path=log, baseline_path=baseline_path, now=later)
    second_payload = baseline_path.read_text(encoding="utf-8")
    assert first_payload == second_payload


def test_percentile_at_boundary():
    # _percentile is internal but covered indirectly here via known data
    assert pl._percentile([], 50) is None
    assert pl._percentile([42.0], 50) == 42.0
    assert pl._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0
    assert pl._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 100) == 5.0
    p95 = pl._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 95)
    assert p95 is not None and 4.0 < p95 < 5.0
