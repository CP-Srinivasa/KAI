"""Die einzige Vergabestelle fuer ``PaymentStatus`` (ADR 0017 §4).

Der Bestand hatte drei ueberlappende Zustandsvokabulare (``ops_ledger``
``{intent,in_flight,unknown,executed,error}``, ``value_layer``
``{disabled,planned,executed,error}``, ``reconciliation``
``{left_open,...}``) und **kein** Modul, das einen Uebergang validierte. Hier
gibt es eine Tabelle und eine Funktion; alles andere im Paket bekommt seinen
Status ueber :func:`transition` oder gar nicht.

**Die eigentliche Regel ist nicht die Tabelle, sondern die Beweislast.** Ein
Intent, dessen Send bereits draussen war (``SUBMITTED``, ``IN_FLIGHT``,
``RECONCILIATION_REQUIRED``), darf nur mit Node-Evidenz terminal werden. Ohne
sie bleibt genau ein Weg offen: ``RECONCILIATION_REQUIRED``. Das ist die Lehre
aus dem 25k-Spend vom 07-02 — der Client lief in einen Timeout, die Zeile sagte
``error``, die Kanalbilanzen sagten bezahlt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.payments.enums import PaymentStatus, RailOutcome, Verdict
from app.payments.models import Proof
from app.payments.money import FROZEN, HASH_LENGTH, require_aware

S = PaymentStatus

#: ADR §4. Ein Zustand ohne Eintrag existiert nicht; ein leerer Eintrag ist terminal.
TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    S.REQUESTED: frozenset({S.DENIED, S.AWAITING_APPROVAL, S.AUTHORIZED}),
    S.AWAITING_APPROVAL: frozenset({S.AUTHORIZED, S.CANCELLED, S.EXPIRED}),
    S.AUTHORIZED: frozenset({S.SUBMITTED, S.EXPIRED, S.CANCELLED}),
    S.SUBMITTED: frozenset(
        {S.IN_FLIGHT, S.SETTLED, S.SETTLED_REVERSIBLE, S.FAILED_FINAL, S.RECONCILIATION_REQUIRED}
    ),
    S.IN_FLIGHT: frozenset(
        {
            S.SETTLED,
            S.SETTLED_REVERSIBLE,
            S.FAILED_RETRYABLE,
            S.FAILED_FINAL,
            S.RECONCILIATION_REQUIRED,
        }
    ),
    S.FAILED_RETRYABLE: frozenset({S.AUTHORIZED, S.FAILED_FINAL}),
    S.RECONCILIATION_REQUIRED: frozenset({S.SETTLED, S.FAILED_FINAL, S.RECONCILIATION_REQUIRED}),
    S.SETTLED_REVERSIBLE: frozenset({S.SETTLED, S.REVERSED}),
    S.DENIED: frozenset(),
    S.SETTLED: frozenset(),
    S.REVERSED: frozenset(),
    S.FAILED_FINAL: frozenset(),
    S.EXPIRED: frozenset(),
    S.CANCELLED: frozenset(),
}

TERMINAL_STATES: frozenset[PaymentStatus] = frozenset(
    state for state, targets in TRANSITIONS.items() if not targets
)

#: Zustaende, aus denen heraus bereits Geld am Rail liegt oder liegen KANN.
#: Ab hier ist jede Aussage ueber seinen Verbleib beweispflichtig.
#: ``SETTLED_REVERSIBLE`` steht mit drin, damit auch eine Rueckbuchung einen
#: Beleg braucht — Lightning kennt sie nicht, ein spaeterer Rail schon.
_POST_SEND_STATES: frozenset[PaymentStatus] = frozenset(
    {S.SUBMITTED, S.IN_FLIGHT, S.RECONCILIATION_REQUIRED, S.SETTLED_REVERSIBLE}
)

#: Zielzustaende, die eine Aussage ueber den Verbleib des Geldes TREFFEN.
#: ``RECONCILIATION_REQUIRED`` fehlt hier bewusst: "wir wissen es nicht" ist
#: die einzige Aussage, die keinen Beweis braucht — sonst gaebe es keinen
#: sicheren Hafen und der Code muesste raten.
_EVIDENCE_BOUND_TARGETS: frozenset[PaymentStatus] = frozenset(
    {S.SETTLED, S.SETTLED_REVERSIBLE, S.FAILED_FINAL, S.FAILED_RETRYABLE, S.REVERSED}
)

#: Zaehlt gegen Limits (ADR §4): alles, was Geld bewegt haben KANN.
#: ``FAILED_FINAL`` fehlt — es existiert nur mit Node-Evidenz.
CAP_COUNTING_STATES: frozenset[PaymentStatus] = frozenset(
    {S.SUBMITTED, S.IN_FLIGHT, S.RECONCILIATION_REQUIRED, S.SETTLED, S.SETTLED_REVERSIBLE}
)


class RailEvidence(BaseModel):
    """Was der Node ueber einen Sendeversuch gesagt hat — nicht, was wir hoffen.

    ``source`` unterscheidet die drei Wege, auf denen eine Aussage entstehen
    kann: die Antwort auf den Send selbst, ein spaeterer Lookup, oder der
    Reconciler. Alle drei sind gueltige Beweise; keiner von ihnen ist "der
    Aufruf hat eine Exception geworfen".
    """

    model_config = FROZEN

    source: Literal["rail_response", "rail_lookup", "reconciler"]
    rail: str = Field(min_length=1, max_length=32)
    rail_dedup_key: str = Field(min_length=1, max_length=HASH_LENGTH)
    observed_status: str = Field(min_length=1, max_length=32)
    observed_at: datetime
    proof: Proof | None = None
    failure_reason: str = Field(default="", max_length=64)

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "observed_at")


class TransitionEvidence(BaseModel):
    """Der Grund fuer einen Uebergang, mit oder ohne Node-Beleg."""

    model_config = FROZEN

    actor: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    rail_evidence: RailEvidence | None = None

    @field_validator("occurred_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "occurred_at")


def transition(
    current: PaymentStatus,
    target: PaymentStatus,
    *,
    evidence: TransitionEvidence,
) -> PaymentStatus:
    """Gib ``target`` zurueck, wenn der Uebergang zulaessig UND belegt ist.

    ``evidence`` ist keyword-only und ohne Default: ein Uebergang ohne
    angegebenen Grund waere im Geldpfad ein anonymer Schreibzugriff.

    Raises:
        ValueError: der Uebergang steht nicht in :data:`TRANSITIONS`, oder er
            trifft eine Aussage ueber den Verbleib des Geldes, ohne dass der
            Rail sie gedeckt haette.
    """
    allowed = TRANSITIONS.get(current)
    if allowed is None:  # pragma: no cover - StrEnum deckt die Tabelle vollstaendig
        raise ValueError(f"unknown payment status: {current}")
    if target not in allowed:
        raise ValueError(
            f"illegal transition {current.value} -> {target.value}; "
            f"allowed: {sorted(s.value for s in allowed) or 'none (terminal)'}"
        )
    needs_proof = current in _POST_SEND_STATES and target in _EVIDENCE_BOUND_TARGETS
    if needs_proof and evidence.rail_evidence is None:
        raise ValueError(
            f"transition {current.value} -> {target.value} requires rail evidence: "
            "after a submit, only the rail can say where the money went — without "
            "it the only honest target is RECONCILIATION_REQUIRED"
        )
    return target


def status_for_verdict(verdict: Verdict) -> PaymentStatus:
    """Welcher Zustand aus einem Policy-Verdikt folgt.

    Steht hier und nicht im Service, weil auch das eine Zustandsvergabe ist.
    Alles, was NICHT ausdruecklich ALLOW oder REQUIRES_APPROVAL ist, wird
    ``DENIED`` — ein unbekanntes Verdikt darf nie zu einer Freigabe fuehren.
    """
    if verdict is Verdict.ALLOW:
        return S.AUTHORIZED
    if verdict is Verdict.REQUIRES_APPROVAL:
        return S.AWAITING_APPROVAL
    return S.DENIED


def classify_rail_outcome(
    outcome: object,
    *,
    reversal_supported: bool = False,
) -> PaymentStatus:
    """Bilde eine Rail-Aussage auf einen Zustand ab — fail-closed.

    Alles, was keine EINDEUTIGE Aussage des Rails ist (Timeout, Transportfehler,
    leerer Wert, ein Status, den dieser Code nicht kennt), wird
    ``RECONCILIATION_REQUIRED``. Der teure Fehler ist nicht ein Intent zu viel
    in der Klaerung, sondern ein ``FAILED``, das einen Retry freigibt, obwohl
    das Geld bereits unterwegs war.

    ``outcome`` ist bewusst ``object``: die Funktion ist die Grenze zwischen
    einer fremden Antwort und dem Domaenenmodell und darf an einem unerwarteten
    Typ nicht scheitern, sondern muss ihn konservativ einordnen.
    """
    if isinstance(outcome, RailOutcome):
        known = outcome
    elif isinstance(outcome, str):
        try:
            known = RailOutcome(outcome.strip().upper())
        except ValueError:
            return S.RECONCILIATION_REQUIRED
    else:
        return S.RECONCILIATION_REQUIRED

    if known is RailOutcome.SETTLED:
        return S.SETTLED_REVERSIBLE if reversal_supported else S.SETTLED
    if known is RailOutcome.FAILED:
        return S.FAILED_FINAL
    if known is RailOutcome.IN_FLIGHT:
        return S.IN_FLIGHT
    return S.RECONCILIATION_REQUIRED


__all__ = [
    "CAP_COUNTING_STATES",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "RailEvidence",
    "TransitionEvidence",
    "classify_rail_outcome",
    "status_for_verdict",
    "transition",
]
