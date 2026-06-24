"""Lightning value-layer operations ledger (tamper-evident audit trail).

The append-only ``artifacts/ln_ops_ledger.jsonl`` records every node-touching
value-layer action (plan + outcome) for an L3-OTS-anchorable audit trail. Read side
feeds the dashboard; write side (Sprint 4) is called by the gated value layer on
every executed/error outcome. No capital path of its own.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.lightning.jsonl_tail import read_recent_jsonl

logger = logging.getLogger(__name__)

_OPS_PATH = Path("artifacts/ln_ops_ledger.jsonl")


def read_recent_ln_ops(path: Path | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    """Read the most recent value-layer ops (newest last); ``[]`` until the gated
    writer produces any. Tolerant: missing file / blank / corrupt lines skipped."""
    return read_recent_jsonl(path or _OPS_PATH, limit=limit)


def append_ln_op(
    action: str,
    state: str,
    *,
    plan: dict[str, Any],
    response: dict[str, Any] | None = None,
    path: Path | None = None,
) -> None:
    """Append one value-layer op (plan + outcome) to the audit ledger.

    Fail-soft: a write error is logged and swallowed — the audit trail must NEVER
    kill the (already-gated) send path. Append-only JSONL, one line per op.
    """
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "action": action,
        "state": state,
        "plan": plan,
        "response": response or {},
    }
    out = path or _OPS_PATH
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — audit must never kill the send path
        logger.warning("[ln-ops] append failed: %s", exc)
