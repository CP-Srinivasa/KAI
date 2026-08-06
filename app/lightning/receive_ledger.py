"""Receive-side audit journal — the mint path's OWN trail (W0/PR-C, M-9 + BL-2).

``artifacts/ln_receive_ledger.jsonl`` records every node-touching invoice MINT
(``create_invoice``) with the same redaction boundary as the money journal, and it
is deliberately a SEPARATE file with a deliberately weaker writer than
``ops_ledger`` v2. That asymmetry is the whole point:

**Why separate (and not the chained v2 journal).** The public ``/oracle/*`` mint is
the only real revenue path KAI has, it is unauthenticated, and it mints one invoice
per unpaid request. Journalling it through ``prepare_ln_intent`` would put the
anonymous hot path behind the money journal's EXCLUSIVE inter-process lock and its
O(n) full-file re-parse (measured: 2000 mints ≈ 95 s cumulative, growing O(n²)), and
it would make a torn/forked SPEND journal answer 503 to every anonymous caller
(BL-2: two journal rows + a 503 per request). Receive-side auditing must survive the
mint, not gate it — so:

  * **no chaining, no lock, no read** — one O(1) ``O_APPEND`` write of a single line
    well under ``PIPE_BUF`` (atomic on POSIX), then ``fsync``;
  * **fail-soft with a LOUD log** — a failure is logged at ERROR and returns False;
    it never changes the caller's result and never raises into the request path;
  * **no cap/authorisation semantics** — nothing here gates money. Receive moves
    value INWARD; there is no double-spend to prevent and no budget to reserve.

**What guarantees the truth then.** The mint is not the settlement event. Money that
actually arrived is booked from LND's own invoice database into
``ln_earnings_ledger.jsonl`` (``earnings_booking``) keyed by ``payment_hash`` — that
is the treasury source, and it is reconstructable from the node alone. This file is
the operational trail of what KAI OFFERED (mints, failures), not what it earned; a
lost line here costs a log entry, never a sat. Anchoring/verification of the receive
trail is intentionally out of this PR.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.lightning.ops_ledger import redact_ln_op_record

logger = logging.getLogger(__name__)

_RECEIVE_DEFAULT_PATH = Path("artifacts/ln_receive_ledger.jsonl")
_RECEIVE_PATH_ENV = "APP_LN_RECEIVE_LEDGER_PATH"


def receive_ledger_path() -> Path:
    """Resolve the receive journal path (``APP_LN_RECEIVE_LEDGER_PATH`` overrides)."""
    override = os.environ.get(_RECEIVE_PATH_ENV, "").strip()
    return Path(override) if override else _RECEIVE_DEFAULT_PATH


def append_receive_event(
    action: str,
    state: str,
    *,
    plan: dict[str, Any],
    response: dict[str, Any] | None = None,
    intent_id: str = "",
    authorization: dict[str, Any] | None = None,
    path: Path | None = None,
) -> bool:
    """Append ONE redacted receive event; ``True`` if it landed on disk.

    Never raises. A failure is a LOUD ERROR log (a silently missing audit trail is
    exactly the failure mode a truth platform must not have) and returns ``False`` —
    the mint itself is unaffected, because an invoice that the node has already
    created cannot be un-created by an audit problem.
    """
    out = path or receive_ledger_path()
    record = redact_ln_op_record(
        {
            "ts": datetime.now(UTC).isoformat(),
            "intent_id": intent_id,
            "action": action,
            "state": state,
            "plan": plan,
            "response": response or {},
            "authorization": authorization or {},
        }
    )
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:  # noqa: BLE001 — the audit must never kill the mint
        logger.error(
            "[ln-receive] AUDIT LOST for %s/%s: %s: %s — the mint itself was unaffected; "
            "settled receives remain reconstructable from the node's invoice DB",
            action,
            state,
            type(exc).__name__,
            exc,
        )
        return False
    return True


def read_recent_receive_events(
    path: Path | None = None, *, limit: int = 200
) -> list[dict[str, Any]]:
    """Most recent receive events (newest last); ``[]`` when nothing was minted."""
    from app.lightning.jsonl_tail import read_recent_jsonl

    return read_recent_jsonl(path or receive_ledger_path(), limit=limit)


__all__ = ["append_receive_event", "read_recent_receive_events", "receive_ledger_path"]
