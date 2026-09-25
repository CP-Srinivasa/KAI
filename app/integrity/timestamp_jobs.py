"""Durable, idempotent UC-3 OpenTimestamps jobs keyed by L402 payment hash."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.core.file_lock import append_lock
from app.integrity.anchor import AnchorUnavailableError, OpenTimestampsStamper

DEFAULT_MAX_TIMESTAMP_JOBS = 10_000


class TimestampJobConflictError(RuntimeError):
    """A payment hash was already bound to another digest."""


class TimestampJobUnavailableError(RuntimeError):
    """The calendar submission did not produce a durable proof."""


class TimestampJobCapacityError(TimestampJobUnavailableError):
    """No durable slot remains for a new paid timestamp job."""


class _Stamper(Protocol):
    def stamp(self, digest_hex: str, out_dir: Path, *, prefix: str = "audit") -> str: ...


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_hash(value: str, *, name: str) -> str:
    result = value.strip().lower()
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise ValueError(f"{name} must be 32-byte hex")
    return result


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_bytes(
        path,
        (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(),
    )


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _proof_matches_digest(path: Path, digest: str) -> bool:
    """Verify the digest embedded in a detached OTS proof before record healing."""
    try:
        from opentimestamps.core.serialize import BytesDeserializationContext
        from opentimestamps.core.timestamp import DetachedTimestampFile

        detached = DetachedTimestampFile.deserialize(BytesDeserializationContext(path.read_bytes()))
        return bytes(detached.file_digest) == bytes.fromhex(digest)
    except (ImportError, OSError, ValueError):
        raise
    except Exception:  # noqa: BLE001 - corrupt/untrusted proof fails closed
        return False


class TimestampJobStore:
    """Durable idempotent submission for one payment-hash/digest binding.

    A strict cross-process sidecar lock covers record lookup, submission and atomic
    persistence. Completed jobs are replayed byte-for-byte after retries/restarts;
    an external calendar may still observe a retry after a crash around its call.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        stamper: _Stamper | None = None,
        max_jobs: int = DEFAULT_MAX_TIMESTAMP_JOBS,
    ):
        self.root = Path(root)
        self.stamper = stamper or OpenTimestampsStamper()
        self.max_jobs = int(max_jobs)
        if self.max_jobs <= 0:
            raise ValueError("max_jobs must be positive")

    def has_capacity(self, *, payment_hash: str | None = None, reserve: int = 0) -> bool:
        """Advisory pre-mint check; existing jobs always fit.

        Lock-free on purpose: it runs for unpaid callers, and a global strict
        lock there would let an anonymous flood stall paid ``submit`` calls. The
        hard bound is enforced under the lock in :meth:`submit`. ``reserve``
        keeps headroom for invoices already issued (challenges reserve nothing),
        so a paid caller does not lose the race for the last slot.
        """
        canonical = _canonical_hash(payment_hash, name="payment_hash") if payment_hash else None
        if canonical is not None and (self.root / canonical).is_dir():
            return True
        return self._job_count() + max(0, int(reserve)) < self.max_jobs

    def has_binding(self, *, payment_hash: str, digest: str) -> bool:
        """Return whether a paid job already durably binds this hash and digest."""
        payment_hash = _canonical_hash(payment_hash, name="payment_hash")
        digest = _canonical_hash(digest, name="digest")
        record_path = self.root / payment_hash / "record.json"
        if not record_path.exists():
            return False
        with append_lock(record_path, strict=True):
            record = _read_record(record_path)
            return bool(record is not None and record.get("digest") == digest)

    def capacity_snapshot(self) -> dict[str, int | float | str]:
        """Return a read-only operator-health view of durable UC-3 capacity."""
        with append_lock(self.root / ".capacity", strict=True):
            used = self._job_count()
        available = max(0, self.max_jobs - used)
        utilization = used / self.max_jobs
        state = "full" if available == 0 else "warning" if utilization >= 0.8 else "ok"
        return {
            "state": state,
            "used": used,
            "max": self.max_jobs,
            "available": available,
            "utilization": utilization,
        }

    def _job_count(self) -> int:
        return (
            sum(
                1
                for path in self.root.iterdir()
                if path.is_dir()
                and len(path.name) == 64
                and all(char in "0123456789abcdef" for char in path.name)
            )
            if self.root.exists()
            else 0
        )

    def submit(self, *, payment_hash: str, digest: str) -> tuple[dict[str, Any], bytes]:
        payment_hash = _canonical_hash(payment_hash, name="payment_hash")
        digest = _canonical_hash(digest, name="digest")
        job_dir = self.root / payment_hash
        record_path = job_dir / "record.json"
        proof_path = job_dir / "proof.ots"
        work_dir = job_dir / "work"
        # Existing paid jobs take the fast replay path and never scan the whole
        # store. New jobs reserve a bounded slot under the global capacity lock.
        if not job_dir.exists():
            with append_lock(self.root / ".capacity", strict=True):
                if not job_dir.exists():
                    if self._job_count() >= self.max_jobs:
                        raise TimestampJobCapacityError("timestamp job capacity reached")
                    job_dir.mkdir(parents=True, exist_ok=True)

        with append_lock(record_path, strict=True):
            record = _read_record(record_path)
            if record is None and record_path.exists():
                raise TimestampJobUnavailableError("timestamp job record is unreadable")
            if record is not None and record.get("digest") != digest:
                raise TimestampJobConflictError("payment hash is bound to another digest")
            if record is not None and record.get("state") in {
                "pending_bitcoin",
                "bitcoin_confirmed",
            }:
                try:
                    proof = proof_path.read_bytes()
                except OSError:
                    proof = b""
                if proof and hashlib.sha256(proof).hexdigest() == record.get("proof_sha256"):
                    return record, proof
                if proof and _proof_matches_digest(proof_path, digest):
                    from app.integrity.upgrade import CONFIRMED, read_proof_info

                    state = (
                        "bitcoin_confirmed"
                        if read_proof_info(proof_path).state == CONFIRMED
                        else "pending_bitcoin"
                    )
                    now = _utc_now()
                    healed = {
                        **record,
                        "state": state,
                        "updated_at": now,
                        "proof_sha256": hashlib.sha256(proof).hexdigest(),
                    }
                    if state == "bitcoin_confirmed":
                        healed["confirmed_at"] = record.get("confirmed_at", now)
                    _atomic_json(record_path, healed)
                    return healed, proof
                raise TimestampJobUnavailableError("persisted timestamp proof is unreadable")
            if record is not None and record.get("state") == "submitting":
                try:
                    proof = proof_path.read_bytes()
                except OSError:
                    proof = b""
                if proof and _proof_matches_digest(proof_path, digest):
                    recovered = {
                        **record,
                        "state": "pending_bitcoin",
                        "updated_at": _utc_now(),
                        "proof_sha256": hashlib.sha256(proof).hexdigest(),
                    }
                    _atomic_json(record_path, recovered)
                    return recovered, proof

            created_at = str(record.get("created_at")) if record else _utc_now()
            attempts = int(record.get("attempts", 0)) + 1 if record else 1
            job_dir.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(work_dir, ignore_errors=True)
            work_dir.mkdir(parents=True, exist_ok=True)
            submitting = {
                "schema_version": 1,
                "payment_hash": payment_hash,
                "digest": digest,
                "state": "submitting",
                "created_at": created_at,
                "updated_at": _utc_now(),
                "attempts": attempts,
            }
            _atomic_json(record_path, submitting)
            try:
                generated_path = Path(
                    self.stamper.stamp(digest, work_dir, prefix=f"uc3-{payment_hash[:16]}")
                )
                proof = generated_path.read_bytes()
                if not proof:
                    raise TimestampJobUnavailableError("calendar returned an empty proof")
                _atomic_bytes(proof_path, proof)
                complete = {
                    "schema_version": 1,
                    "payment_hash": payment_hash,
                    "digest": digest,
                    "state": "pending_bitcoin",
                    "created_at": created_at,
                    "updated_at": _utc_now(),
                    "attempts": attempts,
                    "proof_sha256": hashlib.sha256(proof).hexdigest(),
                }
                _atomic_json(record_path, complete)
                return complete, proof
            except TimestampJobUnavailableError as exc:
                _atomic_json(
                    record_path,
                    {**submitting, "state": "retryable_error", "last_error": "proof_unavailable"},
                )
                raise exc
            except AnchorUnavailableError as exc:
                _atomic_json(
                    record_path,
                    {
                        **submitting,
                        "state": "retryable_error",
                        "last_error": "calendar_unavailable",
                    },
                )
                raise TimestampJobUnavailableError(str(exc)) from exc
            except (OSError, ValueError) as exc:
                raise TimestampJobUnavailableError("proof persistence failed") from exc
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)


