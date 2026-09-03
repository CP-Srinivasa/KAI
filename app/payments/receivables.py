"""Die Empfangsseite: eine eigene Forderung und ihr Journal-Record (ADR 0017 §1).

Der Sendepfad und der Empfangspfad sind nicht symmetrisch, und das ist der
Grund fuer dieses Modul. Ein Send hat einen Aufrufer, der auf die Antwort
wartet; eine Forderung wird von AUSSEN beglichen, und niemand ruft dabei an.
Der einzige Beobachter ist der Reconciler — und er braucht einen Record, gegen
den er den Node halten kann.

Ohne diesen Record waere ein Geldeingang eine Zustandsaenderung ohne Spur:
belegbar nur, solange der Node die Invoice noch fuehrt, und keiner Leistung
zuzuordnen. ``order_ref`` ist genau diese Zuordnung — KAIs eigene
Bestellreferenz, die der Rail nie sieht (Self-Use-Test, ADR 0016).
"""

from __future__ import annotations

from datetime import datetime

from app.payments.enums import PaymentStatus
from app.payments.journal import PaymentJournal
from app.payments.models import Invoice


def receivable_intent_id(ref_hash: str) -> str:
    """Der Vorgangsschluessel einer Forderung.

    Aus dem Invoice-Hash abgeleitet und damit deterministisch: zwei Records zu
    derselben Forderung tragen denselben Vorgang, auch wenn sie aus
    verschiedenen Prozessen kommen (der Server stellt aus, der Reconcile-Timer
    bucht ein).
    """
    return f"rcv_{ref_hash[:16]}"


def record_invoice(
    journal: PaymentJournal,
    invoice: Invoice,
    *,
    purpose: str,
    order_ref: str,
    moment: datetime,
) -> None:
    """Haenge die ausgestellte Forderung ans Journal — redigiert wie alles andere."""
    journal.append(
        receivable_intent_id(invoice.ref_hash),
        "intent_created",
        {
            "status": PaymentStatus.REQUESTED.value,
            "invoice_ref_hash": invoice.ref_hash,
            "order_ref": order_ref,
            "purpose": purpose,
            "rail": invoice.rail,
            "amount_minor_units": invoice.amount.minor_units,
            "currency": invoice.amount.currency,
            "payee_hash": invoice.payee_hash,
            "memo_hash": invoice.memo_hash,
            "expires_at_unix": int(invoice.expires_at.timestamp()),
        },
        ts=moment,
    )


__all__ = ["receivable_intent_id", "record_invoice"]
