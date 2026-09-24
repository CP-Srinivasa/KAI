"""Unit tests for L3 OTS proof upgrade + status classification.

The anchor writes a PENDING .ots (calendar commitment, not yet Bitcoin-mined).
This module's job is the asynchronous upgrade (pending → Bitcoin-confirmed once
mined) and the pending-vs-confirmed classification the read surface needs.

Network is never touched: a fake calendar factory is injected. OTS proofs are
built with the real ``opentimestamps`` lib (a hard dependency) so the
serialize/deserialize/classify path is the real one.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpSHA256
from opentimestamps.core.serialize import BytesSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

from app.core.integrity_settings import IntegritySettings
from app.integrity import get_integrity_status
from app.integrity.upgrade import (
    UpgradeReport,
    read_proof_info,
    upgrade_pending_proofs,
)

_CAL = "https://alice.btc.calendar.opentimestamps.org"


def _write_proof(path: Path, *, digest: bytes, confirmed_height: int | None = None) -> None:
    """Write a .ots in the same format anchor.stamp() does: a pending calendar
    commitment, optionally already carrying a Bitcoin attestation (confirmed)."""
    ts = Timestamp(digest)
    sub = ts.ops.add(OpSHA256())
    sub.attestations.add(PendingAttestation(_CAL))
    if confirmed_height is not None:
        sub.attestations.add(BitcoinBlockHeaderAttestation(confirmed_height))
    ctx = BytesSerializationContext()
    DetachedTimestampFile(OpSHA256(), ts).serialize(ctx)
    path.write_bytes(ctx.getbytes())


def _fake_calendar_factory(height: int):
    """Factory whose calendar returns an upgraded timestamp with a Bitcoin
    attestation for the queried commitment (simulates a mined aggregation)."""

    class _Cal:
        def __init__(self, uri: str) -> None:
            self.uri = uri

        def get_timestamp(self, commitment: bytes, timeout=None):  # noqa: ANN001
            up = Timestamp(commitment)
            up.attestations.add(BitcoinBlockHeaderAttestation(height))
            return up

    return _Cal


def _raising_calendar_factory(exc: Exception):
    class _Cal:
        def __init__(self, uri: str) -> None:
            self.uri = uri

        def get_timestamp(self, commitment: bytes, timeout=None):  # noqa: ANN001
            raise exc

    return _Cal


# --- classification --------------------------------------------------------------


def test_read_proof_info_pending(tmp_path) -> None:
    p = tmp_path / "audit-abc.ots"
    _write_proof(p, digest=b"\x11" * 32)
    info = read_proof_info(p)
    assert info.state == "pending"
    assert info.bitcoin_height is None


def test_read_proof_info_confirmed(tmp_path) -> None:
    p = tmp_path / "audit-abc.ots"
    _write_proof(p, digest=b"\x22" * 32, confirmed_height=820634)
    info = read_proof_info(p)
    assert info.state == "confirmed"
    assert info.bitcoin_height == 820634


def test_read_proof_info_unreadable(tmp_path) -> None:
    p = tmp_path / "audit-garbage.ots"
    p.write_bytes(b"not an ots proof")
    info = read_proof_info(p)
    assert info.state == "unreadable"


# --- upgrade pass ----------------------------------------------------------------


def test_upgrade_confirms_pending_proof(tmp_path) -> None:
    p = tmp_path / "audit-1.ots"
    _write_proof(p, digest=b"\x33" * 32)
    assert read_proof_info(p).state == "pending"

    report = upgrade_pending_proofs(tmp_path, calendar_factory=_fake_calendar_factory(800000))
    assert isinstance(report, UpgradeReport)
    assert report.scanned == 1
    assert report.upgraded == 1
    assert report.still_pending == 0
    assert report.failed == 0
    # The on-disk proof is now Bitcoin-confirmed (and persisted).
    info = read_proof_info(p)
    assert info.state == "confirmed"
    assert info.bitcoin_height == 800000


def test_upgrade_keeps_pending_when_calendar_unavailable(tmp_path) -> None:
    p = tmp_path / "audit-2.ots"
    _write_proof(p, digest=b"\x44" * 32)

    report = upgrade_pending_proofs(
        tmp_path, calendar_factory=_raising_calendar_factory(RuntimeError("not mined yet"))
    )
    assert report.scanned == 1
    assert report.upgraded == 0
    assert report.still_pending == 1
    assert report.failed == 0
    assert read_proof_info(p).state == "pending"  # unchanged, fail-soft


def test_upgrade_skips_already_confirmed_without_calendar_call(tmp_path) -> None:
    p = tmp_path / "audit-3.ots"
    _write_proof(p, digest=b"\x55" * 32, confirmed_height=700000)

    def _must_not_call(uri):  # noqa: ANN001
        raise AssertionError("calendar must not be queried for a confirmed proof")

    report = upgrade_pending_proofs(tmp_path, calendar_factory=_must_not_call)
    assert report.scanned == 1
    assert report.already_confirmed == 1
    assert report.upgraded == 0
    assert report.still_pending == 0


def test_upgrade_empty_dir_is_noop(tmp_path) -> None:
    report = upgrade_pending_proofs(tmp_path, calendar_factory=_fake_calendar_factory(1))
    assert report.scanned == 0 and report.upgraded == 0


def test_upgrade_counts_unreadable_as_failed(tmp_path) -> None:
    (tmp_path / "audit-bad.ots").write_bytes(b"garbage")
    report = upgrade_pending_proofs(tmp_path, calendar_factory=_fake_calendar_factory(1))
    assert report.scanned == 1
    assert report.failed == 1
    assert report.upgraded == 0


def test_upgrade_discovers_nested_uc3_proof_and_reconciles_job(tmp_path) -> None:
    job_dir = tmp_path / ("a" * 64)
    job_dir.mkdir()
    proof_path = job_dir / "proof.ots"
    _write_proof(proof_path, digest=b"\x66" * 32)
    (job_dir / "record.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "payment_hash": "a" * 64,
                "digest": "66" * 32,
                "state": "pending_bitcoin",
                "created_at": "2026-09-23T10:00:00+00:00",
                "updated_at": "2026-09-23T10:00:00+00:00",
                "attempts": 1,
                "proof_sha256": "stale-after-upgrade",
            }
        ),
        encoding="utf-8",
    )

    report = upgrade_pending_proofs(tmp_path, calendar_factory=_fake_calendar_factory(900000))

    assert report.scanned == 1 and report.upgraded == 1
    record = json.loads((job_dir / "record.json").read_text(encoding="utf-8"))
    assert record["state"] == "bitcoin_confirmed"
    assert record["confirmed_at"]
    assert read_proof_info(proof_path).bitcoin_height == 900000


def test_confirmed_uc3_proof_heals_record_on_next_upgrade_pass(tmp_path) -> None:
    job_dir = tmp_path / ("a" * 64)
    job_dir.mkdir()
    proof_path = job_dir / "proof.ots"
    _write_proof(proof_path, digest=b"\x77" * 32)
    (job_dir / "record.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "payment_hash": "a" * 64,
                "digest": "77" * 32,
                "state": "pending_bitcoin",
                "created_at": "2026-09-23T10:00:00+00:00",
                "updated_at": "2026-09-23T10:00:00+00:00",
                "attempts": 1,
                "proof_sha256": "pre-upgrade-hash",
            }
        ),
        encoding="utf-8",
    )

    with patch("app.integrity.timestamp_jobs.mark_timestamp_job_confirmed", return_value=False):
        first = upgrade_pending_proofs(tmp_path, calendar_factory=_fake_calendar_factory(900001))
    assert first.failed == 1 and first.upgraded == 0

    second = upgrade_pending_proofs(tmp_path, calendar_factory=_fake_calendar_factory(900001))
    assert second.already_confirmed == 1 and second.failed == 0
    record = json.loads((job_dir / "record.json").read_text(encoding="utf-8"))
    assert record["state"] == "bitcoin_confirmed"


def _pending_job(tmp_path: Path, digest_byte: bytes) -> Path:
    job_dir = tmp_path / ("a" * 64)
    job_dir.mkdir()
    _write_proof(job_dir / "proof.ots", digest=digest_byte * 32)
    (job_dir / "record.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "payment_hash": "a" * 64,
                "digest": digest_byte.hex() * 32,
                "state": "pending_bitcoin",
                "created_at": "2026-09-23T10:00:00+00:00",
                "updated_at": "2026-09-23T10:00:00+00:00",
                "attempts": 1,
                "proof_sha256": "pre-upgrade-hash",
            }
        ),
        encoding="utf-8",
    )
    return job_dir


def test_uc3_record_heals_after_mark_raised_in_previous_pass(tmp_path) -> None:
    """A lock/IO error while reconciling must not strand the paid job forever."""
    job_dir = _pending_job(tmp_path, b"\x99")

    with patch(
        "app.integrity.timestamp_jobs.mark_timestamp_job_confirmed",
        side_effect=OSError("record lock unavailable"),
    ):
        first = upgrade_pending_proofs(tmp_path, calendar_factory=_fake_calendar_factory(900002))
    assert first.failed == 1

    second = upgrade_pending_proofs(tmp_path, calendar_factory=_fake_calendar_factory(900002))
    assert second.already_confirmed == 1 and second.failed == 0
    record = json.loads((job_dir / "record.json").read_text(encoding="utf-8"))
    assert record["state"] == "bitcoin_confirmed"


def test_reconciled_uc3_record_is_not_rewritten_on_every_pass(tmp_path) -> None:
    """Confirmed + matching hash is a no-op: no fsync churn on the SD card."""
    job_dir = _pending_job(tmp_path, b"\xaa")
    upgrade_pending_proofs(tmp_path, calendar_factory=_fake_calendar_factory(900003))
    record_path = job_dir / "record.json"
    settled = record_path.read_bytes()
    assert json.loads(settled)["state"] == "bitcoin_confirmed"

    again = upgrade_pending_proofs(tmp_path, calendar_factory=_fake_calendar_factory(900003))
    assert again.already_confirmed == 1 and again.failed == 0
    assert record_path.read_bytes() == settled


def test_upgrade_ignores_transient_work_proofs(tmp_path) -> None:
    work = tmp_path / ("a" * 64) / "work"
    work.mkdir(parents=True)
    _write_proof(work / "temporary.ots", digest=b"\x88" * 32)
    assert upgrade_pending_proofs(tmp_path).scanned == 0


# --- status surface: pending vs Bitcoin-confirmed --------------------------------


def _anchor_dir(tmp_path: Path, *, confirmed_height: int | None = None) -> Path:
    """A proofs dir with one anchor record + matching .ots (pending or confirmed)."""
    out = tmp_path / "out"
    out.mkdir()
    digest_hex = "ab" * 32  # 64 hex chars
    (out / f"audit-{digest_hex[:16]}.json").write_text(
        json.dumps({"ts": "2026-06-20T00:00:00+00:00", "digest": digest_hex}), encoding="utf-8"
    )
    _write_proof(
        out / f"audit-{digest_hex[:16]}.ots",
        digest=bytes.fromhex(digest_hex),
        confirmed_height=confirmed_height,
    )
    return out


def test_status_reports_proof_state_pending(tmp_path) -> None:
    out = _anchor_dir(tmp_path)
    s = get_integrity_status(IntegritySettings(enabled=True, proofs_dir=str(out)))
    assert s.state == "ok"
    assert s.proof_available is True
    assert s.proof_state == "pending"
    assert s.bitcoin_height is None


def test_status_reports_proof_state_confirmed(tmp_path) -> None:
    out = _anchor_dir(tmp_path, confirmed_height=820634)
    s = get_integrity_status(IntegritySettings(enabled=True, proofs_dir=str(out)))
    assert s.state == "ok"
    assert s.proof_available is True
    assert s.proof_state == "confirmed"
    assert s.bitcoin_height == 820634
