"""Woher der Zustand eines Vorgangs kommt (ADR 0018 §4/§5).

Getrennt von :mod:`app.payments.service`, weil hier eine Rangfolge steht und
dort eine Orchestrierung. Die Rangfolge ist die ganze Aussage dieses Moduls:

**Das Journal fuehrt, der Prozessspeicher folgt.**

Der Reconcile-Timer laeuft als eigener Prozess. Was er ueber Geld feststellt —
``SETTLED``, ``FAILED_FINAL`` — schreibt er ins Journal, nicht in den Speicher
des Servers. Wer den Speicher bevorzugt, antwortet nach einem Reconcile-Lauf
mit einem Zustand, den das Journal laengst widerlegt hat; im LIVE-Fenster
2026-09-04 blieb das bis zum naechsten Neustart so.

Die Gegenrichtung kann nicht eintreten: der Index bekommt jeden Record, den
dieser Prozess selbst schreibt, im selben Aufruf. Das Journal liegt also nie
HINTER dem Speicher, hoechstens vor ihm.
"""

from __future__ import annotations

from app.payments.enums import PaymentStatus
from app.payments.journal import PaymentJournal
from app.payments.service_types import IntentView, PaymentServiceError, Tracked


def journal_status(journal: PaymentJournal, intent_id: str) -> PaymentStatus | None:
    """Was das Journal ueber diesen Vorgang sagt — nach einem Tail-Nachlesen.

    ``None`` heisst "keine Aussage": der Vorgang steht nicht im Journal, oder
    sein letzter Record traegt einen Wert, der kein ``PaymentStatus`` ist. Die
    ``attention``-Records des Reconcilers laufen unter eigenen
    Vorgangsschluesseln, aber ein unerwarteter Wert darf hier nicht werfen — er
    darf nur nichts behaupten.
    """
    journal.refresh_tail()
    raw = journal.index.intent_status(intent_id)
    if raw is None:
        return None
    try:
        return PaymentStatus(raw)
    except ValueError:  # pragma: no cover - der Writer schreibt nur gueltige
        return None


def view_of(journal: PaymentJournal, tracked: Tracked | None, intent_id: str) -> IntentView:
    """Die Antwort auf ``GET /payments/intents/{id}`` — journal-first.

    Raises:
        PaymentServiceError: weder Journal noch Speicher kennen den Vorgang.
    """
    status = journal_status(journal, intent_id)
    if tracked is not None:
        if status is not None:
            tracked.status = status
        return IntentView(intent_id=intent_id, status=tracked.status, decision=tracked.decision)
    if status is None:
        raise PaymentServiceError(f"unknown intent: {intent_id}")
    return IntentView(intent_id=intent_id, status=status)


def synced(journal: PaymentJournal, tracked: Tracked) -> Tracked:
    """Hole den Speicherzustand ans Journal heran, bevor jemand auf ihm handelt.

    Der Abgleich gehoert nicht nur in den Lesepfad: sonst bliebe genau die
    gefaehrliche Haelfte offen — der Operator saehe ``SETTLED``, waehrend
    ``execute`` weiter aus einem Speicher liest, der den Vorgang fuer offen
    haelt.
    """
    status = journal_status(journal, tracked.intent.intent_id)
    if status is not None:
        tracked.status = status
    return tracked


__all__ = ["journal_status", "synced", "view_of"]
