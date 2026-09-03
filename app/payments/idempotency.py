"""Idempotenz-Konsum unter dem Journal-Lock (ADR 0017 §5).

**Kein zweites Artefakt.** Der Bestand fuehrt einen eigenen Store
(``lightning/idempotency_store.py``) mit ``threading.Lock`` und einem
Full-Rewrite je Konsum. Zwei Prozesse verlieren dort Keys (Lost Update: beide
laden denselben Stand, beide schreiben ihren eigenen zurueck), und
``_DEFAULT_MAX_KEYS = 1000`` verdraengt alte Keys — eine Verdraengungsgrenze
ohne Zeitgrenze ist ein Replay-Fenster mit Countdown.

Hier ist der Key Teil **desselben Records**, der den Intent begruendet, und der
Konsum passiert unter **demselben** Lock wie der Append. Damit kann der Key
nicht ohne den Intent existieren und der Intent nicht ohne den Key. Es gibt
kein Evict: das Journal ist append-only, und ein Key, den wir vergessen, ist
ein Send, den wir wiederholen koennen.

**Der Key steht als Hash im Journal.** Er ist ein vom Aufrufer gewaehlter Wert
und kann alles enthalten — fuer die Dedup genuegt sein SHA-256 vollstaendig.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.payments.enums import PaymentStatus
from app.payments.journal import PaymentJournal
from app.payments.models import PaymentIntent


@dataclass(frozen=True)
class IdempotencyOutcome:
    """Was der Konsum ergeben hat.

    ``replayed=True`` heisst: dieser Key hat bereits einen Intent, und das ist
    KEIN Fehler, sondern die Antwort. Der Aufrufer gibt den bestehenden Zustand
    zurueck (HTTP 200, ``replayed=true``), er sendet nicht erneut.
    """

    intent_id: str
    replayed: bool
    status: str | None = None


def hash_idempotency_key(idempotency_key: str) -> str:
    """Einweg-Fingerabdruck des Keys — der Rohwert verlaesst den Prozess nie."""
    return hashlib.sha256(idempotency_key.strip().encode("utf-8")).hexdigest()


def hash_destination(destination: str) -> str:
    """Bindung des Ziels an den Intent (ADR §11) — als Hash, nie als Rohwert."""
    return hashlib.sha256(destination.strip().encode("utf-8")).hexdigest()


def _intent_payload(intent: PaymentIntent, key_hash: str) -> dict[str, object]:
    return {
        "idempotency_key_hash": key_hash,
        "correlation_id": intent.correlation_id,
        "actor": intent.actor,
        "purpose": intent.purpose,
        "rail": intent.rail,
        "mode": intent.mode.value,
        "status": intent.status.value,
        "amount_minor_units": intent.amount_requested.minor_units,
        "currency": intent.amount_requested.currency,
        "scale": intent.amount_requested.scale,
        "fee_limit_minor_units": intent.fee_limit.minor_units,
        "destination_hash": hash_destination(intent.destination),
        "expires_at_unix": int(intent.expires_at.timestamp()),
    }


def consume(
    journal: PaymentJournal,
    idempotency_key: str,
    intent: PaymentIntent,
) -> IdempotencyOutcome:
    """Reserviere ``idempotency_key`` fuer ``intent`` — oder gib den Bestehenden zurueck.

    Der gesamte Vorgang laeuft unter dem Journal-Lock: nachlesen, was andere
    Prozesse angehaengt haben, im Index nachsehen, und im selben kritischen
    Abschnitt den ``intent_created``-Record schreiben. Zwischen "der Key ist
    frei" und "der Key ist meiner" darf kein anderer Prozess liegen — sonst
    haetten zwei Sender denselben Key fuer zwei Sends.

    Verschachtelt aufrufbar: laeuft bereits eine ``journal.transaction()``,
    benutzt dieser Aufruf denselben Lock (der Service klammert Idempotenz,
    Policy und Verdikt in EINE Transaktion).
    """
    key_hash = hash_idempotency_key(idempotency_key)
    with journal.transaction() as tx:
        existing = journal.index.intent_for_key(key_hash)
        if existing is not None:
            return IdempotencyOutcome(
                intent_id=existing,
                replayed=True,
                status=journal.index.intent_status(existing),
            )
        tx.append(intent.intent_id, "intent_created", _intent_payload(intent, key_hash))
        return IdempotencyOutcome(
            intent_id=intent.intent_id,
            replayed=False,
            status=PaymentStatus(intent.status).value,
        )


__all__ = [
    "IdempotencyOutcome",
    "consume",
    "hash_destination",
    "hash_idempotency_key",
]
