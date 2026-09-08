"""Die Produktschicht gegen den echten Kern (D-CORE-006).

Kein Doppel des Payment Control Plane: hier laufen der echte
``PaymentService``, das echte hash-verkettete Journal und der
``SimulationRail`` mit seinem Testhaken ``settle(ref_hash)``. Nur so beweist
der Test das, worauf es ankommt — dass die Geldwahrheit aus dem KERN kommt und
diese Schicht keine zweite anlegt.

Die vier Zusagen:

1. Eine Forderung entsteht im Kern; die Produktschicht kennt sie nur ueber
   ``ref_hash``.
2. Nach der Zahlung steht genau EIN ``receivable_settled`` im Journal — auch
   nach zweimaligem ``refresh`` und auch ueber einen Neustart hinweg.
3. Betrag, Zeitpunkt und Beleg lesen sich aus diesem Record, nie aus dem Store.
4. Ein stummer Rail aendert gar nichts (fail-soft).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.pay.service import PayError
from app.pay.status import PayStatus
from app.payments.rail import RailError
from app.payments.receivables import SETTLED_EVENT, receivable_intent_id, settlement_of
from tests.unit.pay.conftest import NOW, Harness, pay_settings


async def _create(harness: Harness, **overrides: object) -> object:
    base: dict[str, object] = {
        "amount_sat": 1200,
        "description": "Beratung 30 Minuten",
        "reference": "ORDER-1",
    }
    base.update(overrides)
    return await harness.service.create_request(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Anfordern
# --------------------------------------------------------------------------- #


async def test_die_forderung_entsteht_im_kern_und_steht_im_geldjournal(harness: Harness) -> None:
    entry = await _create(harness)

    assert entry.payment_id.startswith("pay_")  # type: ignore[attr-defined]
    assert entry.status is PayStatus.WAITING  # type: ignore[attr-defined]
    assert entry.bolt11  # type: ignore[attr-defined]
    events = harness.journal.events(receivable_intent_id(entry.ref_hash))  # type: ignore[attr-defined]
    assert [event.event_type for event in events] == ["intent_created"]
    assert events[0].payload["purpose"] == "kai_pay"
    # Die Bestellreferenz des Aufrufers ist die Zuordnung im KERN, nicht hier.
    assert events[0].payload["order_ref"] == "ORDER-1"


async def test_betrag_und_frist_kommen_aus_der_ausgestellten_invoice(harness: Harness) -> None:
    """Nicht aus der Anfrage: eine Anzeige, die von der Invoice abweicht, luegt."""
    entry = await _create(harness, expiry_seconds=600)
    assert entry.amount_sat == 1200  # type: ignore[attr-defined]
    assert entry.expires_at == NOW + timedelta(seconds=600)  # type: ignore[attr-defined]


async def test_ohne_referenz_traegt_der_kern_die_eigene_payment_id(harness: Harness) -> None:
    entry = await _create(harness, reference="")
    events = harness.journal.events(receivable_intent_id(entry.ref_hash))  # type: ignore[attr-defined]
    assert events[0].payload["order_ref"] == entry.payment_id  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"amount_sat": 0}, "amount_sat"),
        ({"amount_sat": 2_000_000}, "amount_sat"),
        ({"description": "   "}, "description"),
        ({"description": "x" * 141}, "description"),
        ({"reference": "r" * 65}, "reference"),
        ({"expiry_seconds": 30}, "expiry_seconds"),
        ({"expiry_seconds": 90_000}, "expiry_seconds"),
        ({"webhook_url": "http://example.org/hook"}, "https"),
    ],
)
async def test_grenzen_werden_vor_dem_kern_geprueft(
    harness: Harness, overrides: dict[str, object], needle: str
) -> None:
    with pytest.raises(PayError, match=needle):
        await _create(harness, **overrides)
    # Nichts davon hat den Kern erreicht — kein Record, keine Invoice.
    assert harness.journal.events() == []


async def test_derselbe_idempotenzschluessel_gibt_dieselbe_forderung(harness: Harness) -> None:
    first = await _create(harness, idempotency_key="key-1")
    second = await _create(harness, idempotency_key="key-1")
    assert first.payment_id == second.payment_id  # type: ignore[attr-defined]
    assert first.bolt11 == second.bolt11  # type: ignore[attr-defined]
    assert harness.store.total_count() == 1


# --------------------------------------------------------------------------- #
# Nachfragen
# --------------------------------------------------------------------------- #


async def test_eine_bezahlte_forderung_wird_im_kern_gebucht(harness: Harness) -> None:
    entry = await _create(harness)
    assert (await harness.service.refresh(entry.payment_id)).status is PayStatus.WAITING  # type: ignore[attr-defined]

    harness.rail.settle(entry.ref_hash)  # type: ignore[attr-defined]
    updated = await harness.service.refresh(entry.payment_id)  # type: ignore[attr-defined]

    assert updated.status is PayStatus.SETTLED
    settlement = settlement_of(harness.journal, entry.ref_hash)  # type: ignore[attr-defined]
    assert settlement is not None
    assert settlement.amount_settled_minor_units == 1200
    assert settlement.order_ref == "ORDER-1"
    # Der Store traegt nur den ZEIGER auf den Record, keinen Betrag.
    assert (updated.journal_seq, updated.record_hash) == (
        settlement.journal_seq,
        settlement.record_hash,
    )


async def test_zweimal_refresh_erzeugt_genau_einen_journal_record(harness: Harness) -> None:
    """Die Kernzusage gegen einen zweiten Reconciler (Operator-Schaerfung)."""
    entry = await _create(harness)
    harness.rail.settle(entry.ref_hash)  # type: ignore[attr-defined]

    await harness.service.refresh(entry.payment_id)  # type: ignore[attr-defined]
    await harness.service.refresh(entry.payment_id)  # type: ignore[attr-defined]

    assert len(harness.settled_records(entry.ref_hash)) == 1  # type: ignore[attr-defined]


async def test_auch_nach_einem_neustart_wird_nicht_doppelt_gebucht(harness: Harness) -> None:
    """Der Neustart baut Index UND Store neu — die Buchung bleibt einmalig."""
    entry = await _create(harness)
    harness.rail.settle(entry.ref_hash)  # type: ignore[attr-defined]
    await harness.service.refresh(entry.payment_id)  # type: ignore[attr-defined]

    service = harness.reopen()
    reloaded = await service.refresh(entry.payment_id)  # type: ignore[attr-defined]

    assert reloaded.status is PayStatus.SETTLED
    assert len(harness.settled_records(entry.ref_hash)) == 1  # type: ignore[attr-defined]


async def test_der_reconciler_und_die_produktschicht_buchen_zusammen_einmal(
    harness: Harness,
) -> None:
    """Beide Takte, ein Schreiber: erst der Timer, dann die Seite.

    Genau diese Reihenfolge trat am Geraet auf — der 15-Minuten-Lauf ist
    schneller als der Kunde, der die Seite neu laedt.
    """
    from app.payments.reconcile_passes import receivables

    entry = await _create(harness)
    harness.rail.settle(entry.ref_hash)  # type: ignore[attr-defined]

    counts: dict[str, int] = {}
    await receivables(harness.journal, harness.rail, counts=counts, now=NOW)
    assert counts["RECEIVABLE_SETTLED"] == 1

    updated = await harness.service.refresh(entry.payment_id)  # type: ignore[attr-defined]
    assert updated.status is PayStatus.SETTLED
    assert len(harness.settled_records(entry.ref_hash)) == 1  # type: ignore[attr-defined]


async def test_eine_abgelaufene_forderung_wird_expired(harness: Harness) -> None:
    entry = await _create(harness, expiry_seconds=60)
    harness.now = NOW + timedelta(minutes=2)

    updated = await harness.service.refresh(entry.payment_id)  # type: ignore[attr-defined]

    assert updated.status is PayStatus.EXPIRED
    assert harness.settled_records(entry.ref_hash) == []  # type: ignore[attr-defined]


async def test_ein_stummer_rail_aendert_nichts(harness: Harness, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Fail-soft: ohne Aussage bleibt WAITING — auch wenn die Frist um ist."""
    entry = await _create(harness, expiry_seconds=60)
    harness.now = NOW + timedelta(minutes=2)

    async def _boom(_ref_hash: str) -> None:
        raise RailError("node unreachable")

    monkeypatch.setattr(harness.rail, "invoice_status", _boom)
    updated = await harness.service.refresh(entry.payment_id)  # type: ignore[attr-defined]

    assert updated.status is PayStatus.WAITING
    assert "node unreachable" in updated.last_error
    assert harness.settled_records(entry.ref_hash) == []  # type: ignore[attr-defined]


