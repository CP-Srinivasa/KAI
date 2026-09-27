"""Die Freigabe-Zeremonie (ADR 0018 §4/§11).

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

#: Zustaende VOR dem Send, in denen ein Ablauf noch folgenlos fuer Geld ist.
_EXPIRABLE = frozenset({PaymentStatus.AWAITING_APPROVAL, PaymentStatus.AUTHORIZED})


def refuse_if_expired(
    journal: PaymentJournal, tracked: Tracked, *, moment: datetime, actor: str
) -> None:
    """Eine Freigabe gilt nur bis zum Ablauf des Intents (Audit A1, 27.09.2026).

    Bisher verfiel ein Intent nur im Reconciler-Takt; ``grant`` und ``execute``
    nahmen einen abgelaufenen Vorgang noch an. Jetzt wird er hier — vor dem
    HOTP und vor dem Write-ahead — ``EXPIRED`` (derselbe Record wie beim
    Reconciler) und laut verweigert. Eine springende Uhr kann hoechstens einen
    gueltigen Vorgang ablehnen, nie einen abgelaufenen senden.
    """
    expires_at = tracked.intent.expires_at
    if moment < expires_at:
        return
    if tracked.status in _EXPIRABLE:
        tracked.status = transition(
            tracked.status,
            PaymentStatus.EXPIRED,
            evidence=TransitionEvidence(
                actor=actor, reason="intent expiry elapsed before send", occurred_at=moment
            ),
        )
        journal.append(
            tracked.intent.intent_id,
            "expired",
            {
                "status": PaymentStatus.EXPIRED.value,
                "expires_at_unix": int(expires_at.timestamp()),
            },
            ts=moment,
        )
    raise PaymentServiceError(
        f"refused: intent expired at {expires_at.isoformat()} — create a new one"
    )


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
    refuse_if_expired(journal, tracked, moment=moment, actor="operator")
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


__all__ = ["grant", "refuse_if_expired"]
