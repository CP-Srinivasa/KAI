"""Der Strom der Produktschicht — Verknuepfung, nicht Wahrheit (KAI PAY v0.1).

``artifacts/pay/requests.jsonl`` haelt fest, WER was angefordert hat und unter
welchem ``ref_hash`` die Forderung im Kern gefuehrt wird. Was er ausdruecklich
NICHT haelt: ob bezahlt wurde, wie viel und wann. Diese drei Antworten kommen
aus dem Rail-Lookup und dem hash-verketteten Geld-Journal (ADR 0018 §5) — hier
stehen nur ein Statuswort fuer die Anzeige und ein Zeiger auf den Record, der
die Geldwahrheit traegt (``journal_seq`` + ``record_hash``).

**Warum diese Trennung streng ist.** Ein zweiter Ort, an dem ein Betrag steht,
ist ein zweiter Ort, an dem ein Betrag FALSCH stehen kann; und weil dieser hier
leichter zu schreiben ist als das Journal, wuerde er im Zweifel gewinnen. Das
ist genau das Muster, das die Doppel-Journal-Phase gekostet hat (ADR §12).

**Warum ohne Lock und ohne Kette**, anders als das Geld-Journal: dieser Strom
wird von genau einem Prozess geschrieben (``kai-server``), eine Zeile bleibt
weit unter ``PIPE_BUF`` (``O_APPEND`` ist auf POSIX atomar), und ein verlorener
Eintrag kostet eine Anzeige, nie einen Satoshi — der Eingang bleibt aus dem
Journal und dem Node rekonstruierbar. Dieselbe Abwaegung wie in
``app/lightning/receive_ledger.py``, aus demselben Grund.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.pay.status import PayStatus

logger = logging.getLogger(__name__)

#: Dateiname des Stroms — als Konstante, damit Leser sie importieren statt das
#: Literal zu wiederholen (Stream-Ratchet G4, ``scripts/stream_consumer_ratchet.py``).
PAY_REQUESTS_FILENAME = "requests.jsonl"

#: Die sechs Ereignisse dieses Stroms. Erschoepfend: ein siebtes gehoert
#: hierher, nicht in einen ``str``-Aufruf an der Schreibstelle.
EVENT_CREATED = "request_created"
EVENT_SETTLED = "settled_observed"
EVENT_EXPIRED = "expired_observed"
EVENT_FAILED = "failed_observed"
EVENT_WEBHOOK_SENT = "webhook_sent"
EVENT_WEBHOOK_FAILED = "webhook_failed"

#: Welches Ereignis welchen Zustand herstellt. Die Umkehrung steht bewusst
#: nicht als ``if``-Kette an der Leseseite — eine Tabelle laesst sich pruefen.
_STATUS_EVENT: dict[PayStatus, str] = {
    PayStatus.SETTLED: EVENT_SETTLED,
    PayStatus.EXPIRED: EVENT_EXPIRED,
    PayStatus.FAILED: EVENT_FAILED,
}
_EVENT_STATUS = {event: status for status, event in _STATUS_EVENT.items()}

MAX_DESCRIPTION_LENGTH = 140
MAX_REFERENCE_LENGTH = 64


@dataclass(frozen=True)
class PayRequest:
    """Eine angeforderte Zahlung, wie die Produktschicht sie fuehrt."""

    payment_id: str
    ref_hash: str
    amount_sat: int
    description: str
    reference: str
    webhook_url: str
    created_at: datetime
    expires_at: datetime
    #: Die Zahlungsaufforderung des Rails. Sie steht hier und NICHT im
    #: Geld-Journal (dort traegt die Allowlist nur den ``ref_hash``): sie ist
    #: kein Geheimnis, sondern das Einzige, womit der Zahler ueberhaupt zahlen
    #: kann — der QR-Code. Ohne sie muesste eine neu geladene Seite eine ZWEITE
    #: Forderung ausstellen, und der Kunde haette zwei gueltige QR-Codes.
    bolt11: str = ""
    #: SHA-256 des ``Idempotency-Key``, nie der Schluessel selbst. Er macht die
    #: Antwort auf einen wiederholten POST ueber einen Neustart hinweg gleich.
    idempotency_key_hash: str = ""
    status: PayStatus = PayStatus.WAITING
    #: Zeiger auf den ``receivable_settled``-Record im Geld-Journal. Kein
    #: Betrag, keine Zeit — nur die Stelle, an der beides beweisbar steht.
    journal_seq: int = 0
    record_hash: str = ""
    #: Letzter Rail-Fehler, der eine Antwort verhindert hat. Reine Diagnose:
    #: er aendert den Zustand nie (fail-soft, siehe ``app/pay/status.py``).
    last_error: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "payment_id": self.payment_id,
            "ref_hash": self.ref_hash,
            "amount_sat": self.amount_sat,
            "description": self.description,
            "reference": self.reference,
            "webhook_url": self.webhook_url,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "bolt11": self.bolt11,
            "idempotency_key_hash": self.idempotency_key_hash,
            "status": self.status.value,
        }


class PayStore:
    """Append-only-Strom mit einem beim Start gebauten Index.

    Der Index ist ABGELEITET, nie Wahrheit: bei Widerspruch gewinnt die Datei,
    und ein Neustart stellt ihn wieder her. Dieselbe Zusage wie beim
    ``JournalIndex`` — nur ohne dessen Kette, weil hier kein Geld haengt.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._by_id: dict[str, PayRequest] = {}
        self._order: list[str] = []
        self._by_key: dict[str, str] = {}

    @property
    def path(self) -> Path:
        return self._path

    # -- Start -------------------------------------------------------------- #

    def load(self) -> int:
        """Baue den Index aus der Datei. Returns: gelesene Ereignisse.

        Eine unlesbare oder halb geschriebene Zeile wird UEBERSPRUNGEN und
        geloggt — nicht wie im Geld-Journal verweigert. Der Unterschied ist die
        Folge: dort forkt ein Weiterschreiben eine Beweiskette, hier fehlt eine
        Anzeige, deren Inhalt im Journal weiterhin belegt ist.
        """
        self._by_id = {}
        self._order = []
        self._by_key = {}
        if not self._path.is_file():
            return 0
        seen = 0
        lines = self._path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                self._ingest(json.loads(line))
            except (ValueError, KeyError, TypeError) as exc:
                logger.warning(
                    "[kai-pay] skipping malformed store line %d in %s: %s",
                    number,
                    self._path,
                    type(exc).__name__,
                )
                continue
            seen += 1
        return seen

    def _ingest(self, record: dict[str, Any]) -> None:
        event = str(record.get("event", ""))
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise TypeError("payload is not an object")
        payment_id = str(payload.get("payment_id", ""))
        if not payment_id:
            raise KeyError("payment_id")
        if event == EVENT_CREATED:
            request = _request_from_payload(payload)
            self._by_id[payment_id] = request
            self._order.append(payment_id)
            if request.idempotency_key_hash:
                self._by_key.setdefault(request.idempotency_key_hash, payment_id)
            return
        known = self._by_id.get(payment_id)
        if known is None:
            # Ein Folgeereignis ohne Anlage: die Anlage-Zeile fehlt (Datei von
            # Hand gekuerzt). Nichts zu aktualisieren — und nichts zu erfinden.
            return
        status = _EVENT_STATUS.get(event)
        if status is None:
            return
        self._by_id[payment_id] = replace(
            known,
            status=status,
            journal_seq=int(payload.get("journal_seq") or known.journal_seq),
            record_hash=str(payload.get("record_hash") or known.record_hash),
        )

    # -- Schreiben ---------------------------------------------------------- #

    def create(self, request: PayRequest) -> PayRequest:
        """Lege eine Forderung an und haenge ihre Anlage an den Strom."""
        self._by_id[request.payment_id] = request
        self._order.append(request.payment_id)
        if request.idempotency_key_hash:
            self._by_key.setdefault(request.idempotency_key_hash, request.payment_id)
        self._append(EVENT_CREATED, request.to_payload())
        return request

    def mark(
        self,
        payment_id: str,
        status: PayStatus,
        *,
        journal_seq: int = 0,
        record_hash: str = "",
    ) -> PayRequest:
        """Halte einen terminalen Zustand fest. ``SETTLED`` gewinnt immer.

        Raises:
            KeyError: unbekannte ``payment_id``.
            ValueError: ``status`` ist nicht terminal — ``WAITING`` ist der
                Ausgangszustand und wird nie geschrieben.
        """
        known = self._by_id[payment_id]
        event = _STATUS_EVENT.get(status)
        if event is None:
            raise ValueError(f"{status.value} is not a terminal pay status")
        if known.status == PayStatus.SETTLED:
            return known
        updated = replace(
            known,
            status=status,
            journal_seq=journal_seq or known.journal_seq,
            record_hash=record_hash or known.record_hash,
            last_error="",
        )
        self._by_id[payment_id] = updated
        self._append(
            event,
            {
                "payment_id": payment_id,
                "ref_hash": known.ref_hash,
                "reference": known.reference,
                "journal_seq": updated.journal_seq,
                "record_hash": updated.record_hash,
            },
        )
        return updated

    def note_error(self, payment_id: str, detail: str) -> PayRequest:
        """Merke einen Rail-Fehler — nur im Index, ohne Zeile im Strom.

        Ein unerreichbarer Node erzeugt sonst mit jedem Poller-Tick eine Zeile
        und begraebt die vier Ereignisse, die etwas bedeuten. Der Fehler steht
        in der Antwort (``last_error``) und im Log; dauerhaft festzuhalten ist
        er nicht wert.
        """
        known = self._by_id[payment_id]
        updated = replace(known, last_error=detail[:200])
        self._by_id[payment_id] = updated
        return updated

    def webhook_result(self, payment_id: str, *, ok: bool, detail: str) -> None:
        """Der Ausgang eines Callback-Versuchs — er aendert nie einen Zustand."""
        self._append(
            EVENT_WEBHOOK_SENT if ok else EVENT_WEBHOOK_FAILED,
            {"payment_id": payment_id, "detail": detail[:200]},
        )

    def _append(self, event: str, payload: dict[str, Any]) -> bool:
        """Eine Zeile, ein ``write``, ein ``fsync``. Wirft nie.

        Fail-soft mit LAUTEM Log: ein verlorener Eintrag darf die Antwort an
        den Aufrufer nicht kippen — die Forderung existiert am Node und im
        Geld-Journal, ob diese Datei sie kennt oder nicht.
        """
        line = json.dumps(
            {"ts": datetime.now(UTC).isoformat(), "event": event, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            logger.error(
                "[kai-pay] STORE LINE LOST (%s): %s: %s — the request itself is unaffected; "
                "settlement stays provable from artifacts/payments/payment_journal.jsonl",
                event,
                type(exc).__name__,
                exc,
            )
            return False
        return True

    # -- Lesen -------------------------------------------------------------- #

    def get(self, payment_id: str) -> PayRequest | None:
        return self._by_id.get(payment_id)

    def by_key(self, idempotency_key_hash: str) -> PayRequest | None:
        """Die Forderung zu einem bereits verbrauchten ``Idempotency-Key``."""
        payment_id = self._by_key.get(idempotency_key_hash)
        return self._by_id.get(payment_id) if payment_id else None

    def by_reference(self, reference: str) -> list[PayRequest]:
        """Alle Forderungen zu einer Bestellreferenz — aelteste zuerst."""
        needle = reference.strip()
        if not needle:
            return []
        return [self._by_id[pid] for pid in self._order if self._by_id[pid].reference == needle]

    def open_requests(self, limit: int) -> list[PayRequest]:
        """Die aeltesten offenen Forderungen. Alt zuerst: sie laufen zuerst ab."""
        out = [
            self._by_id[pid] for pid in self._order if self._by_id[pid].status == PayStatus.WAITING
        ]
        return out[:limit]

    def recent(self, limit: int) -> list[PayRequest]:
        """Die neuesten Forderungen zuerst — die Reihenfolge einer Anzeige."""
        return [self._by_id[pid] for pid in reversed(self._order)][:limit]

    def settled_count(self) -> int:
        return sum(1 for r in self._by_id.values() if r.status == PayStatus.SETTLED)

    def newest_settled(self) -> PayRequest | None:
        """Die zuletzt angelegte Forderung, die als beglichen gilt."""
        for pid in reversed(self._order):
            request = self._by_id[pid]
            if request.status == PayStatus.SETTLED:
                return request
        return None

    def total_count(self) -> int:
        return len(self._order)


def _request_from_payload(payload: dict[str, Any]) -> PayRequest:
    return PayRequest(
        payment_id=str(payload["payment_id"]),
        ref_hash=str(payload["ref_hash"]),
        amount_sat=int(payload["amount_sat"]),
        description=str(payload.get("description", "")),
        reference=str(payload.get("reference", "")),
        webhook_url=str(payload.get("webhook_url", "")),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        expires_at=datetime.fromisoformat(str(payload["expires_at"])),
        bolt11=str(payload.get("bolt11", "")),
        idempotency_key_hash=str(payload.get("idempotency_key_hash", "")),
        status=PayStatus(str(payload.get("status", PayStatus.WAITING.value))),
    )


__all__ = [
    "EVENT_CREATED",
    "EVENT_EXPIRED",
    "EVENT_FAILED",
    "EVENT_SETTLED",
    "EVENT_WEBHOOK_FAILED",
    "EVENT_WEBHOOK_SENT",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_REFERENCE_LENGTH",
    "PAY_REQUESTS_FILENAME",
    "PayRequest",
    "PayStore",
]
