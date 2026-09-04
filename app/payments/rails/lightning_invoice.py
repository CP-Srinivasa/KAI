"""Die Empfangsseite des lnd-Adapters (ADR 0018 §1/§7).

Getrennt von :mod:`app.payments.rails.lightning` aus demselben Grund wie
``lightning_scan`` und ``lightning_mapping``: der Adapter soll die drei
Sendezusagen zeigen (Timeout ist keine Ablehnung, kein Send ohne Fee-Limit,
kein Send ausser in LIVE), ohne dass der Empfangspfad dazwischenliegt — und
kein Modul im Paket erreicht 350 Zeilen (ADR §2).

Beide Funktionen bekommen den Client als Argument. Sie waehlen ihn nicht
selbst: welcher Credential-Scope hier gilt (``invoice``, nie ``payment``),
entscheidet der Adapter an EINER Stelle.

**Der Unterschied im Fehlerverhalten ist Absicht.** ``create_invoice`` wirft:
eine Forderung, die nicht entstanden ist, darf nicht als Objekt existieren.
``invoice_status`` wirft nie: eine unbeantwortete Frage nach einer Zahlung
heisst "noch nicht bezahlt", nicht "kaputt" — der Reconciler fragt gleich
wieder.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.payments.models import Invoice
from app.payments.rail import InvoiceRequest, InvoiceStatus, RailError
from app.payments.rails.lightning_mapping import normalise_payment_hash, sat, sha


async def create_invoice(client: Any, request: InvoiceRequest, *, rail: str) -> Invoice:
    """``POST /v1/invoices`` — eine eigene Forderung ausstellen.

    Das ``memo`` geht MIT an den Node. Bis zum LIVE-Fenster 2026-09-04 tat es
    das nicht: die Forderungen kamen ohne KAI-Praefix an, und die
    Einnahmenbuchung (``app/lightning/earnings_ledger.py``) — die eigene
    Invoices genau an diesem Praefix erkennt — hat sie nie gebucht. Der
    ``memo_hash`` wird aus dem GESENDETEN Text abgeleitet; ein vom Aufrufer
    mitgegebener Hash waere ein Beleg ohne Urbild.
    """
    try:
        response = await client.add_invoice(
            value_sat=request.amount.minor_units,
            memo=request.memo,
            expiry_seconds=request.expiry_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - der Node konnte nicht ausstellen
        raise RailError(f"create_invoice failed: {type(exc).__name__}: {exc}") from exc

    ref_hash = normalise_payment_hash(response.get("r_hash"))
    if not ref_hash:
        raise RailError("add_invoice returned no usable r_hash")
    # Ohne die kodierte Aufforderung kann der Zahler nichts tun; ein ``ref_hash``
    # allein ist eine Quittungsnummer ohne Rechnung.
    payment_request = str(response.get("payment_request") or "")
    if not payment_request:
        raise RailError("add_invoice returned no payment_request")
    return Invoice(
        rail=rail,
        ref_hash=ref_hash,
        amount=request.amount,
        payee_hash=sha(f"self:{rail}"),
        expires_at=datetime.now(UTC) + timedelta(seconds=request.expiry_seconds),
        memo_hash=sha(request.memo) if request.memo else "",
        payment_request=payment_request,
    )


async def invoice_status(client: Any, ref_hash: str, *, rail: str) -> InvoiceStatus:
    """``GET /v1/invoices`` — hat jemand diese Forderung beglichen?"""
    moment = datetime.now(UTC)
    wanted = normalise_payment_hash(ref_hash)
    pending = InvoiceStatus(
        rail=rail, ref_hash=wanted or ref_hash, settled=False, observed_at=moment
    )
    try:
        invoices = await client.list_invoices()
    except Exception:  # noqa: BLE001 - keine Antwort heisst "noch nicht bezahlt"
        return pending
    for raw in invoices:
        if not isinstance(raw, dict) or normalise_payment_hash(raw.get("r_hash")) != wanted:
            continue
        if not bool(raw.get("settled", False)):
            return pending
        settled_index = int(raw.get("settle_date") or 0)
        return InvoiceStatus(
            rail=rail,
            ref_hash=wanted,
            settled=True,
            observed_at=moment,
            amount_paid=sat(int(raw.get("amt_paid_sat") or 0)),
            settled_at=(
                datetime.fromtimestamp(settled_index, tz=UTC) if settled_index > 0 else None
            ),
        )
    return pending


__all__ = ["create_invoice", "invoice_status"]
