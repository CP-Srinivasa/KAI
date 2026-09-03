"""Der lnd-Adapter (ADR 0017 §7).

Kein Test spricht mit einem Node: der Client wird ueber ``client_factory``
injiziert. Was geprueft wird, sind die drei Zusagen, an denen im Bestand Geld
haengen blieb:

* Ein Timeout ist keine Ablehnung — ``UNKNOWN``, nie ``FAILED``.
* Kein Send ohne Fee-Limit > 0 (``client.py`` laesst das Feld sonst weg, und
  lnd routet ohne Obergrenze).
* Kein Send ausser in LIVE mit gesetztem Kill-Switch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import PaymentSettings
from app.payments.enums import PaymentMode, RailOutcome
from app.payments.models import Money, PaymentAttempt, PaymentIntent
from app.payments.rail import InvoiceRequest, RailError
from app.payments.rails.lightning import LightningRail

from .rail_contract import RailContractTests

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
PAYEE_PUBKEY = "03" + "ab" * 32
PAYMENT_HASH = "1a" * 32
PREIMAGE_HASH = "2b" * 32
BOLT11 = "lnbc10u1pexamplepaymentrequest"


class FakeLndPayment:
    """Die Felder, die ``LndPaymentPage`` fuehrt — mehr braucht der Adapter nicht."""

    def __init__(
        self,
        payment_hash: str,
        status: str,
        *,
        value_sat: int = 1000,
        fee_sat: int = 2,
        failure_reason: str = "",
    ) -> None:
        self.payment_hash = payment_hash
        self.status = status
        self.value_sat = value_sat
        self.fee_sat = fee_sat
        self.failure_reason = failure_reason
        self.payment_index = 1


class FakePage:
    def __init__(self, payments: list[FakeLndPayment], *, next_offset: int = 0) -> None:
        self.payments = tuple(payments)
        self.next_index_offset = next_offset


class FakeClient:
    """Ein lnd, das genau das sagt, was der Test will — und sonst nichts."""

    def __init__(self, **behaviour: Any) -> None:
        self.behaviour = behaviour
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_state(self) -> str:
        self.calls.append(("get_state", {}))
        if isinstance(self.behaviour.get("state"), Exception):
            raise self.behaviour["state"]
        return str(self.behaviour.get("state", "SERVER_ACTIVE"))

    async def get_info(self) -> Any:
        self.calls.append(("get_info", {}))
        if isinstance(self.behaviour.get("info"), Exception):
            raise self.behaviour["info"]

        class Info:
            synced_to_chain = self.behaviour.get("synced_to_chain", True)
            synced_to_graph = self.behaviour.get("synced_to_graph", True)

        return Info()

    async def decode_pay_req(self, *, payment_request: str) -> dict[str, Any]:
        self.calls.append(("decode_pay_req", {"payment_request": payment_request}))
        decoded = self.behaviour.get("decoded")
        if isinstance(decoded, Exception):
            raise decoded
        if decoded is not None:
            return dict(decoded)
        return {
            "destination": PAYEE_PUBKEY,
            "payment_hash": PAYMENT_HASH,
            "num_satoshis": "1000",
            "expiry": "3600",
            "timestamp": str(int(NOW.timestamp())),
            "description": "test",
        }

    async def pay_invoice(self, *, payment_request: str, fee_limit_sat: int = 0) -> dict[str, Any]:
        self.calls.append(
            ("pay_invoice", {"payment_request": payment_request, "fee_limit_sat": fee_limit_sat})
        )
        response = self.behaviour.get("pay")
        if isinstance(response, Exception):
            raise response
        if response is not None:
            return dict(response)
        return {"payment_preimage": PREIMAGE_HASH, "payment_route": {"total_fees": "3"}}

    async def list_payments(self, **kwargs: Any) -> Any:
        self.calls.append(("list_payments", kwargs))
        pages = self.behaviour.get("payments")
        if isinstance(pages, Exception):
            raise pages
        return FakePage(list(pages or []))

    async def add_invoice(self, *, value_sat: int, expiry_seconds: int = 300) -> dict[str, Any]:
        self.calls.append(
            ("add_invoice", {"value_sat": value_sat, "expiry_seconds": expiry_seconds})
        )
        response = self.behaviour.get("add_invoice")
        if isinstance(response, Exception):
            raise response
        return dict(response or {"r_hash": PAYMENT_HASH})

    async def list_invoices(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("list_invoices", kwargs))
        invoices = self.behaviour.get("invoices")
        if isinstance(invoices, Exception):
            raise invoices
        return list(invoices or [])


def ln_settings(**overrides: Any) -> LightningSettings:
    base: dict[str, Any] = {
        "enabled": True,
        "tls_cert_path": "/dev/null",
        "macaroon_hex": "ab",
        "pay_enabled": True,
    }
    base.update(overrides)
    return LightningSettings(**base)


def pay_settings(**overrides: Any) -> PaymentSettings:
    base: dict[str, Any] = {"mode": "live", "fee_limit_default_ppm": 3000, "fee_limit_max_sat": 200}
    base.update(overrides)
    return PaymentSettings(**base)


def a_rail(client: FakeClient | None = None, **overrides: Any) -> LightningRail:
    fake = client or FakeClient()
    return LightningRail(
        payment_settings=pay_settings(**overrides.pop("payments", {})),
        lightning_settings=ln_settings(**overrides.pop("lightning", {})),
        client_factory=lambda scope: fake,
    )


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def an_intent(**overrides: Any) -> PaymentIntent:
    base: dict[str, Any] = {
        "intent_id": "pi_1",
        "idempotency_key": "idem-0123456789abcdef",
        "correlation_id": "corr-1",
        "actor": "operator",
        "purpose": "self_test",
        "rail": "lightning",
        "destination": BOLT11,
        "amount_requested": sat(1000),
        "fee_limit": sat(10),
        "created_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
        "mode": PaymentMode.LIVE,
    }
    base.update(overrides)
    return PaymentIntent(**base)


def an_attempt() -> PaymentAttempt:
    return PaymentAttempt(
        attempt_no=1,
        intent_id="pi_1",
        rail_dedup_key=PAYMENT_HASH,
        submitted_at=NOW,
        amount_sent=sat(1000),
    )


class TestLightningRailContract(RailContractTests):
    """Dieselbe Suite wie fuer den Simulationsrail — gegen einen gefakten Node."""

    @pytest.fixture
    def rail(self) -> LightningRail:
        client = FakeClient(payments=[], invoices=[])
        return a_rail(client)

    @pytest.fixture
    def valid_destination(self) -> str:
        return BOLT11

    @pytest.fixture
    def invalid_destination(self) -> str:
        return "   "


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #


async def test_healthy_node_is_healthy() -> None:
    health = await a_rail(FakeClient()).health()
    assert health.healthy is True
    assert health.wallet_locked is False


@pytest.mark.parametrize(
    ("behaviour", "field"),
    [
        ({"synced_to_chain": False}, "synced_to_chain"),
        ({"synced_to_graph": False}, "synced_to_graph"),
    ],
)
async def test_unsynced_node_is_unhealthy(behaviour: dict[str, Any], field: str) -> None:
    health = await a_rail(FakeClient(**behaviour)).health()
    assert health.healthy is False
    assert getattr(health, field) is False


async def test_locked_wallet_is_unhealthy() -> None:
    health = await a_rail(FakeClient(state="LOCKED")).health()
    assert health.wallet_locked is True
    assert health.healthy is False


async def test_unreachable_node_is_unhealthy_not_an_exception() -> None:
    health = await a_rail(FakeClient(state=TimeoutError("no route to host"))).health()
    assert health.healthy is False
    assert health.reachable is False
    assert "TimeoutError" in health.reason


async def test_a_disabled_client_is_unhealthy_without_calling_the_node() -> None:
    client = FakeClient()
    rail = a_rail(client, lightning={"enabled": False, "tls_cert_path": ""})
    health = await rail.health()
    assert health.healthy is False
    assert client.calls == []


# --------------------------------------------------------------------------- #
# decode
# --------------------------------------------------------------------------- #


async def test_decode_hashes_the_destination_pubkey() -> None:
    import hashlib

    decoded = await a_rail(FakeClient()).decode(BOLT11)
    assert decoded.payee_hash == hashlib.sha256(PAYEE_PUBKEY.encode()).hexdigest()
    assert decoded.rail_dedup_key == PAYMENT_HASH
    assert decoded.amount == sat(1000)
    assert decoded.expires_at == NOW + timedelta(hours=1)


async def test_decode_never_returns_the_raw_pubkey() -> None:
    decoded = await a_rail(FakeClient()).decode(BOLT11)
    assert PAYEE_PUBKEY not in decoded.model_dump_json()


async def test_decode_without_a_payment_hash_is_refused() -> None:
    """Ohne Dedup-Schluessel gibt es nichts, woran ein Retry gebunden waere."""
    client = FakeClient(decoded={"destination": PAYEE_PUBKEY, "payment_hash": ""})
    with pytest.raises(RailError):
        await a_rail(client).decode(BOLT11)


async def test_decode_without_a_destination_is_refused() -> None:
    client = FakeClient(decoded={"destination": "", "payment_hash": PAYMENT_HASH})
    with pytest.raises(RailError):
        await a_rail(client).decode(BOLT11)


async def test_a_node_error_during_decode_is_a_rail_error() -> None:
    with pytest.raises(RailError):
        await a_rail(FakeClient(decoded=TimeoutError("slow"))).decode(BOLT11)


# --------------------------------------------------------------------------- #
# quote
# --------------------------------------------------------------------------- #


async def test_quote_falls_back_to_settings_ppm_and_says_so() -> None:
    quote = await a_rail(FakeClient()).quote(an_intent())
    assert quote.estimate_source == "settings_ppm"
    assert quote.fee_estimate.minor_units == 3  # 1000 sat * 3000 ppm


async def test_quote_is_capped_by_the_configured_maximum() -> None:
    rail = a_rail(
        FakeClient(), payments={"fee_limit_default_ppm": 900_000, "fee_limit_max_sat": 50}
    )
    quote = await rail.quote(an_intent())
    assert quote.fee_estimate.minor_units == 50


async def test_quote_never_touches_the_send_path() -> None:
    client = FakeClient()
    await a_rail(client).quote(an_intent())
    assert not any(call == "pay_invoice" for call, _ in client.calls)


# --------------------------------------------------------------------------- #
# pay — die drei Tore
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["simulation", "shadow"])
async def test_pay_is_refused_outside_live(mode: str) -> None:
    client = FakeClient()
    rail = a_rail(client, payments={"mode": mode})
    with pytest.raises(RailError, match="pay refused"):
        await rail.pay(an_intent(mode=PaymentMode(mode)), an_attempt())
    assert client.calls == [], "SHADOW darf den Sendepfad nicht einmal beruehren"


async def test_pay_is_refused_when_the_kill_switch_is_off() -> None:
    client = FakeClient()
    rail = a_rail(client, lightning={"pay_enabled": False})
    with pytest.raises(RailError, match="APP_LN_PAY_ENABLED"):
        await rail.pay(an_intent(), an_attempt())
    assert client.calls == []


async def test_pay_is_refused_without_a_positive_fee_limit() -> None:
    """``client.py`` sendet ``fee_limit`` nur bei > 0 — 0 waere unbegrenzt."""
    client = FakeClient()
    with pytest.raises(RailError, match="fee_limit"):
        await a_rail(client).pay(an_intent(fee_limit=sat(0)), an_attempt())
    assert client.calls == []


async def test_pay_passes_the_fee_limit_through() -> None:
    client = FakeClient()
    await a_rail(client).pay(an_intent(fee_limit=sat(17)), an_attempt())
    assert client.calls[-1] == ("pay_invoice", {"payment_request": BOLT11, "fee_limit_sat": 17})


# --------------------------------------------------------------------------- #
# pay — Ausgaenge
# --------------------------------------------------------------------------- #


async def test_a_preimage_is_a_settlement() -> None:
    result = await a_rail(FakeClient()).pay(an_intent(), an_attempt())
    assert result.outcome is RailOutcome.SETTLED
    assert result.proof is not None
    assert result.fee_actual == sat(3)


async def test_a_timeout_is_unknown_never_failed() -> None:
    """Der 25k-Fall: der Client laeuft in ein Timeout, der Send ist vielleicht raus."""
    result = await a_rail(FakeClient(pay=TimeoutError("read timeout"))).pay(
        an_intent(), an_attempt()
    )
    assert result.outcome is RailOutcome.UNKNOWN
    assert result.outcome is not RailOutcome.FAILED
    assert result.proof is None


async def test_a_transport_error_is_unknown() -> None:
    result = await a_rail(FakeClient(pay=ConnectionResetError("peer reset"))).pay(
        an_intent(), an_attempt()
    )
    assert result.outcome is RailOutcome.UNKNOWN


async def test_a_payment_error_in_a_200_is_a_failure() -> None:
    """lnd antwortet 200 auch bei gescheiterter Zahlung (``payment_error``)."""
    client = FakeClient(pay={"payment_error": "no route to " + PAYEE_PUBKEY})
    result = await a_rail(client).pay(an_intent(), an_attempt())
    assert result.outcome is RailOutcome.FAILED
    assert result.failure_reason == "PAYMENT_ERROR"
    assert PAYEE_PUBKEY not in result.model_dump_json(), (
        "der lnd-Fehlerstring kann ein Ziel zurueckspiegeln und wird nie uebernommen"
    )


async def test_a_200_without_preimage_is_unknown() -> None:
    result = await a_rail(FakeClient(pay={})).pay(an_intent(), an_attempt())
    assert result.outcome is RailOutcome.UNKNOWN


# --------------------------------------------------------------------------- #
# lookup
# --------------------------------------------------------------------------- #


async def test_lookup_finds_a_succeeded_payment() -> None:
    client = FakeClient(payments=[FakeLndPayment(PAYMENT_HASH, "SUCCEEDED")])
    lookup = await a_rail(client).lookup(PAYMENT_HASH)
    assert lookup.found is True
    assert lookup.outcome is RailOutcome.SETTLED
    assert lookup.amount_sent == sat(1000)
    assert lookup.fee_actual == sat(2)
    assert lookup.proof is not None


async def test_lookup_maps_failed() -> None:
    client = FakeClient(
        payments=[FakeLndPayment(PAYMENT_HASH, "FAILED", failure_reason="FAILURE_REASON_NO_ROUTE")]
    )
    lookup = await a_rail(client).lookup(PAYMENT_HASH)
    assert lookup.outcome is RailOutcome.FAILED
    assert lookup.proof is None


@pytest.mark.parametrize("status", ["IN_FLIGHT", "INITIATED"])
async def test_lookup_maps_in_flight(status: str) -> None:
    client = FakeClient(payments=[FakeLndPayment(PAYMENT_HASH, status)])
    lookup = await a_rail(client).lookup(PAYMENT_HASH)
    assert lookup.outcome is RailOutcome.IN_FLIGHT


async def test_lookup_of_an_absent_payment_is_unknown_not_failed() -> None:
    client = FakeClient(payments=[FakeLndPayment("ff" * 32, "SUCCEEDED")])
    lookup = await a_rail(client).lookup(PAYMENT_HASH)
    assert lookup.found is False
    assert lookup.outcome is RailOutcome.UNKNOWN


async def test_lookup_survives_a_node_error_as_unknown() -> None:
    lookup = await a_rail(FakeClient(payments=TimeoutError("slow"))).lookup(PAYMENT_HASH)
    assert lookup.found is False
    assert lookup.outcome is RailOutcome.UNKNOWN


# --------------------------------------------------------------------------- #
# Empfangen
# --------------------------------------------------------------------------- #


async def test_create_invoice_uses_the_invoice_scope() -> None:
    scopes: list[str] = []
    client = FakeClient()
    rail = LightningRail(
        payment_settings=pay_settings(),
        lightning_settings=ln_settings(),
        client_factory=lambda scope: (scopes.append(scope), client)[1],
    )
    invoice = await rail.create_invoice(InvoiceRequest(amount=sat(1000), purpose="self_test"))
    assert scopes == ["invoice"], "Empfangen braucht nie ein Sende-Credential"
    assert invoice.ref_hash == PAYMENT_HASH


async def test_create_invoice_without_a_usable_hash_is_refused() -> None:
    with pytest.raises(RailError):
        await a_rail(FakeClient(add_invoice={"r_hash": ""})).create_invoice(
            InvoiceRequest(amount=sat(1000), purpose="self_test")
        )


async def test_invoice_status_reports_a_settlement() -> None:
    client = FakeClient(
        invoices=[
            {
                "r_hash": PAYMENT_HASH,
                "settled": True,
                "amt_paid_sat": "1000",
                "settle_date": str(int(NOW.timestamp())),
            }
        ]
    )
    status = await a_rail(client).invoice_status(PAYMENT_HASH)
    assert status.settled is True
    assert status.amount_paid == sat(1000)
    assert status.settled_at == NOW


async def test_invoice_status_of_an_unreachable_node_is_pending_not_settled() -> None:
    status = await a_rail(FakeClient(invoices=TimeoutError("slow"))).invoice_status(PAYMENT_HASH)
    assert status.settled is False


# --------------------------------------------------------------------------- #
# Geheimnisse
# --------------------------------------------------------------------------- #


async def test_the_rail_never_exposes_a_macaroon() -> None:
    rail = a_rail(FakeClient(), lightning={"macaroon_hex": "deadbeefcafe"})
    result = await rail.pay(an_intent(), an_attempt())
    assert "deadbeefcafe" not in result.model_dump_json()
    assert "deadbeefcafe" not in repr(rail.capabilities())
