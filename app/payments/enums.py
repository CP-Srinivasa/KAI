"""Das Vokabular des Payment Control Plane (ADR 0018 §3/§4/§9).

Getrennt von :mod:`app.payments.models`, weil Vokabular und Struktur
unterschiedlich schnell altern: ein neuer Status ist eine Aenderung an der
State Machine, ein neues Feld eine Aenderung am Record. Beides in einer Datei
haette das Modul ueber die 350-Zeilen-Grenze getragen, ohne dass ein Leser
weiss, welche Haelfte er sucht.

Reine Deklarationen — dieses Modul importiert nichts aus ``app.*``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal


class PaymentMode(StrEnum):
    """ADR §1. Der Modus ist nie implizit — er steht in jedem Intent."""

    SIMULATION = "simulation"
    SHADOW = "shadow"
    LIVE = "live"


class Verdict(StrEnum):
    """Ergebnis der Policy-Kette (ADR §3/§6)."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    RETRY_ALLOWED = "RETRY_ALLOWED"
    RETRY_DENIED = "RETRY_DENIED"


class PaymentStatus(StrEnum):
    """Lebenszyklus einer Wertbewegung (ADR §4).

    Die zentrale Trennung gegenueber dem Bestand: ``FAILED_FINAL`` heisst
    *bewiesen nichts bewegt*, ``RECONCILIATION_REQUIRED`` heisst *unbekannt*.
    ``ops_ledger`` musste beide Bedeutungen in einen ``error``-Zustand pressen
    und dafuer zwei gegenlaeufige Regeln festschreiben.
    """

    REQUESTED = "REQUESTED"
    DENIED = "DENIED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AUTHORIZED = "AUTHORIZED"
    SUBMITTED = "SUBMITTED"
    IN_FLIGHT = "IN_FLIGHT"
    SETTLED = "SETTLED"
    SETTLED_REVERSIBLE = "SETTLED_REVERSIBLE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    REVERSED = "REVERSED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ProofKind(StrEnum):
    """Woran ein Settlement bewiesen wird (ADR §3)."""

    PREIMAGE = "PREIMAGE"
    TXID = "TXID"
    PROVIDER_REF = "PROVIDER_REF"


class SettlementFinality(StrEnum):
    """Wie endgueltig ein Settlement ist (ADR §7 ``RailCapabilities``)."""

    INSTANT = "INSTANT"
    PROBABILISTIC = "PROBABILISTIC"
    DEFERRED = "DEFERRED"
    BUSINESS_DAYS = "BUSINESS_DAYS"


CounterpartyKind = Literal["ln_node", "ln_invoice", "btc_address", "iban", "internal"]

#: ADR §9 nennt die Ereignisse abschliessend. Ein Ereignis ausserhalb dieser
#: Menge waere ein Strom, den kein Leser kennt.
#:
#: Zwei Ereignisse kommen aus dem Reconciler hinzu, den §9 noch nicht kannte:
#: ``receivable_settled`` (die Gegenrichtung des Self-Use-Receivable — ohne sie
#: haette ein Geldeingang keinen Record, nur eine geaenderte Node-Antwort) und
#: ``clock_anomaly`` (ein Uhr-Sprung, der Ablauf-Uebergaenge in diesem Lauf
#: aussetzt — ein unterdrueckter Uebergang ohne Spur waere ein stiller Eingriff).
#:
#: ``dual_journal_conflict`` kommt aus der Uebergangsphase (ADR §12): eine
#: Zahlung, die BEIDE Geldjournale fuehren und die der Altpfad nicht bewiesen
#: abgeschlossen hat. Der Record verschwindet mit dem Rueckbau des Altpfads.
AUDIT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "intent_created",
        "policy_decided",
        "approval_granted",
        "approval_denied",
        "submitted",
        "rail_requested",
        "rail_responded",
        "settled",
        "settlement_reversible",
        "reversed",
        "failed",
        "retry_scheduled",
        "reconciled",
        "orphan_settlement",
        "receivable_settled",
        "dual_journal_conflict",
        "clock_anomaly",
        "expired",
        "cancelled",
        "final",
    }
)


class RailOutcome(StrEnum):
    """Was ein Rail ueber einen Sendeversuch AUSSAGT (ADR §7/§8).

    Vier Werte, keiner davon "Fehler beim Aufruf": ein Timeout oder ein
    Transportfehler ist keine Aussage des Rails, sondern ihr Ausbleiben — er
    faellt auf ``UNKNOWN``. Der Bestand hatte fuer beides denselben
    ``error``-Zustand und musste ihn deshalb widerspruechlich behandeln.
    """

    SETTLED = "SETTLED"
    FAILED = "FAILED"
    IN_FLIGHT = "IN_FLIGHT"
    UNKNOWN = "UNKNOWN"
