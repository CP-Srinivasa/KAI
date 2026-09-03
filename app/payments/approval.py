"""Die Freigabe-Zeremonie (ADR 0017 §4/§11).

Getrennt von :mod:`app.payments.service`, weil sie eine eigene Zusage traegt:
**ohne Verifier gibt es keine Freigabe.** Der naheliegende Fehler waere ein
``if self._hotp is None: pass`` — ein fehlendes Geheimnis wuerde dann zur
Erlaubnis. Hier ist das fehlende Geheimnis ein Fehler.

Der zweite Grund fuer die Trennung: eine abgelehnte Freigabe muss eine SPUR
hinterlassen. Wer den falschen Code eingibt, erzeugt einen
``approval_denied``-Record; ohne ihn waere ein Brute-Force-Versuch am Geldpfad
im Journal unsichtbar. Der Record traegt nur den Ausnahmetyp, nie den Code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.payments.enums import PaymentStatus
from app.payments.journal import PaymentJournal
from app.payments.service_types import PaymentServiceError, Tracked
from app.payments.status import TransitionEvidence, transition


def grant(
    journal: PaymentJournal,
    tracked: Tracked,
    *,
    hotp_verifier: Any,
    approval_code: str,
    moment: datetime,
) -> PaymentStatus:
    """Pruefe den HOTP-Code und setze ``AUTHORIZED`` — oder verweigere laut.

    Raises:
        PaymentServiceError: kein Verifier konfiguriert, oder der Code wurde
            abgelehnt. Beide Faelle sehen fuer den Aufrufer gleich aus; der
            Unterschied steht im Journal.
    """
    intent_id = tracked.intent.intent_id
    if hotp_verifier is None:
        raise PaymentServiceError(
            "no HOTP verifier configured — without a seed nobody can approve, "
            "and nobody gets waved through either"
        )
    try:
        result = hotp_verifier.verify(approval_code)
    except Exception as exc:  # noqa: BLE001 - jede HOTP-Ablehnung ist dieselbe Antwort
        journal.append(
            intent_id,
            "approval_denied",
            {"status": tracked.status.value, "failure_reason": type(exc).__name__},
            ts=moment,
        )
        raise PaymentServiceError(f"approval refused: {type(exc).__name__}") from exc

    tracked.status = transition(
        tracked.status,
        PaymentStatus.AUTHORIZED,
        evidence=TransitionEvidence(actor="operator", reason="hotp approval", occurred_at=moment),
    )
    journal.append(
        intent_id,
        "approval_granted",
        {
            "status": tracked.status.value,
            "approval_counter": int(getattr(result, "counter_used", 0)),
        },
        ts=moment,
    )
    return tracked.status


__all__ = ["grant"]
