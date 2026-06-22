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

# Public OpenTimestamps calendars (redundant: a proof is usable if ANY commits).
_OTS_CALENDARS: tuple[str, ...] = (
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
)
_OTS_TIMEOUT_S = 15.0


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
            from opentimestamps.core.serialize import BytesSerializationContext
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
        # Submit to public calendars and MERGE each returned commitment INTO the
        # timestamp. Without the merge the serialized .ots carries no calendar
        # attestation and can never be upgraded to a Bitcoin proof — i.e. it would
        # prove nothing (the whole point of L3). Best-effort per calendar (one may
        # be down); at least one commitment is required for a usable proof. The
        # proof becomes Bitcoin-verifiable later via ``ots upgrade`` once the
        # calendar's aggregation is mined.
        committed = 0
        for url in _OTS_CALENDARS:
            try:
                calendar_ts = RemoteCalendar(url).submit(digest, timeout=_OTS_TIMEOUT_S)
                ts.merge(calendar_ts)
                committed += 1
            except Exception:  # noqa: BLE001 — a single calendar outage is tolerable
                continue
        if committed == 0:
            raise AnchorUnavailableError("no OpenTimestamps calendar accepted the digest")
        # Serialize via the OTS BytesSerializationContext (the prior _FileWriter
        # adapter only had .write() and crashed on the serializer's write_bytes/
        # varuint API — the stamper never actually produced a proof).
        detached = DetachedTimestampFile(OpSHA256(), ts)
        ctx = BytesSerializationContext()
        detached.serialize(ctx)
        out_dir.mkdir(parents=True, exist_ok=True)
        proof_path = out_dir / f"audit-{digest_hex[:16]}.ots"
        proof_path.write_bytes(ctx.getbytes())
        return str(proof_path)


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
    # Per-file byte sizes at anchor time enable the append-only prefix check in
    # the freshness probe: an append-only audit log may only GROW, so its bytes
    # up to ``sizes[path]`` must still hash to ``files[path]`` — a shrink or a
    # changed prefix is tamper, not normal growth.
    sizes: dict[str, int] = {}
    for path in ad.files:
        try:
            sizes[path] = Path(path).stat().st_size
        except OSError:
            continue
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "digest": ad.digest,
        "files": ad.files,
        "sizes": sizes,
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
