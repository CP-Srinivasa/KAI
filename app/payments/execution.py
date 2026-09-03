"""Der Send und was danach kommt (ADR 0017 §4/§8/§9).

Getrennt von :mod:`app.payments.service`, weil hier die Grenze liegt, an der
Geld unwiderruflich wird. Alles davor (Aufnahme, Policy, Freigabe) ist
korrigierbar; ab dem ``submitted``-Record ist es das nicht mehr.

Drei Regeln stehen hier und nirgends sonst:

1. **Write-ahead vor dem Rail-Aufruf.** Ohne diesen Record ist ein Crash
   zwischen Send und Antwort ein Geldverlust ohne Spur.
2. **UNKNOWN traegt keine Evidenz.** Ein Rail-Ergebnis ohne Aussage bekommt
   ausdruecklich KEIN ``rail_evidence`` — damit kann die State Machine daraus
   gar nichts Terminales machen, selbst wenn ein spaeterer Aufrufer es wollte.
3. **Ein Neustart klaert, er entscheidet nicht.** Ein offener ``submitted``
   wird ``RECONCILIATION_REQUIRED``, nie ``FAILED``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.payments.enums import PaymentStatus, RailOutcome
from app.payments.journal import PaymentJournal
from app.payments.models import PaymentAttempt
from app.payments.rail import RailResult
from app.payments.service_types import IntentView, Tracked
from app.payments.status import RailEvidence, TransitionEvidence, classify_rail_outcome, transition

Clock = Callable[[], datetime]

#: Zustaende, die nach einem Neustart geklaert werden muessen.
NEEDS_RECOVERY = frozenset({PaymentStatus.SUBMITTED, PaymentStatus.IN_FLIGHT})


def event_name_for(status: PaymentStatus) -> str:
    """Welches Audit-Ereignis (ADR §9) einen Zustand begleitet."""
    if status in {PaymentStatus.SETTLED, PaymentStatus.SETTLED_REVERSIBLE}:
        return "settled"
    if status in {PaymentStatus.FAILED_FINAL, PaymentStatus.FAILED_RETRYABLE}:
        return "failed"
    if status is PaymentStatus.RECONCILIATION_REQUIRED:
        return "reconciled"
    return "rail_responded"


def rail_evidence_from(result: RailResult) -> RailEvidence | None:
    """Belege aus einer Rail-Antwort — oder ``None``, wenn es keine gibt.

    ``UNKNOWN`` liefert bewusst ``None``: die State Machine verlangt fuer jede
    Aussage ueber den Verbleib des Geldes einen Beleg, und eine Antwort, die
    nichts sagt, ist keiner. Damit ist ein FAILED aus einem Timeout heraus
    nicht "verboten durch Disziplin", sondern strukturell unmoeglich.
    """
    if result.outcome is RailOutcome.UNKNOWN:
        return None
    return RailEvidence(
        source="rail_response",
        rail=result.rail,
        rail_dedup_key=result.rail_dedup_key,
        observed_status=result.raw_status or result.outcome.value,
        observed_at=result.observed_at,
        proof=result.proof,
        failure_reason=result.failure_reason,
    )


def write_ahead(
    journal: PaymentJournal, tracked: Tracked, *, attempt: PaymentAttempt, moment: datetime
) -> None:
    """Setze ``SUBMITTED`` und schreibe den Record — BEVOR der Rail laeuft."""
    tracked.status = transition(
        tracked.status,
        PaymentStatus.SUBMITTED,
        evidence=TransitionEvidence(
            actor="service", reason="write-ahead before rail call", occurred_at=moment
        ),
    )
    journal.append(
        tracked.intent.intent_id,
        "submitted",
        {
            "status": tracked.status.value,
            "attempt_no": attempt.attempt_no,
            "rail_dedup_key": attempt.rail_dedup_key,
            "amount_sent_minor_units": (
                attempt.amount_sent.minor_units if attempt.amount_sent else 0
            ),
        },
        ts=moment,
    )
    tracked.attempts += 1


def apply_rail_result(
    journal: PaymentJournal,
    tracked: Tracked,
    result: RailResult,
    *,
    moment: datetime,
    reversal_supported: bool = False,
) -> IntentView:
    """Rail-Antwort -> Zustand -> Journal. Ein UNKNOWN wird nie zu einem FAILED."""
    journal.append(
        tracked.intent.intent_id,
        "rail_responded",
        {
            "observed_status": result.raw_status or result.outcome.value,
            "rail_dedup_key": result.rail_dedup_key,
            "evidence_source": "rail_response",
        },
        ts=moment,
    )
    target = classify_rail_outcome(result.outcome, reversal_supported=reversal_supported)
    tracked.status = transition(
        tracked.status,
        target,
        evidence=TransitionEvidence(
            actor="rail",
            reason=f"rail outcome {result.outcome.value}",
            occurred_at=moment,
            rail_evidence=rail_evidence_from(result),
        ),
    )
    journal.append(
        tracked.intent.intent_id,
        event_name_for(target),
        {
            "status": target.value,
            "amount_settled_minor_units": (
                result.amount_sent.minor_units if result.amount_sent else 0
            ),
            "fee_actual_minor_units": (result.fee_actual.minor_units if result.fee_actual else 0),
            "proof_hash": result.proof.ref_hash if result.proof else "",
            "failure_reason": result.failure_reason,
        },
        ts=moment,
    )
    return IntentView(intent_id=tracked.intent.intent_id, status=target, decision=tracked.decision)


def recover_open_intents(journal: PaymentJournal, *, clock: Clock) -> list[str]:
    """Klaere offene Sends nach einem Neustart (ADR §4).

    Ein Intent, dessen ``submitted`` ohne Antwort blieb, ist NICHT gescheitert
    — niemand weiss, ob Geld geflossen ist. Er wird
    ``RECONCILIATION_REQUIRED`` und bleibt es, bis der Reconciler Evidenz vom
    Node hat.
    """
    journal.refresh_tail()
    recovered: list[str] = []
    for intent_id in sorted(journal.index.open_intents()):
        raw_status = journal.index.intent_status(intent_id)
        if raw_status is None:
            continue
        try:
            status = PaymentStatus(raw_status)
        except ValueError:  # pragma: no cover - der Writer schreibt nur gueltige
            continue
        if status not in NEEDS_RECOVERY:
            continue
        moment = clock()
        target = transition(
            status,
            PaymentStatus.RECONCILIATION_REQUIRED,
            evidence=TransitionEvidence(
                actor="recover",
                reason="crash_between_submit_and_outcome",
                occurred_at=moment,
            ),
        )
        journal.append(
            intent_id,
            "reconciled",
            {"status": target.value, "failure_reason": "crash_between_submit_and_outcome"},
            ts=moment,
        )
        recovered.append(intent_id)
    return recovered


__all__ = [
    "NEEDS_RECOVERY",
    "apply_rail_result",
    "event_name_for",
    "rail_evidence_from",
    "recover_open_intents",
    "write_ahead",
]
