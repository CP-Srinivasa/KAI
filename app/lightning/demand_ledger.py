"""U2 — L402 demand-telemetry ledger (capital-free measurement instrument).

Append-only ``artifacts/ln_demand_ledger.jsonl`` records every L402 paywall event:

  * ``l402_challenge_minted`` — an unpaid request hit the paywall (interest signal)
  * ``l402_access_granted``   — a valid token+preimage was presented (= they paid)

This is the instrument the G0 demand probe reads. It is pure logging — no node, no
funds, fail-soft (a telemetry error must NEVER break the request that triggered it).

Privacy: a requester is recorded ONLY as a salted, truncated, non-reversible
fingerprint — NEVER a raw IP.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.file_lock import append_lock
from app.lightning.jsonl_tail import read_recent_jsonl

logger = logging.getLogger(__name__)

_DEMAND_PATH = Path("artifacts/ln_demand_ledger.jsonl")

CHALLENGE_MINTED = "l402_challenge_minted"
ACCESS_GRANTED = "l402_access_granted"
PAID_UNAVAILABLE = "l402_paid_unavailable"


def requester_fingerprint(client_ip: str, *, secret: str) -> str:
    """Salted, truncated, NON-reversible fingerprint of a requester.

    Privacy by construction: never store a raw IP. The salt is derived from the L402
    secret so fingerprints are stable WITHIN a deployment (so the ≥2-distinct-FP
    demand guard works) but not linkable across deployments. Empty IP → ``""``.
    """
    if not client_ip:
        return ""
    salt = (secret or "kai-l402").encode("utf-8")
    return hashlib.sha256(salt + b"|" + client_ip.encode("utf-8")).hexdigest()[:16]


def append_demand_event(
    event: str,
    *,
    scope: str,
    requester_fp: str = "",
    price_sat: int = 0,
    payment_hash: str = "",
    terms_version: str = "",
    invite_id: str = "",
    invite_party: str = "",
    path: Path | None = None,
) -> bool:
    """Append one demand event. Fail-soft: a write error is logged and returns False;
    it must never propagate to the request that triggered the telemetry.

    ``terms_version`` (nur bei veröffentlichten Oracle-Bedingungen gesetzt) ordnet dem
    Auftrag die beim Kauf angezeigte Bedingungsversion zu (D-291 E1).

    ``invite_id``/``invite_party`` (D-299 A1): die Einladung, mit der die Rechnung
    ausgestellt wurde. Anders als ``requester_fp`` bleiben sie stehen — sie tragen keinen
    IP-Bezug, und ohne sie ist die Prä-Reg ``oracle_invite_beta_v1`` nicht auswertbar."""
    out = path or _DEMAND_PATH
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        "scope": scope,
        "requester_fp": requester_fp,
        "price_sat": int(price_sat),
        "payment_hash": payment_hash,
    }
    if terms_version:
        record["terms_version"] = terms_version
    if invite_id:
        record["invite_id"] = invite_id
        record["invite_party"] = invite_party
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        # Sperre wie app/oracle_legal/retention.py beim Leeren alter Kennwerte.
        with append_lock(out), out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — telemetry must never crash the caller
        logger.warning("[ln-demand] append failed: %s", exc)
        return False
    return True


def read_recent_demand_events(path: Path | None = None, *, limit: int = 0) -> list[dict[str, Any]]:
    """All (or most recent ``limit``) demand events; ``[]`` if none. Tolerant reader."""
    return read_recent_jsonl(path or _DEMAND_PATH, limit=limit)


__all__ = [
    "ACCESS_GRANTED",
    "CHALLENGE_MINTED",
    "PAID_UNAVAILABLE",
    "append_demand_event",
    "read_recent_demand_events",
    "requester_fingerprint",
]
