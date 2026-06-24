"""Sprint 3b — incoming-earnings ledger (souvereign treasury source, UC-7).

Append-only ``artifacts/ln_earnings_ledger.jsonl`` books every settled INBOUND
payment exactly once (idempotent via ``payment_hash``). It is the source the
Self-Funding treasury (Sprint 7) aggregates from. Pure accounting of money that
already arrived — NO capital path, fail-soft.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.lightning.jsonl_tail import read_recent_jsonl

logger = logging.getLogger(__name__)

_EARNINGS_PATH = Path("artifacts/ln_earnings_ledger.jsonl")


def read_recent_ln_earnings(path: Path | None = None, *, limit: int = 500) -> list[dict[str, Any]]:
    """Most recent booked earnings (newest last); ``[]`` if none. Tolerant reader."""
    return read_recent_jsonl(path or _EARNINGS_PATH, limit=limit)


def recorded_payment_hashes(path: Path | None = None) -> set[str]:
    """Set of payment_hashes already booked (for idempotency)."""
    return {
        str(r.get("payment_hash"))
        for r in read_recent_jsonl(path or _EARNINGS_PATH, limit=0)
        if r.get("payment_hash")
    }


def append_ln_earning(
    *,
    payment_hash: str,
    amount_sat: int,
    source: str,
    memo: str = "",
    settled_at: str | None = None,
    path: Path | None = None,
) -> bool:
    """Book one settled inbound payment. Returns False (no-op) if ``payment_hash``
    was already booked — idempotent, so re-scanning settled invoices never
    double-counts. Append-only; fail-soft (a write error is logged, returns False)."""
    out = path or _EARNINGS_PATH
    if payment_hash in recorded_payment_hashes(out):
        return False
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "payment_hash": payment_hash,
        "amount_sat": int(amount_sat),
        "source": source,
        "memo": memo,
        "settled_at": settled_at or "",
    }
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — accounting must never crash the caller
        logger.warning("[ln-earnings] append failed: %s", exc)
        return False
    return True


def _rhash_to_hex(r_hash: Any) -> str:
    """lnd r_hash is base64-encoded bytes; return the hex payment_hash ('' on junk)."""
    if not isinstance(r_hash, str) or not r_hash:
        return ""
    try:
        return base64.b64decode(r_hash).hex()
    except (ValueError, TypeError):
        return ""


def record_settled_invoices(
    invoices: Sequence[dict[str, Any]],
    *,
    source: str = "lightning",
    path: Path | None = None,
) -> int:
    """Book every SETTLED invoice (idempotent) → returns the count newly booked.

    Reads lnd invoice dicts (``r_hash`` base64, ``amt_paid_sat``, ``settled``); a
    not-settled or unparseable invoice is skipped.
    """
    booked = 0
    for inv in invoices:
        if not isinstance(inv, dict) or not inv.get("settled"):
            continue
        ph = _rhash_to_hex(inv.get("r_hash"))
        if not ph:
            continue
        amount = int(inv.get("amt_paid_sat", inv.get("value", 0)) or 0)
        if append_ln_earning(
            payment_hash=ph,
            amount_sat=amount,
            source=source,
            memo=str(inv.get("memo", "")),
            settled_at=str(inv.get("settle_date", "")),
            path=path,
        ):
            booked += 1
    return booked


__all__ = [
    "append_ln_earning",
    "read_recent_ln_earnings",
    "record_settled_invoices",
    "recorded_payment_hashes",
]