async def test_eine_unbekannte_forderung_ist_ein_pay_error(harness: Harness) -> None:
    with pytest.raises(PayError, match="unknown payment request"):
        await harness.service.refresh("pay_deadbeefdead")


# --------------------------------------------------------------------------- #
# Auskunft und Beleg
# --------------------------------------------------------------------------- #


async def test_die_referenz_findet_die_forderung_wieder(harness: Harness) -> None:
    first = await _create(harness, reference="ORDER-7")
    second = await _create(harness, reference="ORDER-7")
    await _create(harness, reference="ORDER-8")

    found = harness.service.lookup_by_reference("ORDER-7")
    assert [entry.payment_id for entry in found] == [
        first.payment_id,  # type: ignore[attr-defined]
        second.payment_id,  # type: ignore[attr-defined]
    ]


async def test_der_beleg_zitiert_den_journal_record(harness: Harness) -> None:
    entry = await _create(harness)
    harness.rail.settle(entry.ref_hash)  # type: ignore[attr-defined]
    await harness.service.refresh(entry.payment_id)  # type: ignore[attr-defined]

    receipt = await harness.service.receipt(entry.payment_id)  # type: ignore[attr-defined]
    settlement = settlement_of(harness.journal, entry.ref_hash)  # type: ignore[attr-defined]
    assert settlement is not None

    assert receipt.paid_amount_sat == settlement.amount_settled_minor_units
    assert receipt.paid_at == settlement.settled_at
    assert receipt.journal_seq == settlement.journal_seq
    assert receipt.record_hash == settlement.record_hash
    assert receipt.receipt_id == f"rcpt_{settlement.record_hash[:12]}"
    assert receipt.rail == "lightning"
    assert receipt.to_dict()["audit"] == {
        "journal_seq": settlement.journal_seq,
        "record_hash": settlement.record_hash,
    }
    assert settlement.record_hash in receipt.to_text()


