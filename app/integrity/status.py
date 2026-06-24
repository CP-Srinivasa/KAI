"""Read-only L3 audit-integrity status (KAI L3).

The only read surface the rest of KAI imports for anchor status. Mirrors the
chain/lightning adapters and follows the same guarantees:

  * **default-off** — when ``settings.integrity.enabled`` is False it returns a
    ``disabled`` status without touching the filesystem.
  * **pure read** — it NEVER computes a digest or writes a proof (that is
    :func:`app.integrity.anchor.anchor_audit_digest`'s job); it only reads the
    ``audit-*.json`` records the anchor run already wrote.
  * **fail-soft** — any filesystem/parse error is surfaced as ``unavailable``;
    it never raises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.integrity_settings import IntegritySettings
from app.core.settings import get_settings


@dataclass(frozen=True)
class IntegrityStatus:
    """Snapshot of KAI's L3 audit-anchoring state.

    ``state``: ``disabled`` (feature off) / ``no_anchor`` (enabled but nothing
    anchored yet) / ``ok`` (a latest anchor record was found) / ``unavailable``
    (the proofs dir could not be read). ``proof_available`` is True when an
    OpenTimestamps ``.ots`` proof exists for the latest digest (else the digest
    is only recorded, not yet on-chain-anchored).
    """

    state: str
    enabled: bool
    stamper: str = ""
    proofs_dir: str = ""
    anchor_count: int = 0
    last_digest: str = ""
    last_anchored_at: str = ""
    proof_available: bool = False
    # OTS proof state of the latest digest: "" (no proof / null stamper),
    # "pending" (calendar commitment, not yet Bitcoin-mined), "confirmed"
    # (Bitcoin attestation present), "unreadable"/"unknown" (corrupt / lib absent).
    proof_state: str = ""
    bitcoin_height: int | None = None
    reason: str = ""


def get_integrity_status(cfg: IntegritySettings | None = None) -> IntegrityStatus:
    """Return the current L3 anchor status, never raising.

    Args:
        cfg: optional settings override (tests). Defaults to the cached app
             settings' ``integrity`` section.
    """
    cfg = cfg or get_settings().integrity
    if not cfg.enabled:
        return IntegrityStatus(
            state="disabled", enabled=False, stamper=cfg.stamper, proofs_dir=cfg.proofs_dir
        )

    out_dir = Path(cfg.proofs_dir)
    try:
        records = sorted(out_dir.glob("audit-*.json"))
    except OSError as exc:
        return IntegrityStatus(
            state="unavailable",
            enabled=True,
            stamper=cfg.stamper,
            proofs_dir=cfg.proofs_dir,
            reason=str(exc),
        )

    parsed: list[dict[str, Any]] = []
    for path in records:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # skip a corrupt/unreadable record, don't fail the whole read
        if isinstance(data, dict):
            parsed.append(data)

    if not parsed:
        return IntegrityStatus(
            state="no_anchor",
            enabled=True,
            stamper=cfg.stamper,
            proofs_dir=cfg.proofs_dir,
            anchor_count=len(records),
        )

    latest = max(parsed, key=lambda d: str(d.get("ts", "")))
    digest = str(latest.get("digest", ""))
    # The OTS stamper writes audit-<digest[:16]>.ots alongside the json record.
    proof_path = out_dir / f"audit-{digest[:16]}.ots"
    proof_available = bool(digest) and proof_path.exists()
    # Classify pending vs Bitcoin-confirmed — fail-soft: a corrupt proof or a
    # missing opentimestamps lib must never crash this read-only surface.
    proof_state = ""
    bitcoin_height: int | None = None
    if proof_available:
        try:
            from app.integrity.upgrade import read_proof_info

            info = read_proof_info(proof_path)
            proof_state = info.state
            bitcoin_height = info.bitcoin_height
        except Exception:  # noqa: BLE001 — lib absent / any error → don't crash the read
            proof_state = "unknown"
    return IntegrityStatus(
        state="ok",
        enabled=True,
        stamper=cfg.stamper,
        proofs_dir=cfg.proofs_dir,
        anchor_count=len(parsed),
        last_digest=digest,
        last_anchored_at=str(latest.get("ts", "")),
        proof_available=proof_available,
        proof_state=proof_state,
        bitcoin_height=bitcoin_height,
    )
