"""Durable UC-3 timestamp job contracts; no network or funds."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpSHA256
from opentimestamps.core.serialize import BytesSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

from app.integrity.anchor import AnchorUnavailableError
from app.integrity.timestamp_jobs import (
    TimestampJobConflictError,
    TimestampJobStore,
    TimestampJobUnavailableError,
    mark_timestamp_job_confirmed,
)

PAYMENT_HASH = "a" * 64
DIGEST = "b" * 64


def _write_ots(path: Path, *, digest: str, confirmed: bool = False) -> bytes:
    timestamp = Timestamp(bytes.fromhex(digest))
    child = timestamp.ops.add(OpSHA256())
    child.attestations.add(PendingAttestation("https://calendar.example.invalid"))
    if confirmed:
        child.attestations.add(BitcoinBlockHeaderAttestation(900_000))
    context = BytesSerializationContext()
    DetachedTimestampFile(OpSHA256(), timestamp).serialize(context)
    payload = context.getbytes()
    path.write_bytes(payload)
    return payload


class FakeStamper:
    def __init__(self, *, fail_first: bool = False):
        self.calls = 0
        self._guard = threading.Lock()
        self.fail_first = fail_first

    def stamp(self, digest_hex: str, out_dir: Path, *, prefix: str = "audit") -> str:
        with self._guard:
            self.calls += 1
            call = self.calls
        if self.fail_first and call == 1:
            raise AnchorUnavailableError("calendar unavailable")
        proof = out_dir / f"{prefix}-{digest_hex[:16]}.ots"
        proof.write_bytes(b"proof:" + bytes.fromhex(digest_hex))
        return str(proof)


def test_retry_and_restart_return_identical_persisted_proof(tmp_path: Path) -> None:
    stamper = FakeStamper()
    first_record, first_proof = TimestampJobStore(tmp_path, stamper=stamper).submit(
        payment_hash=PAYMENT_HASH, digest=DIGEST
    )
    restarted = TimestampJobStore(tmp_path, stamper=FakeStamper())
    second_record, second_proof = restarted.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
    assert first_proof == second_proof
    assert first_record == second_record
    assert stamper.calls == 1
    assert first_record["state"] == "pending_bitcoin"
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("work"))


def test_concurrent_retry_submits_to_calendar_once(tmp_path: Path) -> None:
    stamper = FakeStamper()
    store = TimestampJobStore(tmp_path, stamper=stamper)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(store.submit, payment_hash=PAYMENT_HASH, digest=DIGEST) for _ in range(2)
        ]
    results = [future.result() for future in futures]
    assert stamper.calls == 1
    assert results[0] == results[1]


def test_payment_hash_cannot_be_rebound_to_another_digest(tmp_path: Path) -> None:
    store = TimestampJobStore(tmp_path, stamper=FakeStamper())
    store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
    with pytest.raises(TimestampJobConflictError):
        store.submit(payment_hash=PAYMENT_HASH, digest="c" * 64)


def test_failed_calendar_submission_is_retriable_without_orphan_workdir(tmp_path: Path) -> None:
    stamper = FakeStamper(fail_first=True)
    store = TimestampJobStore(tmp_path, stamper=stamper)
    with pytest.raises(TimestampJobUnavailableError):
        store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
    failed = json.loads((tmp_path / PAYMENT_HASH / "record.json").read_text(encoding="utf-8"))
    assert failed["state"] == "retryable_error"
    assert failed["last_error"] == "calendar_unavailable"
    record, proof = store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
    assert record["state"] == "pending_bitcoin" and proof
    assert stamper.calls == 2
    assert record["attempts"] == 2
    assert not list(tmp_path.rglob("work"))


def test_capacity_bounds_new_jobs_without_deleting_paid_proofs(tmp_path: Path) -> None:
    store = TimestampJobStore(tmp_path, stamper=FakeStamper(), max_jobs=1)
    first_record, first_proof = store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
    with pytest.raises(TimestampJobUnavailableError, match="capacity"):
        store.submit(payment_hash="c" * 64, digest="d" * 64)
    replay_record, replay_proof = store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
    assert (replay_record, replay_proof) == (first_record, first_proof)


def test_capacity_snapshot_reports_operator_thresholds(tmp_path: Path) -> None:
    store = TimestampJobStore(tmp_path, stamper=FakeStamper(), max_jobs=2)
    assert store.capacity_snapshot() == {
        "state": "ok",
        "used": 0,
        "max": 2,
        "available": 2,
        "utilization": 0.0,
    }
    store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
    warning = store.capacity_snapshot()
    assert warning["state"] == "ok" and warning["used"] == 1
    store.submit(payment_hash="c" * 64, digest="d" * 64)
    full = store.capacity_snapshot()
    assert full["state"] == "full" and full["available"] == 0


def test_existing_payment_binding_is_detected_without_calendar_call(tmp_path: Path) -> None:
    store = TimestampJobStore(tmp_path, stamper=FakeStamper())
    assert store.has_binding(payment_hash=PAYMENT_HASH, digest=DIGEST) is False
    store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
    assert store.has_binding(payment_hash=PAYMENT_HASH, digest=DIGEST) is True
    assert store.has_binding(payment_hash=PAYMENT_HASH, digest="c" * 64) is False


def test_restart_recovers_proof_written_before_completion_record(tmp_path: Path) -> None:
    job_dir = tmp_path / PAYMENT_HASH
    job_dir.mkdir(parents=True)
    expected_proof = _write_ots(job_dir / "proof.ots", digest=DIGEST)
    (job_dir / "record.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "payment_hash": PAYMENT_HASH,
                "digest": DIGEST,
                "state": "submitting",
                "created_at": "2026-09-23T10:00:00+00:00",
                "updated_at": "2026-09-23T10:00:00+00:00",
                "attempts": 1,
            }
        ),
        encoding="utf-8",
    )
    stamper = FakeStamper()
    record, proof = TimestampJobStore(tmp_path, stamper=stamper).submit(
        payment_hash=PAYMENT_HASH, digest=DIGEST
    )
    assert proof == expected_proof
    assert record["state"] == "pending_bitcoin"
    assert stamper.calls == 0


def test_corrupt_completed_record_fails_closed_without_resubmission(tmp_path: Path) -> None:
    job_dir = tmp_path / PAYMENT_HASH
    job_dir.mkdir(parents=True)
    (job_dir / "record.json").write_text("not-json", encoding="utf-8")
    stamper = FakeStamper()
    with pytest.raises(TimestampJobUnavailableError, match="unreadable"):
        TimestampJobStore(tmp_path, stamper=stamper).submit(
            payment_hash=PAYMENT_HASH, digest=DIGEST
        )
    assert stamper.calls == 0


def test_confirmed_upgrade_updates_hash_and_remains_replayable(tmp_path: Path) -> None:
    first_stamper = FakeStamper()
    TimestampJobStore(tmp_path, stamper=first_stamper).submit(
        payment_hash=PAYMENT_HASH, digest=DIGEST
    )
    proof_path = tmp_path / PAYMENT_HASH / "proof.ots"
    confirmed_proof = _write_ots(proof_path, digest=DIGEST, confirmed=True)

    assert mark_timestamp_job_confirmed(proof_path) is True

    restarted_stamper = FakeStamper()
    record, proof = TimestampJobStore(tmp_path, stamper=restarted_stamper).submit(
        payment_hash=PAYMENT_HASH, digest=DIGEST
    )
    assert record["state"] == "bitcoin_confirmed"
    assert record["confirmed_at"]
    assert proof == confirmed_proof
    assert restarted_stamper.calls == 0


def test_hash_mismatch_heals_only_when_ots_digest_matches(tmp_path: Path) -> None:
    store = TimestampJobStore(tmp_path, stamper=FakeStamper())
    store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
    proof_path = tmp_path / PAYMENT_HASH / "proof.ots"
    expected = _write_ots(proof_path, digest=DIGEST, confirmed=True)

    record, proof = store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)

    assert proof == expected
    assert record["state"] == "bitcoin_confirmed"
    assert record["proof_sha256"] == hashlib.sha256(expected).hexdigest()


def test_hash_mismatch_with_wrong_ots_digest_fails_closed(tmp_path: Path) -> None:
    store = TimestampJobStore(tmp_path, stamper=FakeStamper())
    store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
    _write_ots(tmp_path / PAYMENT_HASH / "proof.ots", digest="c" * 64, confirmed=True)

    with pytest.raises(TimestampJobUnavailableError, match="unreadable"):
        store.submit(payment_hash=PAYMENT_HASH, digest=DIGEST)
