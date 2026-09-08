"""Die Empfangsseite: eine eigene Forderung und ihr Journal-Record (ADR 0018 §1).

Der Sendepfad und der Empfangspfad sind nicht symmetrisch, und das ist der
Grund fuer dieses Modul. Ein Send hat einen Aufrufer, der auf die Antwort
wartet; eine Forderung wird von AUSSEN beglichen, und niemand ruft dabei an.
Der einzige Beobachter ist der Reconciler — und er braucht einen Record, gegen
den er den Node halten kann.

Ohne diesen Record waere ein Geldeingang eine Zustandsaenderung ohne Spur:
belegbar nur, solange der Node die Invoice noch fuehrt, und keiner Leistung
zuzuordnen. ``order_ref`` ist genau diese Zuordnung — KAIs eigene
Bestellreferenz, die der Rail nie sieht (Self-Use-Test, ADR 0016).

**Warum die Buchung seit KAI PAY v0.1 HIER steht und nicht im Reconciler**
(D-CORE-006). Sie stand als Rumpf einer Schleife in
``reconcile_passes.receivables`` und war damit nur fuer den 15-Minuten-Timer
erreichbar. Eine Produktschicht, die einen Eingang zeitnah bestaetigen soll,
haette daneben eine ZWEITE Buchung gebraucht — zwei Schreiber fuer denselben
Record, zwei Meinungen darueber, wann er faellig ist, und genau das Muster, das
die Doppel-Journal-Phase gekostet hat (ADR §12).

Jetzt gibt es einen Durchgang fuer GENAU EINE Forderung
(:func:`settle_receivable`), und der Timer fuehrt ihn in einer Schleife aus.
Er ist der einzige Schreiber von ``receivable_settled`` im ganzen Repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.payments.enums import PaymentStatus
from app.payments.journal import JournalIntegrityError, PaymentJournal
from app.payments.models import Invoice
from app.payments.rail import PaymentRail, RailError

#: Der Record, der einen Geldeingang belegt. Er ist der einzige Ort, an dem
#: eine bezahlte Forderung nachweisbar steht.
SETTLED_EVENT = "receivable_settled"


def receivable_intent_id(ref_hash: str) -> str:
    """Der Vorgangsschluessel einer Forderung.

    Aus dem Invoice-Hash abgeleitet und damit deterministisch: zwei Records zu
    derselben Forderung tragen denselben Vorgang, auch wenn sie aus
    verschiedenen Prozessen kommen (der Server stellt aus, der Reconcile-Timer
    bucht ein).
    """
    return f"rcv_{ref_hash[:16]}"


@dataclass(frozen=True)
class ReceivableSettlement:
    """Der gebuchte Geldeingang — ausschliesslich aus dem Journal gelesen.

    Betrag und Zeitpunkt stehen hier, WEIL sie aus dem hash-verketteten Record
    stammen. ``journal_seq`` und ``record_hash`` sind die Stelle, an der ein
    Dritter das nachrechnen kann; ohne sie waere dieser Datensatz eine
    Behauptung wie jede andere.
    """

    ref_hash: str
    intent_id: str
    order_ref: str
    amount_settled_minor_units: int
    settled_at: datetime
    journal_seq: int
    record_hash: str
    #: Der Rail, der die Forderung ausgestellt hat — aus dem
    #: ``intent_created``-Record desselben Vorgangs, nicht aus der laufenden
    #: Konfiguration. Ein Beleg soll sagen, WO gezahlt wurde, nicht wo heute
    #: gezahlt wuerde.
    rail: str = ""


@dataclass(frozen=True)
class ReceivableOutcome:
    """Was ein Durchgang ueber eine Forderung herausgefunden hat.

    ``settled=None`` heisst ausdruecklich **keine Aussage** — der Rail hat
    nicht geantwortet. Das ist nicht dasselbe wie ``False``: nur eine echte
    Aussage darf eine Forderung ablaufen lassen.
    """

    ref_hash: str
    settled: bool | None
    settlement: ReceivableSettlement | None = None
    #: Ob DIESER Durchgang den Record geschrieben hat. Ein zweiter Durchgang
    #: ueber dieselbe Forderung findet ihn vor und schreibt nie erneut.
    recorded: bool = False
    error: str = ""


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


async def settle_receivable(
    journal: PaymentJournal,
    rail: PaymentRail,
    ref_hash: str,
    *,
    now: datetime,
) -> ReceivableOutcome:
    """Ein Durchgang des Receivables-Passes fuer GENAU EINE Forderung (ADR §8).

    Die Reihenfolge ist die Zusage gegen eine Doppelbuchung:

    1. **Erst nachlesen, was ein anderer Prozess schon geschrieben hat.** Der
       Reconcile-Timer und der Server schreiben in dieselbe Datei; ein Index,
       der den Tail nicht kennt, hielte eine gebuchte Forderung fuer offen und
       buchte ein zweites Mal.
    2. **Dann fragen, ob sie ueberhaupt noch offen ist.** Ist sie es nicht,
       kommt der vorhandene Record zurueck — ohne Rail-Aufruf, ohne Append.
    3. **Erst dann den Rail fragen** und, nur bei ``settled``, genau einen
       Record anhaengen.

    Ein Rail-Fehler ist ausdruecklich KEIN Befund: er liefert ``settled=None``.
    Wer daraus einen Ablauf ableitet, behauptet ueber Geld etwas, das niemand
    gesagt hat.
    """
    try:
        journal.refresh_tail()
    except JournalIntegrityError as exc:
        # Fail-soft nach aussen, laut im Ergebnis: eine gebrochene Kette meldet
        # der Health-Waechter (``check_payment_journal_chain``), nicht dieser
        # Durchgang. Er darf nur nichts BEHAUPTEN.
        return ReceivableOutcome(ref_hash=ref_hash, settled=None, error=str(exc))

    known = settlement_of(journal, ref_hash)
    if known is not None:
        return ReceivableOutcome(ref_hash=ref_hash, settled=True, settlement=known)

    open_refs = {r.ref_hash: r for r in journal.index.open_receivables()}
    receivable = open_refs.get(ref_hash)
    if receivable is None:
        # Weder offen noch gebucht: diese Forderung kennt das Journal nicht.
        # Kein Rail-Aufruf auf einen Hash, den KAI nie ausgestellt hat.
        return ReceivableOutcome(ref_hash=ref_hash, settled=None, error="unknown receivable")

    try:
        status = await rail.invoice_status(ref_hash)
    except RailError as exc:
        return ReceivableOutcome(ref_hash=ref_hash, settled=None, error=f"rail: {exc}")

    if not status.settled:
        return ReceivableOutcome(ref_hash=ref_hash, settled=False)

    amount = status.amount_paid.minor_units if status.amount_paid else 0
    event = journal.append(
        receivable.intent_id,
        SETTLED_EVENT,
        {
            "status": PaymentStatus.SETTLED.value,
            "invoice_ref_hash": receivable.ref_hash,
            "order_ref": receivable.order_ref,
            "amount_settled_minor_units": amount,
            "evidence_source": "rail_lookup",
        },
        ts=now,
    )
    return ReceivableOutcome(
        ref_hash=ref_hash,
        settled=True,
        recorded=True,
        settlement=ReceivableSettlement(
            ref_hash=receivable.ref_hash,
            intent_id=receivable.intent_id,
            order_ref=receivable.order_ref,
            amount_settled_minor_units=amount,
            settled_at=event.ts,
            journal_seq=event.seq,
            record_hash=event.record_hash,
            rail=status.rail,
        ),
    )


def settlement_of(journal: PaymentJournal, ref_hash: str) -> ReceivableSettlement | None:
    """Der gebuchte Eingang zu einer Forderung — oder ``None``.

    Liest die Records des Vorgangs, nicht den Index: der Index weiss nur DASS
    gebucht wurde, der Record weiss, mit welchem Betrag, wann und unter welchem
    Hash. Genau diese drei Angaben sind die Geldwahrheit.
    """
    intent_id = receivable_intent_id(ref_hash)
    events = journal.events(intent_id)
    rail = next(
        (str(e.payload.get("rail", "")) for e in events if e.event_type == "intent_created"),
        "",
    )
    for event in reversed(events):
        if event.event_type != SETTLED_EVENT:
            continue
        payload = event.payload
        return ReceivableSettlement(
            ref_hash=str(payload.get("invoice_ref_hash", ref_hash)),
            intent_id=intent_id,
            order_ref=str(payload.get("order_ref", "")),
            amount_settled_minor_units=int(payload.get("amount_settled_minor_units", 0) or 0),
            settled_at=event.ts,
            journal_seq=event.seq,
            record_hash=event.record_hash,
            rail=rail,
        )
    return None


def expiry_of(journal: PaymentJournal, ref_hash: str) -> datetime | None:
    """Der Ablauf der Forderung, wie ihn der Kern beim Ausstellen festgehalten hat.

    Nicht der Ablauf, den ein Aufrufer angefragt hat: massgeblich ist, was der
    Rail ausgestellt und das Journal festgehalten hat. Eine Anzeige, die eine
    andere Frist nennt als die Invoice, ist eine falsche Zusage.
    """
    unix = journal.index.expires_at(receivable_intent_id(ref_hash))
    return datetime.fromtimestamp(unix, tz=UTC) if unix is not None else None


__all__ = [
    "SETTLED_EVENT",
    "ReceivableOutcome",
    "ReceivableSettlement",
    "expiry_of",
    "receivable_intent_id",
    "record_invoice",
    "settle_receivable",
    "settlement_of",
]