def mark_timestamp_job_confirmed(proof_path: Path) -> bool:
    """Atomically reconcile a UC-3 job after its OTS proof is Bitcoin-confirmed.

    The generic upgrader rewrites ``proof.ots``.  Updating the sibling record in
    the same lock domain keeps subsequent paid retries replayable and prevents a
    stale proof hash from turning a successful upgrade into a false 503.
    """
    proof_path = Path(proof_path)
    if proof_path.name != "proof.ots":
        return False
    record_path = proof_path.with_name("record.json")
    if not record_path.exists():
        return False
    with append_lock(record_path, strict=True):
        record = _read_record(record_path)
        if record is None or record.get("state") not in {
            "submitting",
            "retryable_error",
            "pending_bitcoin",
            "bitcoin_confirmed",
        }:
            return False
        try:
            proof = proof_path.read_bytes()
        except OSError:
            return False
        if not proof:
            return False
        proof_sha256 = hashlib.sha256(proof).hexdigest()
        already_reconciled = (
            record.get("state") == "bitcoin_confirmed"
            and record.get("proof_sha256") == proof_sha256
        )
        if already_reconciled:
            # Already reconciled: the upgrader calls this on every pass, so a
            # rewrite here would only churn fsyncs on the Pi's SD card.
            return True
        digest = record.get("digest")
        if not isinstance(digest, str) or not _proof_matches_digest(proof_path, digest):
            return False
        now = _utc_now()
        confirmed = {
            **record,
            "state": "bitcoin_confirmed",
            "updated_at": now,
            "confirmed_at": record.get("confirmed_at", now),
            "proof_sha256": proof_sha256,
        }
        _atomic_json(record_path, confirmed)
        return True


__all__ = [
    "DEFAULT_MAX_TIMESTAMP_JOBS",
    "TimestampJobCapacityError",
    "TimestampJobConflictError",
    "TimestampJobStore",
    "TimestampJobUnavailableError",
    "mark_timestamp_job_confirmed",
]
