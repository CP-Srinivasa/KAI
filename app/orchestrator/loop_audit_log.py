"""Schreibpfad des Loop-Audits (``trading_loop_audit.jsonl``) — Satzbau und Anhängen.

Aus ``TradingLoop._write_audit`` herausgelöst (God-File-Ratchet,
``docs/runbooks/repo_hygiene_policy.md`` §5). Rein mechanisch: derselbe Satz,
dasselbe ``append_lock``, dasselbe fail-soft-Verhalten — ein Audit-Problem darf
den Loop nie anhalten.

Der Strom ist eine HARD EXCLUSION der Rotation (`hold_metrics`,
`build_recent_cycles_summary` und die canonical-edge-Zählungen aggregieren die
VOLLE Historie), und er wird aus demselben Prozessgefüge wie entry-watch und
liquidation-stream geschrieben — deshalb der Lock.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.file_lock import append_lock

if TYPE_CHECKING:
    from app.orchestrator.models import LoopCycle

logger = logging.getLogger(__name__)


def build_cycle_audit_record(
    cycle: LoopCycle, regime_stamp: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Der Audit-Satz eines Zyklus. Rein — kein IO, damit testbar."""
    record: dict[str, Any] = {
        "cycle_id": cycle.cycle_id,
        "started_at": cycle.started_at,
        "completed_at": cycle.completed_at,
        "symbol": cycle.symbol,
        "status": cycle.status.value,
        "market_data_fetched": cycle.market_data_fetched,
        "signal_generated": cycle.signal_generated,
        "risk_approved": cycle.risk_approved,
        "order_created": cycle.order_created,
        "fill_simulated": cycle.fill_simulated,
        "decision_id": cycle.decision_id,
        "risk_check_id": cycle.risk_check_id,
        "order_id": cycle.order_id,
        "notes": list(cycle.notes),
    }
    record.update(regime_stamp or {})
    return record


def append_cycle_audit(
    path: Path, cycle: LoopCycle, regime_stamp: dict[str, Any] | None = None
) -> None:
    """Einen Zyklus anhängen. Fängt jede Ausnahme — der Loop läuft weiter."""
    try:
        record = build_cycle_audit_record(cycle, regime_stamp)
        with append_lock(path), path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
            handle.flush()
    except Exception as exc:  # noqa: BLE001 — Audit darf den Loop nie anhalten
        logger.error("[LOOP] Audit write failed: %s", exc)
