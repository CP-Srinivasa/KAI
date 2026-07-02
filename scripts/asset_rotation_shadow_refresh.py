#!/usr/bin/env python3
"""Oneshot: evaluate asset-rotation decisions on paper data → shadow log (G1).

READ-ONLY measurement: persists the rotation FSM state + appends a decision
record; NOTHING acts on the decisions (no feed, no sizing, no capital). Fired by
the ``kai-asset-rotation-shadow`` timer. Fail-safe: any error is logged and the
process exits 0 (the unit's ``-`` ExecStart prefix also prevents propagation).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.learning.asset_rotation_shadow import run_rotation_shadow  # noqa: E402

_AUDIT = Path("artifacts/paper_execution_audit.jsonl")
_STATE = Path("artifacts/asset_rotation_state.json")
_SHADOW = Path("artifacts/asset_rotation_shadow.jsonl")
_LAST_N = 200


def main() -> int:
    try:
        record = run_rotation_shadow(
            audit_path=_AUDIT,
            state_path=_STATE,
            shadow_log_path=_SHADOW,
            last_n=_LAST_N,
            now=datetime.now(UTC),
        )
        print(
            f"asset_rotation_shadow: evaluated {record['evaluated']} symbols, "
            f"{record['changes']} change(s)"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 — measurement-only, never break a timer
        print(f"asset_rotation_shadow failed: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
