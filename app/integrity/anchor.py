"""Audit-digest anchoring (KAI L3) — default-off, dependency-safe.

Pipeline: compute the audit digest (see ``digest.py``) → hand it to a Stamper.
- ``NullStamper`` (default): records the digest, anchors nothing.
- ``OpenTimestampsStamper``: creates an OTS proof via the optional
  ``opentimestamps`` library (imported lazily, so it is NOT a hard dependency
  until the operator enables ``stamper="opentimestamps"``).

``anchor_audit_digest`` never raises into a caller: when disabled it is a no-op,
and a stamper failure is captured in the returned result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.core.integrity_settings import IntegritySettings
from app.integrity.digest import compute_audit_digest


class AnchorUnavailableError(RuntimeError):
    """Raised by a stamper when it cannot produce a proof."""


@dataclass(frozen=True)
class AnchorResult:
    state: str  # "disabled" | "recorded" | "anchored" | "error"
    digest: str = ""
    proof_path: str = ""
    reason: str = ""


class Stamper(Protocol):
    name: str

    def stamp(self, digest_hex: str, out_dir: Path) -> str:
        """Produce a proof for ``digest_hex``; return the proof file path (or "")."""
        ...


class NullStamper:
    """Records the digest only — anchors nothing (dry inventory)."""

    name = "null"

    def stamp(self, digest_hex: str, out_dir: Path) -> str:  # noqa: D102
        return ""


class OpenTimestampsStamper:
    """Anchors the digest via OpenTimestamps calendar servers (optional dep)."""

    name = "opentimestamps"

    def stamp(self, digest_hex: str, out_dir: Path) -> str:  # noqa: D102
        try:
            import opentimestamps  # noqa: F401
            from opentimestamps.calendar import RemoteCalendar
            from opentimestamps.core.op import OpSHA256
            from opentimestamps.core.timestamp import (
                DetachedTimestampFile,
                Timestamp,
            )
        except ImportError as exc:  # optional dependency not installed
            raise AnchorUnavailableError(
                "opentimestamps library not installed (pip install opentimestamps)"
            ) from exc

        digest = bytes.fromhex(digest_hex)
        ts = Timestamp(digest)
        # Submit to a public calendar; the proof becomes verifiable once a
        # calendar commitment is bitcoin-anchored (upgrade later via `ots upgrade`).
        calendar = RemoteCalendar("https://alice.btc.calendar.opentimestamps.org")
        calendar.submit(digest)
        detached = DetachedTimestampFile(OpSHA256(), ts)
        out_dir.mkdir(parents=True, exist_ok=True)
        proof_path = out_dir / f"audit-{digest_hex[:16]}.ots"
        with proof_path.open("wb") as fh:
            detached.serialize(_FileWriter(fh))
        return str(proof_path)


class _FileWriter:
    """Tiny adapter so opentimestamps' serializer can write to a file object."""

    def __init__(self, fh) -> None:
        self._fh = fh

    def write(self, data: bytes) -> None:
        self._fh.write(data)


def _make_stamper(name: str) -> Stamper:
    if name == "opentimestamps":
        return OpenTimestampsStamper()
    return NullStamper()


def anchor_audit_digest(cfg: IntegritySettings) -> AnchorResult:
    """Compute the audit digest and (optionally) anchor it. Never raises."""
    if not cfg.enabled:
        return AnchorResult(state="disabled")

    ad = compute_audit_digest(cfg.audit_paths)
    out_dir = Path(cfg.proofs_dir)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "digest": ad.digest,
        "files": ad.files,
        "missing": ad.missing,
        "stamper": cfg.stamper,
    }
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"audit-{ad.digest[:16]}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
        )
        proof = _make_stamper(cfg.stamper).stamp(ad.digest, out_dir)
    except AnchorUnavailableError as exc:
        return AnchorResult(state="error", digest=ad.digest, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 — anchoring must never crash the caller
        return AnchorResult(state="error", digest=ad.digest, reason=f"unexpected: {exc}")

    if proof:
        return AnchorResult(state="anchored", digest=ad.digest, proof_path=proof)
    return AnchorResult(state="recorded", digest=ad.digest)
