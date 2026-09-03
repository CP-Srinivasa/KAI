"""Kennzahlen aus dem Geld-Journal (ADR 0017 §10).

Getrennt von :mod:`app.payments.health`, weil es zwei verschiedene Fragen sind:
*"wie steht es um den Geldpfad?"* braucht Rail, Settings und Umgebung — *"was
sagen diese Records?"* braucht nur die Records. Die Trennung macht die
Rechnungen ohne Node und ohne Konfiguration pruefbar.

**Was hier NICHT passiert: raten.** Ohne ein einziges Settlement ist die
Latenz ``None``, nicht ``0.0``. Eine Null waere eine Messung, und eine
erfundene Messung im Geldpfad ist die teuerste Sorte Zahl.

**Die Latenz misst ``submitted`` -> ``settled``**, also den Weg vom
Write-ahead bis zum Beleg. Das ist bewusst mehr als die reine Node-Zeit: der
Operator will wissen, wie lange eine Zahlung UNKLAR war, nicht wie schnell lnd
antwortet.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.payments.enums import PaymentStatus
from app.payments.models import PaymentAuditEvent

#: Zustaende, in denen Geld unterwegs sein KANN (ADR §4).
_IN_FLIGHT = frozenset({PaymentStatus.SUBMITTED.value, PaymentStatus.IN_FLIGHT.value})


@dataclass(frozen=True)
class JournalMetrics:
    """Alles, was sich aus den Records allein sagen laesst."""

    in_flight: int = 0
    reconciliation_required: int = 0
    policy_rejects: int = 0
    fees: int = 0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    last_settlement: dict[str, Any] | None = None
    last_failure: dict[str, Any] | None = None
    latencies_ms: tuple[float, ...] = field(default=())


def percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank — dieselbe Definition wie ``observability/llm_telemetry``.

    Eine zweite Definition im selben Haus waere ein Vergleich, der nur so
    aussieht wie einer.
    """
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, math.ceil(pct * len(sorted_values)) - 1))
    return sorted_values[index]


def collect(events: Iterable[PaymentAuditEvent], *, cutoff: datetime) -> JournalMetrics:
    """Rechne die Kennzahlen. ``cutoff`` begrenzt Fenster-Groessen, nicht Zustaende.

    Der Unterschied ist wichtig: ein Intent, der vor drei Tagen gesendet wurde
    und immer noch offen ist, gehoert in ``in_flight`` — sonst verschwaende
    genau der Fall aus der Sicht, der am laengsten Geld bindet. Gezaehlt nach
    Fenster werden nur Groessen, die ein Fenster brauchen: Ablehnungen,
    Gebuehren, Latenzen.
    """
    status: dict[str, str] = {}
    submitted_at: dict[str, datetime] = {}
    latencies: list[float] = []
    rejects = 0
    fees = 0
    last_settlement: dict[str, Any] | None = None
    last_failure: dict[str, Any] | None = None

    for event in events:
        payload = event.payload
        state = payload.get("status")
        if isinstance(state, str) and state:
            status[event.intent_id] = state

        if event.event_type == "submitted":
            submitted_at[event.intent_id] = event.ts
        elif event.event_type == "settled":
            start = submitted_at.get(event.intent_id)
            if start is not None and event.ts >= cutoff:
                latencies.append((event.ts - start).total_seconds() * 1000.0)
            if event.ts >= cutoff:
                fees += _int(payload.get("fee_actual_minor_units"))
            last_settlement = {
                "ts": event.ts.isoformat(),
                "amount_minor_units": _int(payload.get("amount_settled_minor_units")),
            }
        elif event.event_type == "failed":
            last_failure = {
                "ts": event.ts.isoformat(),
                "failure_class": str(payload.get("failure_reason") or "unknown"),
            }
        elif event.event_type == "policy_decided" and event.ts >= cutoff:
            if str(payload.get("verdict", "")).upper() == "DENY":
                rejects += 1

    ordered = sorted(latencies)
    return JournalMetrics(
        in_flight=sum(1 for state in status.values() if state in _IN_FLIGHT),
        reconciliation_required=sum(
            1 for state in status.values() if state == PaymentStatus.RECONCILIATION_REQUIRED.value
        ),
        policy_rejects=rejects,
        fees=fees,
        latency_p50_ms=percentile(ordered, 0.50),
        latency_p95_ms=percentile(ordered, 0.95),
        last_settlement=last_settlement,
        last_failure=last_failure,
        latencies_ms=tuple(ordered),
    )


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = ["JournalMetrics", "collect", "percentile"]
