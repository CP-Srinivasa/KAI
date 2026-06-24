"""Read side of the Lightning value-layer operations ledger (audit trail).

The append-only ``artifacts/ln_ops_ledger.jsonl`` records every value-layer action
(plan + execution) for a tamper-evident, L3-OTS-anchorable audit trail. The WRITER
arrives with the gated value layer (Sprint 4/5); this read side exists now so the
dashboard can show an honest "no operations yet" until then. Read-only, no capital
path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.lightning.jsonl_tail import read_recent_jsonl

_OPS_PATH = Path("artifacts/ln_ops_ledger.jsonl")


def read_recent_ln_ops(path: Path | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    """Read the most recent value-layer ops (newest last); ``[]`` until the gated
    writer produces any. Tolerant: missing file / blank / corrupt lines skipped."""
    return read_recent_jsonl(path or _OPS_PATH, limit=limit)
