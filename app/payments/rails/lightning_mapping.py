"""Uebersetzung zwischen lnd-Antworten und dem Domaenenmodell (ADR 0018 §7).

Getrennt von :mod:`app.payments.rails.lightning`, weil es zwei verschiedene
Fragen sind: *"was bedeutet diese Antwort?"* braucht keinen Node, keine
Credentials und keinen Modus — *"darf ich den Node ueberhaupt rufen?"* schon.
Die Trennung macht die riskanten Abbildungen ohne Netz pruefbar.

Die riskanteste steht gleich oben: nur ``SUCCEEDED`` und ``FAILED`` sind
Aussagen. Alles andere — auch ein HTTP 200 ohne Preimage — ist ``UNKNOWN``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from app.payments.enums import ProofKind, RailOutcome
from app.payments.models import Money, PaymentAttempt, Proof
from app.payments.rail import (
    DecodedDestination,
    RailError,
    RailLookup,
    RailPayment,
    RailResult,
)

#: lnd-Status, die eine EINDEUTIGE Aussage sind. Alles andere ist UNKNOWN.
_TERMINAL_LND_STATUS = {"SUCCEEDED": RailOutcome.SETTLED, "FAILED": RailOutcome.FAILED}
_IN_FLIGHT_LND_STATUS = {"IN_FLIGHT", "INITIATED"}

#: Wallet-Zustaende, in denen lnd nicht signieren kann.
_LOCKED_STATES = {"LOCKED", "NON_EXISTING", "WAITING_TO_START", "UNKNOWN", ""}

_SAT_CURRENCY = "SAT"


def _sat(amount: int) -> Money:
    return Money(minor_units=max(0, int(amount)), currency=_SAT_CURRENCY, scale=0)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalise_payment_hash(raw: Any) -> str:
    """Auf lowercase-Hex normalisieren (MI-1).

    lnd spricht an manchen Stellen base64 (``r_hash``), an anderen Hex. Zwei
    Schreibweisen desselben Hashes wuerden die Dedup blind machen.
    """
    from app.lightning.ops_ledger import normalize_payment_hash

    return str(normalize_payment_hash(raw)).lower()


def sat(amount: int) -> Money:
    return Money(minor_units=max(0, int(amount)), currency=_SAT_CURRENCY, scale=0)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalise_payment_hash(raw: Any) -> str:
    """Auf lowercase-Hex normalisieren (MI-1).

    lnd spricht an manchen Stellen base64 (``r_hash``), an anderen Hex. Zwei
    Schreibweisen desselben Hashes wuerden die Dedup blind machen.
    """
    from app.lightning.ops_ledger import normalize_payment_hash

    return str(normalize_payment_hash(raw)).lower()


def wallet_is_locked(state: str) -> bool:
    return state.strip().upper() in _LOCKED_STATES


def destination_from_payreq(decoded: dict[str, Any], *, rail: str) -> DecodedDestination:
    """Aus einer ``decodepayreq``-Antwort die Bindung bauen.

    Ohne Payee UND ohne Dedup-Schluessel ist der Decode wertlos: die Allowlist
    haette nichts zu pruefen und ein Retry nichts, woran er sich bindet. Das
    ist deshalb ein Fehler, kein teilweise gefuelltes Ergebnis.
    """
    destination_pubkey = str(decoded.get("destination", "")).strip()
    payment_hash = normalise_payment_hash(decoded.get("payment_hash"))
    if not destination_pubkey or not payment_hash:
        raise RailError("decoded payment request carries no destination or payment_hash")
    amount_sat = int(decoded.get("num_satoshis") or 0)
    expiry = int(decoded.get("expiry") or 0)
    timestamp = int(decoded.get("timestamp") or 0)
    expires_at = (
        datetime.fromtimestamp(timestamp + expiry, tz=UTC) if timestamp > 0 and expiry > 0 else None
    )
    description = str(decoded.get("description", ""))
    return DecodedDestination(
        rail=rail,
        kind="ln_invoice",
        payee_hash=sha(destination_pubkey),
        rail_dedup_key=payment_hash,
        amount=sat(amount_sat) if amount_sat > 0 else None,
        expires_at=expires_at,
        memo_hash=sha(description) if description else "",
    )


def result_from_send(
    response: dict[str, Any], *, rail: str, attempt: PaymentAttempt, moment: datetime
) -> RailResult:
    """lnd antwortet mit HTTP 200 auch dann, wenn die Zahlung scheiterte.

    ``payment_error`` ist ein Freitext, der ein Ziel zurueckspiegeln kann — er
    wird deshalb nie uebernommen, sondern nur als Anwesenheit gewertet.
    """
    error = str(response.get("payment_error", "")).strip()
    if error:
        return RailResult(
            rail=rail,
            outcome=RailOutcome.FAILED,
            rail_dedup_key=attempt.rail_dedup_key,
            observed_at=moment,
            failure_reason="PAYMENT_ERROR",
            raw_status="FAILED",
        )
    preimage = str(response.get("payment_preimage", "")).strip()
    route = response.get("payment_route") or {}
    fee_sat = int(route.get("total_fees") or route.get("total_fees_msat", 0) or 0)
    if not preimage:
        # 200 ohne Fehler UND ohne Preimage: kein Beweis, keine Aussage.
        return RailResult(
            rail=rail,
            outcome=RailOutcome.UNKNOWN,
            rail_dedup_key=attempt.rail_dedup_key,
            observed_at=moment,
            raw_status="NO_PREIMAGE",
        )
    return RailResult(
        rail=rail,
        outcome=RailOutcome.SETTLED,
        rail_dedup_key=attempt.rail_dedup_key,
        observed_at=moment,
        amount_sent=attempt.amount_sent,
        fee_actual=sat(fee_sat),
        proof=Proof(kind=ProofKind.PREIMAGE, ref_hash=normalise_payment_hash(preimage)),
        raw_status="SUCCEEDED",
    )


def payments_from_rows(
    rows: tuple[Any, ...], *, rail: str, moment: datetime
) -> tuple[RailPayment, ...]:
    """``ListPayments``-Zeilen als Rueckwaerts-Sicht (ADR §8).

    Kein Zeitstempel: die Zeile traegt keinen, den der Client durchreicht. Der
    Beobachtungszeitpunkt ist deshalb der des LAUFS, nicht der der Zahlung —
    und genau deshalb sagt :class:`RailPaymentList` ``window_enforced=False``.
    """
    return tuple(
        RailPayment(
            rail=rail,
            rail_dedup_key=row.payment_hash,
            outcome=RailOutcome.SETTLED,
            observed_at=moment,
            amount_sent=sat(row.value_sat),
            fee_actual=sat(row.fee_sat),
        )
        for row in rows
    )


def lookup_from_payment(payment: Any, *, rail: str, moment: datetime) -> RailLookup:
    """Ein ``ListPayments``-Eintrag wird zu einer Aussage — oder zu keiner."""
    status = str(payment.status).strip().upper()
    outcome = _TERMINAL_LND_STATUS.get(status)
    if outcome is None:
        outcome = RailOutcome.IN_FLIGHT if status in _IN_FLIGHT_LND_STATUS else RailOutcome.UNKNOWN
    return RailLookup(
        rail=rail,
        found=True,
        outcome=outcome,
        rail_dedup_key=payment.payment_hash,
        observed_at=moment,
        amount_sent=sat(payment.value_sat) if payment.value_sat else None,
        fee_actual=sat(payment.fee_sat),
        proof=(
            Proof(kind=ProofKind.PREIMAGE, ref_hash=payment.payment_hash)
            if outcome is RailOutcome.SETTLED
            else None
        ),
        failure_reason=str(payment.failure_reason or "")[:64],
    )


__all__ = [
    "destination_from_payreq",
    "lookup_from_payment",
    "normalise_payment_hash",
    "payments_from_rows",
    "result_from_send",
    "sat",
    "sha",
    "wallet_is_locked",
]
