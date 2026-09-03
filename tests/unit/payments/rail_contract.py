"""Wiederverwendbare Contract-Suite fuer JEDE Rail-Implementierung (ADR 0018 §7).

Keine Testdatei, sondern eine Basisklasse: eine konkrete Rail erbt davon und
liefert drei Fixtures (``rail``, ``valid_destination``,
``invalid_destination``). Damit ist der Vertrag an EINER Stelle beschrieben und
kann nicht je Rail auseinanderlaufen.

Geprueft werden ausschliesslich Aussagen, die fuer jeden Rail gelten muessen —
nichts, was Lightning-spezifisch ist. Die wichtigste davon:

    Eine ausbleibende Antwort ist keine Aussage.

Ein Lookup auf einen unbekannten Schluessel liefert ``UNKNOWN``, niemals
``FAILED``. Der Unterschied ist der zwischen "der Node sagt, es ist nichts
geflossen" und "wir haben nichts gehoert" — und genau daran haengt, ob ein
Retry erlaubt ist.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.payments.enums import RailOutcome
from app.payments.models import HASH_LENGTH, Money
from app.payments.rail import (
    DecodedDestination,
    InvoiceRequest,
    PaymentRail,
    RailCapabilities,
    RailError,
    RailHealth,
    RailPaymentList,
)


class RailContractTests:
    """Der Vertrag, den jede Rail-Implementierung erfuellt."""

    @pytest.fixture
    def rail(self) -> PaymentRail:  # pragma: no cover - von der Unterklasse geliefert
        raise NotImplementedError

    @pytest.fixture
    def valid_destination(self) -> str:  # pragma: no cover
        raise NotImplementedError

    @pytest.fixture
    def invalid_destination(self) -> str:  # pragma: no cover
        raise NotImplementedError

    # -- Selbstauskunft ----------------------------------------------------- #

    def test_implements_the_protocol(self, rail: PaymentRail) -> None:
        assert isinstance(rail, PaymentRail)

    def test_capabilities_describe_this_rail(self, rail: PaymentRail) -> None:
        caps = rail.capabilities()
        assert isinstance(caps, RailCapabilities)
        assert caps.name == rail.name, "ein Rail muss sich unter seinem eigenen Namen melden"

    def test_capabilities_are_stable(self, rail: PaymentRail) -> None:
        """Zweimal gefragt, zweimal dieselbe Antwort — sonst ist die Policy nicht
        reproduzierbar."""
        assert rail.capabilities() == rail.capabilities()

    def test_a_reversible_rail_declares_a_window(self, rail: PaymentRail) -> None:
        caps = rail.capabilities()
        if caps.reversal_supported:
            assert caps.reversal_window is not None, (
                "Rueckbuchbarkeit ohne Frist ist keine Zusage, sondern eine Hoffnung"
            )

    async def test_health_reports_for_this_rail(self, rail: PaymentRail) -> None:
        health = await rail.health()
        assert isinstance(health, RailHealth)
        assert health.rail == rail.name
        assert health.observed_at.tzinfo is not None

    # -- Decode ------------------------------------------------------------- #

    async def test_decode_binds_a_payee(self, rail: PaymentRail, valid_destination: str) -> None:
        decoded = await rail.decode(valid_destination)
        assert isinstance(decoded, DecodedDestination)
        assert decoded.rail == rail.name
        assert len(decoded.payee_hash) == HASH_LENGTH
        assert decoded.rail_dedup_key

    async def test_decode_is_deterministic(self, rail: PaymentRail, valid_destination: str) -> None:
        first = await rail.decode(valid_destination)
        second = await rail.decode(valid_destination)
        assert first.payee_hash == second.payee_hash
        assert first.rail_dedup_key == second.rail_dedup_key

    async def test_decode_refuses_an_unusable_destination(
        self, rail: PaymentRail, invalid_destination: str
    ) -> None:
        """Kein ``None``, kein leerer Payee: die Allowlist braucht eine Bindung."""
        with pytest.raises(RailError):
            await rail.decode(invalid_destination)

    # -- Lookup ------------------------------------------------------------- #

    async def test_lookup_of_an_unknown_key_is_unknown_not_failed(self, rail: PaymentRail) -> None:
        lookup = await rail.lookup("f" * 64)
        assert lookup.found is False
        assert lookup.outcome is RailOutcome.UNKNOWN
        assert lookup.outcome is not RailOutcome.FAILED, (
            "'nicht gefunden' ist kein Beweis dafuer, dass nichts geflossen ist"
        )

    # -- Invoice ------------------------------------------------------------ #

    async def test_create_invoice_returns_hashes_only(self, rail: PaymentRail) -> None:
        caps = rail.capabilities()
        request = InvoiceRequest(
            amount=Money(minor_units=1000, currency="SAT", scale=0),
            purpose="self_test",
        )
        invoice = await rail.create_invoice(request)
        assert invoice.rail == caps.name
        assert len(invoice.ref_hash) == HASH_LENGTH
        assert len(invoice.payee_hash) == HASH_LENGTH
        assert invoice.amount.minor_units == 1000

    async def test_a_fresh_invoice_is_not_settled(self, rail: PaymentRail) -> None:
        invoice = await rail.create_invoice(
            InvoiceRequest(
                amount=Money(minor_units=1000, currency="SAT", scale=0), purpose="self_test"
            )
        )
        status = await rail.invoice_status(invoice.ref_hash)
        assert status.settled is False
        assert status.ref_hash == invoice.ref_hash

    async def test_invoice_status_of_an_unknown_ref_is_not_settled(self, rail: PaymentRail) -> None:
        status = await rail.invoice_status("e" * 64)
        assert status.settled is False

    # -- Rueckwaerts-Reconciliation (ADR §8) -------------------------------- #

    async def test_list_payments_reports_its_own_window_honesty(self, rail: PaymentRail) -> None:
        """Ein Rail muss SAGEN, ob er das Zeitfenster einhalten konnte.

        Der Reconciler leitet daraus ab, ob ein unbekannter Send ein echter
        Waisen-Settlement ist oder nur Node-Historie von vor der Inbetriebnahme.
        Ein Rail, der ein Fenster nur BEHAUPTET, wuerde jede alte Zahlung als
        Waise melden — ein Daueralarm am ersten Tag.
        """
        listing = await rail.list_payments(datetime(2020, 1, 1, tzinfo=UTC))
        assert isinstance(listing, RailPaymentList)
        assert listing.rail == rail.name
        for payment in listing.payments:
            assert payment.rail == rail.name
            assert payment.rail_dedup_key
            assert payment.observed_at.tzinfo is not None
