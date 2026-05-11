"""Phase-0 Live-Execution-Audit-Stream (Schema ``live-v1``).

Spec: docs/security/kai_light_live_phase0_spec.md §5.

Schema-Idee: jede Live-Order-Spur enthält **alle 4 Approval-Schichten**
(HOTP, Cap-Check, Risk-Engine, Exchange-Perms) plus Server-SL-Beweis. Ein
Audit-Replay zeigt nach 3 Monaten eindeutig: "war diese Order legitim".

Public API:
    AuditEvent — dataclass mit allen Spec-§5-Feldern (frozen)
    write_event(path, event) -> None — append-only, fail-closed

Pattern: pure functions, kein State. ``LiveExecutionEngine`` ruft
``write_event`` an drei Stellen:
- vor jedem Order-Attempt → ``AuditEventType.ATTEMPTED``
- nach Erfolg → ``AuditEventType.PLACED``
- nach Reject (jedem Gate-Fail) → ``AuditEventType.REJECTED``

JSON-Schema-Version ``live-v1`` ist fixed in dieser Datei — jede Änderung
braucht einen neuen Versions-String (live-v2, ...) damit Replay-Tools
historische Spuren mit dem richtigen Parser lesen können.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LIVE_AUDIT_SCHEMA_VERSION = "live-v1"


class AuditEventType(StrEnum):
    """Drei Event-Types im live-v1 Stream."""

    ATTEMPTED = "live_order_attempted"
    PLACED = "live_order_placed"
    REJECTED = "live_order_rejected"


@dataclass(frozen=True)
class GateCheckRecord:
    """Snapshot eines Gate-Verlaufs für den Audit.

    Nur diese 5 Gates werden je dokumentiert (analog `live_engine.GateResult`):
    hotp / live_caps / risk / exchange_perms / server_sl.
    """

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class AuditEvent:
    """live-v1 Audit-Event. Alle Felder sind Pflicht — fail-closed-Design.

    Felder, die nur bei bestimmten ``event_type`` Sinn machen, dürfen leer/
    None sein, aber das Feld muss IMMER serialisiert werden (Replay-Tools
    verlangen stabile Schema-Shape). Beispiele:
    - ``order_id`` ist nur bei PLACED gesetzt
    - ``sl_order_id`` / ``sl_price`` analog
    - ``reject_reason`` nur bei REJECTED
    """

    # Identity
    schema_version: str = LIVE_AUDIT_SCHEMA_VERSION
    event_type: str = AuditEventType.ATTEMPTED.value
    audit_id: str = ""
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    # Order Context
    exchange: str = ""  # "binance" | "bybit"
    symbol: str = ""
    side: str = ""  # "buy" | "sell"
    quantity: float = 0.0
    entry_price: float = 0.0
    notional_usd: float = 0.0
    stop_loss: float = 0.0
    client_order_id: str = ""

    # Approval-Schichten (PLACED hat alle = True; REJECTED zeigt wo bricht)
    hotp_counter_used: int | None = None
    live_caps_check: str = ""  # "passed" | "not_evaluated" | "<breach-reason>"
    risk_engine_check: str = ""
    exchange_perms_check: str = ""
    server_sl_check: str = ""

    # Gate-Spur (jeder evaluierte Gate, in Reihenfolge)
    gates: tuple[GateCheckRecord, ...] = field(default_factory=tuple)

    # Bei Erfolg: Exchange-Beweis
    order_id: str = ""
    sl_order_id: str = ""
    sl_price: float = 0.0
    current_open_positions_at_send: int = 0

    # Bei Reject:
    reject_reason: str = ""
    gate_failed: str | None = None
    detail: str = ""

    # State-Context (Live-Mode-Snapshot)
    live_state: str = ""  # "locked" | "unlocked"
    idle_lock_remaining_s: int = 0


def write_event(path: Path, event: AuditEvent) -> None:
    """Append-only audit-jsonl. Fail-closed: write-error logged, never raises.

    Schema-Garantien:
    - jede Zeile ist eine in sich geschlossene JSON
    - schema_version-Feld erlaubt parser-version-detection
    - timestamp_utc ist ISO-8601 mit ``+00:00``

    Idempotenz: NICHT garantiert — Caller MUSS sicherstellen, dass die
    audit_id eindeutig ist. Replay-Tools können duplikate via audit_id
    erkennen aber nicht filtern (legitimer Use-Case: retry mit gleichem
    audit_id soll BEIDE Versuche sichtbar machen).
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # asdict konvertiert nested dataclasses (GateCheckRecord) zu dicts.
        payload = asdict(event)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
            fh.flush()
    except OSError as exc:
        logger.error(
            "live_audit_write_failed event_type=%s audit_id=%s exc=%s",
            event.event_type, event.audit_id, exc,
        )


def read_events(path: Path) -> list[AuditEvent]:
    """Read-Helper für Tests + Audit-Replay. Skippt korrupte Lines.

    Returns:
        Liste aller AuditEvents in Reihenfolge. Bei JSON-Parse-Fehler oder
        Schema-Mismatch: Line wird mit Warning übersprungen.

    Used by:
        - tests/unit/test_live_audit.py
        - audit_replay-CLI (zukünftig)
    """
    if not path.exists():
        return []
    events: list[AuditEvent] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("live_audit_corrupt_line file=%s line=%d", path, line_no)
            continue
        # GateCheckRecord-Liste wieder einlesen.
        gates_raw = data.pop("gates", [])
        gates = tuple(GateCheckRecord(**g) for g in gates_raw)
        try:
            events.append(AuditEvent(**data, gates=gates))
        except TypeError as exc:
            logger.warning(
                "live_audit_schema_mismatch file=%s line=%d exc=%s",
                path, line_no, exc,
            )
            continue
    return events


def filter_events(
    events: list[AuditEvent],
    *,
    event_type: AuditEventType | None = None,
    exchange: str | None = None,
    symbol: str | None = None,
    success_only: bool = False,
) -> list[AuditEvent]:
    """Helper für Audit-Forensik.

    Replay-Use-Case: "zeige mir alle BTCUSDT-Live-Trades vom letzten Monat
    auf Binance, die erfolgreich placed wurden".
    """
    result = events
    if event_type is not None:
        result = [e for e in result if e.event_type == event_type.value]
    if exchange is not None:
        result = [e for e in result if e.exchange == exchange]
    if symbol is not None:
        result = [e for e in result if e.symbol == symbol]
    if success_only:
        result = [e for e in result if e.event_type == AuditEventType.PLACED.value]
    return result


def event_to_dict(event: AuditEvent) -> dict[str, Any]:
    """Convenience für telegram-bot reply formatting + dashboard surfaces."""
    return asdict(event)