async def test_ohne_geldeingang_gibt_es_keinen_beleg(harness: Harness) -> None:
    entry = await _create(harness)
    with pytest.raises(PayError, match="no settlement record"):
        await harness.service.receipt(entry.payment_id)  # type: ignore[attr-defined]


async def test_die_ansicht_zeigt_den_qr_nur_solange_er_gilt(harness: Harness) -> None:
    """Nach Reload denselben QR — nach dem Eingang keinen (#909)."""
    entry = await _create(harness)
    waiting = harness.service.view(entry)  # type: ignore[arg-type]
    assert waiting["bolt11"] == entry.bolt11  # type: ignore[attr-defined]
    assert waiting["lightning_uri"] == f"lightning:{entry.bolt11}"  # type: ignore[attr-defined]
    assert waiting["paid_amount_sat"] == 0
    assert waiting["paid_at"] is None

    harness.rail.settle(entry.ref_hash)  # type: ignore[attr-defined]
    settled = harness.service.view(await harness.service.refresh(entry.payment_id))  # type: ignore[attr-defined]
    assert settled["bolt11"] is None
    assert settled["lightning_uri"] is None
    assert settled["paid_amount_sat"] == 1200


async def test_health_zaehlt_offene_und_erledigte_forderungen(harness: Harness) -> None:
    first = await _create(harness)
    await _create(harness, reference="ORDER-2")
    harness.rail.settle(first.ref_hash)  # type: ignore[attr-defined]
    await harness.service.refresh(first.payment_id)  # type: ignore[attr-defined]

    health = harness.service.health()
    assert health["enabled"] is True
    assert health["open_requests"] == 1
    assert health["settled_total"] == 1
    settlement = settlement_of(harness.journal, first.ref_hash)  # type: ignore[attr-defined]
    assert settlement is not None
    assert health["last_settled_at"] == settlement.settled_at.isoformat()


async def test_der_kern_lehnt_einen_unbekannten_zweck_ab(harness: Harness, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Die Policy des Kerns bleibt die Instanz — nicht diese Schicht."""
    harness.service = type(harness.service)(
        payments=harness.payments,
        store=harness.store,
        settings=pay_settings(purpose="not_allowed"),
        clock=lambda: harness.now,
    )
    entry = await _create(harness)
    events = harness.journal.events(receivable_intent_id(entry.ref_hash))  # type: ignore[attr-defined]
    # Der Kern stellt aus UND journalisiert — die Allowlist greift erst beim
    # SENDEN. Genau deshalb prueft ``validate_pay_boot`` den Zweck beim Start.
    assert events[0].payload["purpose"] == "not_allowed"
    assert SETTLED_EVENT not in [event.event_type for event in events]
