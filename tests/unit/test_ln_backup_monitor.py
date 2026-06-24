"""Sprint 6 — SCB (channel.backup) drift monitor (resilience).

The static channel backup must be re-archived whenever channels change. This module
hashes the SCB and compares against a recorded baseline: ``no_baseline`` (first
run, records it), ``stable``, ``changed`` (→ operator re-backup reminder), or
``missing``. Read-only, fail-soft, no capital path.
"""

from __future__ import annotations

import json

from app.lightning.backup_monitor import ScbStatus, check_scb_drift, read_scb_status


def test_read_status_missing(tmp_path) -> None:
    s = read_scb_status(tmp_path / "channel.backup")
    assert isinstance(s, ScbStatus) and s.present is False and s.sha256 == ""


def test_read_status_present(tmp_path) -> None:
    p = tmp_path / "channel.backup"
    p.write_bytes(b"scb-bytes-v1")
    s = read_scb_status(p)
    assert s.present is True and s.size_bytes == 12 and len(s.sha256) == 64


def test_drift_no_baseline_records_then_stable(tmp_path) -> None:
    scb = tmp_path / "channel.backup"
    scb.write_bytes(b"v1")
    base = tmp_path / "scb_baseline.json"
    r1 = check_scb_drift(scb, baseline_path=base)
    assert r1["state"] == "no_baseline" and base.exists()
    # second run, unchanged → stable
    r2 = check_scb_drift(scb, baseline_path=base)
    assert r2["state"] == "stable"


def test_drift_changed_updates_baseline(tmp_path) -> None:
    scb = tmp_path / "channel.backup"
    scb.write_bytes(b"v1")
    base = tmp_path / "scb_baseline.json"
    check_scb_drift(scb, baseline_path=base)  # records v1
    scb.write_bytes(b"v2-after-channel-open")  # channel changed → SCB changed
    r = check_scb_drift(scb, baseline_path=base)
    assert r["state"] == "changed"
    assert r["reminder"]  # re-backup reminder surfaced
    # baseline advanced to the new hash → next run is stable again
    assert check_scb_drift(scb, baseline_path=base)["state"] == "stable"


def test_drift_missing_file(tmp_path) -> None:
    base = tmp_path / "scb_baseline.json"
    base.write_text(json.dumps({"sha256": "abc"}), encoding="utf-8")
    r = check_scb_drift(tmp_path / "gone.backup", baseline_path=base)
    assert r["state"] == "missing"
