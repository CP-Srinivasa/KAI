"""Durable, idempotent UC-3 OpenTimestamps jobs keyed by L402 payment hash."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


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


class TimestampJobStore:
    """Exactly-once calendar submission for one payment-hash/digest binding.

    A strict cross-process sidecar lock covers record lookup, submission and atomic
    persistence. Completed jobs are replayed byte-for-byte after retries/restarts.
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

    def submit(self, *, payment_hash: str, digest: str) -> tuple[dict[str, Any], bytes]:
        payment_hash = _canonical_hash(payment_hash, name="payment_hash")
        digest = _canonical_hash(digest, name="digest")
        job_dir = self.root / payment_hash
        record_path = job_dir / "record.json"
        proof_path = job_dir / "proof.ots"
        work_dir = job_dir / "work"
        # Reserve a bounded durable slot before the external call. Existing paid
        # jobs always remain retrievable; at capacity, new mints fail closed.
        with append_lock(self.root / ".capacity", strict=True):
            existing = (
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
            if not job_dir.exists() and existing >= self.max_jobs:
                raise TimestampJobUnavailableError("timestamp job capacity reached")
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
                raise TimestampJobUnavailableError("persisted timestamp proof is unreadable")
            if record is not None and record.get("state") == "submitting":
                try:
                    proof = proof_path.read_bytes()
                except OSError:
                    proof = b""
                if proof:
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
        now = _utc_now()
        confirmed = {
            **record,
            "state": "bitcoin_confirmed",
            "updated_at": now,
            "confirmed_at": record.get("confirmed_at", now),
            "proof_sha256": hashlib.sha256(proof).hexdigest(),
        }
        _atomic_json(record_path, confirmed)
        return True


__all__ = [
    "DEFAULT_MAX_TIMESTAMP_JOBS",
    "TimestampJobConflictError",
    "TimestampJobStore",
    "TimestampJobUnavailableError",
    "mark_timestamp_job_confirmed",
]
