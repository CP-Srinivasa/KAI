"""Sanitised rejection stream for the money-journal input contract (G5).

Rejected plans never enter the hash-chained money journal.  This side stream is
therefore the only durable explanation of why the writer refused an intent.  It
must never contain the plan itself: invoices, pubkeys and addresses are value-
layer material, not diagnostics.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import portalocker

LN_INPUT_REJECTIONS_FILENAME = "ln_input_contract_rejections.jsonl"
_DEFAULT_PATH = Path("artifacts") / LN_INPUT_REJECTIONS_FILENAME


class MoneyInputRejectionAuditError(RuntimeError):
    """The rejected input could not be recorded in its sanitised side stream."""


def append_money_input_rejection(
    *,
    action: str,
    reasons: list[str],
    path: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Durably append rejection reasons without copying any plan value."""
    target = path or _DEFAULT_PATH
    record = {
        "schema": "money-input-rejection/v1",
        "ts": (now or datetime.now(UTC)).isoformat(),
        "contract": "money_journal_input/v1",
        "action": action,
        "reasons": reasons,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(target, mode="a", encoding="utf-8", timeout=10) as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:  # noqa: BLE001 - wrapped into the fail-closed boundary
        raise MoneyInputRejectionAuditError("money input rejection audit write failed") from exc


__all__ = [
    "LN_INPUT_REJECTIONS_FILENAME",
    "MoneyInputRejectionAuditError",
    "append_money_input_rejection",
]
