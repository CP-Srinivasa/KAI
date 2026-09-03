"""Die drei Durchgaenge eines Reconcile-Laufs (ADR 0018 §8).

Getrennt von :mod:`app.payments.reconcile`, weil dort die Frage *"darf dieser
Lauf ueberhaupt Ablaeufe vergeben?"* beantwortet wird (Uhr, Zustand, Report)
und hier *"was sagt der Node und was folgt daraus?"*. Die Trennung haelt beide
Module unter der 350-Zeilen-Grenze und macht die riskanteste Abbildung — welche
Node-Aussage einen Retry freigibt — ohne Uhr und ohne Zustandsdatei pruefbar.

Kein Durchgang hier ruft ``rail.pay``. Das ist die Zusage aus ADR §5: ein
sendender Prozess, der Timer haengt nur Outcomes an.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.payment_settings import PaymentSettings
from app.payments.enums import PaymentStatus, RailOutcome
from app.payments.journal import PaymentJournal
from app.payments.journal_index import Receivable
from app.payments.rail import PaymentRail, RailError, RailLookup, RailPaymentList
from app.payments.status import RailEvidence, TransitionEvidence, transition

S = PaymentStatus

#: Zustaende, zu denen der Node etwas zu sagen hat: der Send ist draussen oder
#: koennte es sein. ``FAILED_RETRYABLE`` steht mit drin, weil sein Retry an
#: einer Node-Aussage haengt, die sich geaendert haben kann.
OPEN_AFTER_SEND: frozenset[PaymentStatus] = frozenset(
    {S.SUBMITTED, S.IN_FLIGHT, S.RECONCILIATION_REQUIRED, S.FAILED_RETRYABLE}
)

#: Zustaende VOR dem Send — hier ist ein Ablauf noch folgenlos fuer Geld.
OPEN_BEFORE_SEND: frozenset[PaymentStatus] = frozenset({S.AWAITING_APPROVAL, S.AUTHORIZED})

#: Fehlergruende, bei denen ein Retry vertretbar ist: der Node sagt, die
#: Zahlung ist gescheitert, und der Grund liegt am Weg, nicht am Ziel.
#: Alles andere — auch ein unbekannter Grund — wird ``FAILED_FINAL``.
#: Fail-closed: ein Retry ist ein zweiter Send, und der teure Fehler ist der
#: zweite Send auf einer Annahme.
RETRYABLE_FAILURES: frozenset[str] = frozenset(
    {
        "NO_ROUTE",
        "FAILURE_REASON_NO_ROUTE",
        "TIMEOUT",
        "FAILURE_REASON_TIMEOUT",
        "INSUFFICIENT_BALANCE",
        "FAILURE_REASON_INSUFFICIENT_BALANCE",
    }
)


# --------------------------------------------------------------------------- #


async def forward(
    journal: PaymentJournal, rail: PaymentRail, *, counts: dict[str, int], now: datetime
) -> int:
    """Jeden offenen Send gegen den Node halten (ADR §8)."""
    checked = 0
    for intent_id in sorted(journal.index.open_intents()):
        current = status_of(journal, intent_id)
        if current is None or current not in OPEN_AFTER_SEND:
            continue
        key = journal.index.dedup_key(intent_id)
        if key is None:
            continue
        checked += 1
        try:
            lookup = await rail.lookup(key)
        except RailError:
            continue
        target = _target_for(lookup, current=current)
        if target is None or target is current:
            _bump(counts, "UNCHANGED")
            continue
        _record(journal, intent_id, current, target, lookup=lookup, now=now)
        _bump(counts, target.value)
    return checked


def _target_for(lookup: RailLookup, *, current: PaymentStatus) -> PaymentStatus | None:
    """Node-Aussage -> Zielzustand, oder ``None`` fuer "keine Aussage".

    ``found=False`` ist ausdruecklich KEINE Aussage: die Zahlung kann in einer
    Seite liegen, die der Scan nicht erreicht hat, oder der Node war stumm.
    """
    if not lookup.found:
        return _stay_in_clearing(current)
    if lookup.outcome is RailOutcome.SETTLED:
        return S.SETTLED
    if lookup.outcome is RailOutcome.FAILED:
        reason = lookup.failure_reason.strip().upper()
        retryable = reason in RETRYABLE_FAILURES and S.FAILED_RETRYABLE in _allowed_from(current)
        return S.FAILED_RETRYABLE if retryable else S.FAILED_FINAL
    return _stay_in_clearing(current)


def _stay_in_clearing(current: PaymentStatus) -> PaymentStatus | None:
    """Ohne Aussage bleibt nur die Klaerung — und aus ihr heraus gar nichts."""
    if current is S.RECONCILIATION_REQUIRED:
        return None
    if current is S.FAILED_RETRYABLE:
        return None
    return S.RECONCILIATION_REQUIRED


def _allowed_from(current: PaymentStatus) -> frozenset[PaymentStatus]:
    from app.payments.status import TRANSITIONS

    return TRANSITIONS.get(current, frozenset())


def _record(
    journal: PaymentJournal,
    intent_id: str,
    current: PaymentStatus,
    target: PaymentStatus,
    *,
    lookup: RailLookup,
    now: datetime,
) -> None:
    """Einen belegten Uebergang schreiben. Die Vergabestelle bleibt ``transition``."""
    evidence = RailEvidence(
        source="rail_lookup",
        rail=lookup.rail,
        rail_dedup_key=lookup.rail_dedup_key,
        observed_status=lookup.outcome.value,
        observed_at=lookup.observed_at,
        proof=lookup.proof,
        failure_reason=lookup.failure_reason,
    )
    transition(
        current,
        target,
        evidence=TransitionEvidence(
            actor="reconciler",
            reason=f"rail lookup {lookup.outcome.value}",
            occurred_at=now,
            rail_evidence=evidence,
        ),
    )
    journal.append(
        intent_id,
        _event_for(target),
        {
            "status": target.value,
            "rail_dedup_key": lookup.rail_dedup_key,
            "observed_status": lookup.outcome.value,
            "evidence_source": "rail_lookup",
            "amount_settled_minor_units": (
                lookup.amount_sent.minor_units if lookup.amount_sent else 0
            ),
            "fee_actual_minor_units": (lookup.fee_actual.minor_units if lookup.fee_actual else 0),
            "proof_hash": lookup.proof.ref_hash if lookup.proof else "",
            "failure_reason": lookup.failure_reason,
        },
        ts=now,
    )


def _event_for(target: PaymentStatus) -> str:
    if target in {S.SETTLED, S.SETTLED_REVERSIBLE}:
        return "settled"
    if target in {S.FAILED_FINAL, S.FAILED_RETRYABLE}:
        return "failed"
    if target is S.EXPIRED:
        return "expired"
    return "reconciled"


# --------------------------------------------------------------------------- #
# Ablauf
# --------------------------------------------------------------------------- #


def expire(journal: PaymentJournal, *, counts: dict[str, int], now: datetime) -> None:
    """Intents VOR dem Send verfallen lassen — nur mit vertrauenswuerdiger Uhr."""
    cutoff = now.timestamp()
    for intent_id in sorted(journal.index.open_intents()):
        current = status_of(journal, intent_id)
        if current is None or current not in OPEN_BEFORE_SEND:
            continue
        expires_at = journal.index.expires_at(intent_id)
        if expires_at is None or expires_at > cutoff:
            continue
        transition(
            current,
            S.EXPIRED,
            evidence=TransitionEvidence(
                actor="reconciler", reason="intent expiry elapsed", occurred_at=now
            ),
        )
        journal.append(
            intent_id,
            "expired",
            {"status": S.EXPIRED.value, "expires_at_unix": expires_at},
            ts=now,
        )
        _bump(counts, S.EXPIRED.value)


# --------------------------------------------------------------------------- #
# Rueckwaerts
# --------------------------------------------------------------------------- #


async def backward(
    journal: PaymentJournal,
    rail: PaymentRail,
    *,
    counts: dict[str, int],
    now: datetime,
    settings: PaymentSettings,
) -> tuple[RailPaymentList, tuple[str, ...]]:
    """Was der Rail bewegt hat, ohne dass ein Intent es beauftragt haette."""
    since = now - timedelta(seconds=settings.max_inflight_window_s)
    try:
        listing = await rail.list_payments(since)
    except RailError:
        return RailPaymentList(rail=rail.name, window_enforced=False, complete=False), ()
    orphans = tuple(sorted(_orphan_keys_from(listing, journal)))
    for key in orphans:
        journal.append(
            f"orphan_{key[:24]}",
            "orphan_settlement",
            {"status": "attention", "rail_dedup_key": key, "evidence_source": "rail_lookup"},
            ts=now,
        )
        _bump(counts, "ORPHAN_SETTLEMENT")
    return listing, orphans


def _orphan_keys_from(listing: RailPaymentList, journal: PaymentJournal) -> set[str]:
    """Rail-Schluessel ohne Intent und ohne bereits geschriebenen Befund."""
    known = {
        key
        for intent_id in journal.index.all_intents()
        if (key := journal.index.dedup_key(intent_id)) is not None
    }
    reported = journal.index.orphan_keys()
    return {
        payment.rail_dedup_key
        for payment in listing.payments
        if payment.outcome is RailOutcome.SETTLED
        and payment.rail_dedup_key not in known
        and payment.rail_dedup_key not in reported
    }


# --------------------------------------------------------------------------- #
# Forderungen
# --------------------------------------------------------------------------- #


async def receivables(
    journal: PaymentJournal, rail: PaymentRail, *, counts: dict[str, int], now: datetime
) -> int:
    """Offene Forderungen gegen den Node halten (Self-Use, ADR §1)."""
    open_ones: list[Receivable] = journal.index.open_receivables()
    for receivable in open_ones:
        try:
            status = await rail.invoice_status(receivable.ref_hash)
        except RailError:
            continue
        if not status.settled:
            continue
        journal.append(
            receivable.intent_id,
            "receivable_settled",
            {
                "status": S.SETTLED.value,
                "invoice_ref_hash": receivable.ref_hash,
                "order_ref": receivable.order_ref,
                "amount_settled_minor_units": (
                    status.amount_paid.minor_units if status.amount_paid else 0
                ),
                "evidence_source": "rail_lookup",
            },
            ts=now,
        )
        _bump(counts, "RECEIVABLE_SETTLED")
    return len(open_ones)


# --------------------------------------------------------------------------- #
# Kleinkram
# --------------------------------------------------------------------------- #


def status_of(journal: PaymentJournal, intent_id: str) -> PaymentStatus | None:
    raw = journal.index.intent_status(intent_id)
    if raw is None:
        return None
    try:
        return PaymentStatus(raw)
    except ValueError:  # pragma: no cover - der Writer schreibt nur gueltige
        return None


def unresolved(journal: PaymentJournal) -> int:
    """Intents, ueber deren Geld nach dem Lauf immer noch niemand etwas sagt."""
    return sum(
        1
        for intent_id in journal.index.open_intents()
        if status_of(journal, intent_id) is S.RECONCILIATION_REQUIRED
    )


def _bump(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


__all__ = [
    "OPEN_AFTER_SEND",
    "OPEN_BEFORE_SEND",
    "RETRYABLE_FAILURES",
    "backward",
    "expire",
    "forward",
    "receivables",
    "status_of",
    "unresolved",
]
