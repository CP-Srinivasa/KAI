"""In-Prozess-Index ueber das Geld-Journal (ADR 0017 §5).

Warum es diesen Index gibt: ``ops_ledger.spent_today_sat_v2`` liest und
verifiziert bei JEDER Policy-Auswertung die ganze Datei, und
``_payment_hash_conflict`` scannt fuer den Dedup noch einmal alle Records. Die
Rechnung dafuer steht im Repo — ``receive_ledger.py:11-17``: *"2000 mints
≈ 95 s cumulative, growing O(n²)"*. Genau deshalb wurde der Empfangspfad
seinerzeit aus dem Journal genommen.

Der Index ist **abgeleitet**, nie Wahrheit: er wird beim Start aus dem Journal
gebaut und danach nur noch mit den Records gefuettert, die dazugekommen sind.
Bei Widerspruch gewinnt das Journal — ein Neustart stellt den Index her.

Die Tagesgrenze ist **UTC**. Auf einem Pi in CEST waere eine lokale Grenze ein
Tages-Cap, das zweimal im Jahr um eine Stunde springt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.payments.enums import PaymentStatus
from app.payments.status import TERMINAL_STATES


@dataclass(frozen=True)
class DayTotals:
    """Was an einem UTC-Tag gesendet und was davon bestaetigt wurde."""

    amount_sent: int = 0
    amount_settled: int = 0


@dataclass
class JournalIndex:
    """Abgeleiteter Lesezustand: Idempotenz, offene Intents, Tageszaehler."""

    _keys: dict[str, str] = field(default_factory=dict)
    _status: dict[str, str] = field(default_factory=dict)
    _days: dict[str, DayTotals] = field(default_factory=dict)

    # -- Aufbau ------------------------------------------------------------- #

    def ingest(self, record: dict[str, Any]) -> None:
        """Nimm einen bereits verifizierten Record auf."""
        intent_id = str(record.get("intent_id", ""))
        payload = record.get("payload") or {}
        if not isinstance(payload, dict):  # pragma: no cover - Writer garantiert dict
            payload = {}

        key_hash = payload.get("idempotency_key_hash")
        if isinstance(key_hash, str) and key_hash and intent_id:
            self._keys.setdefault(key_hash, intent_id)

        if intent_id:
            status = payload.get("status")
            if isinstance(status, str) and status:
                self._status[intent_id] = status
            else:
                # Ein Intent ohne je gesehenen Status gilt als OFFEN. Fail-closed:
                # ein unbekannter Zustand darf nie als erledigt durchgehen.
                self._status.setdefault(intent_id, PaymentStatus.REQUESTED.value)

        day = self._day_key(record.get("ts"))
        if day is None:
            return
        sent = payload.get("amount_sent_minor_units")
        settled = payload.get("amount_settled_minor_units")
        if not isinstance(sent, int) and not isinstance(settled, int):
            return
        current = self._days.get(day, DayTotals())
        self._days[day] = DayTotals(
            amount_sent=current.amount_sent + (sent if isinstance(sent, int) else 0),
            amount_settled=current.amount_settled + (settled if isinstance(settled, int) else 0),
        )

    @staticmethod
    def _day_key(raw: Any) -> str | None:
        if isinstance(raw, datetime):
            return raw.astimezone(UTC).date().isoformat()
        if not isinstance(raw, str) or not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:  # pragma: no cover - Writer schreibt ISO
            return None
        if parsed.tzinfo is None:  # pragma: no cover - Writer schreibt aware
            return None
        return parsed.astimezone(UTC).date().isoformat()

    # -- Abfragen ----------------------------------------------------------- #

    def intent_for_key(self, idempotency_key_hash: str) -> str | None:
        return self._keys.get(idempotency_key_hash)

    def intent_status(self, intent_id: str) -> str | None:
        return self._status.get(intent_id)

    def open_intents(self) -> set[str]:
        """Intents ohne terminalen Zustand — sie reservieren Cap (ADR §4)."""
        terminal = {state.value for state in TERMINAL_STATES}
        return {intent for intent, status in self._status.items() if status not in terminal}

    def totals_for_day(self, moment: datetime) -> DayTotals:
        day = self._day_key(moment)
        return self._days.get(day or "", DayTotals())

    def snapshot(self) -> dict[str, Any]:
        """Deterministische Fassung — Grundlage fuer ``rebuild == live``."""
        return {
            "keys": dict(sorted(self._keys.items())),
            "status": dict(sorted(self._status.items())),
            "days": {
                day: {"amount_sent": t.amount_sent, "amount_settled": t.amount_settled}
                for day, t in sorted(self._days.items())
            },
        }


__all__ = ["DayTotals", "JournalIndex"]
