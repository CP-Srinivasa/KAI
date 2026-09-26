"""``/pay`` — Telegram-Handler ueber den Payment Control Plane (D-277).

Der Handler ist Transport: er parst, ruft den Service, formatiert. Der Fake
unten ist der Service-Vertrag (``create_intent``/``simulate``/``authorize``/
``execute``/``get``/``settings``) — kein Node, kein Journal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routers.ln_control_delegate import DEFAULT_PURPOSE
from app.messaging import pay_telegram_commands as pay
from app.messaging.pay_telegram_commands import PENDING_TTL, PayFlow, handle_pay
from app.payments.enums import PaymentStatus
from app.payments.models import Money, Quote
from app.payments.service_types import (
    IntentView,
    PaymentRequest,
    PaymentServiceError,
    SimulationView,
)

BOLT11 = "lnbc10u1pexampleinvoicebody"  # 10 µBTC = 1000 sat
CHAT = 12345
NOW = datetime(2026, 9, 14, 20, 0, tzinfo=UTC)


def sat(n: int) -> Money:
    return Money(minor_units=n, currency="SAT", scale=0)


@dataclass
class FakeService:
    """Nur der Vertrag, den ``/pay`` nutzt."""

    mode: str = "live"
    fee_limit_min_sat: int = 1
    fee_limit_max_sat: int = 200
    first_status: PaymentStatus = PaymentStatus.AWAITING_APPROVAL
    replayed: bool = False
    execute_status: PaymentStatus = PaymentStatus.SETTLED
    execute_error: Exception | None = None
    authorize_error: Exception | None = None
    simulate_error: Exception | None = None
    quote_fee: int | None = 7
    quote_source: str = "settings_ppm"
    decision: Any = None
    calls: list[tuple[str, Any]] = field(default_factory=list)
    status: PaymentStatus | None = None
    requests: list[PaymentRequest] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)

    @property
    def settings(self) -> Any:
        return SimpleNamespace(
            mode=self.mode,
            fee_limit_default_ppm=3_000,
            fee_limit_min_sat=self.fee_limit_min_sat,
            fee_limit_max_sat=self.fee_limit_max_sat,
        )

    async def create_intent(self, request: PaymentRequest, key: str) -> IntentView:
        self.calls.append(("create_intent", key))
        self.requests.append(request)
        self.keys.append(key)
        self.status = self.first_status
        return IntentView(
            intent_id="pi_test",
            status=self.status,
            replayed=self.replayed,
            decision=self.decision,
        )

    async def simulate(self, intent_id: str) -> SimulationView:
        self.calls.append(("simulate", intent_id))
        if self.simulate_error:
            raise self.simulate_error
        quote = None
        if self.quote_fee is not None:
            quote = Quote(
                rail="lightning",
                amount=sat(1000),
                fee_estimate=sat(self.quote_fee),
                valid_until=NOW + timedelta(minutes=5),
                estimate_source=self.quote_source,
            )
        assert self.status is not None
        return SimulationView(intent_id=intent_id, status=self.status, quote=quote)

    def get(self, intent_id: str) -> IntentView:
        self.calls.append(("get", intent_id))
        if self.status is None:
            raise PaymentServiceError("unknown intent")
        return IntentView(intent_id=intent_id, status=self.status)

    def authorize(self, intent_id: str, code: str) -> IntentView:
        self.calls.append(("authorize", code))
        if self.authorize_error:
            raise self.authorize_error
        self.status = PaymentStatus.AUTHORIZED
        return IntentView(intent_id=intent_id, status=self.status)

    async def execute(self, intent_id: str) -> IntentView:
        self.calls.append(("execute", intent_id))
        if self.execute_error:
            raise self.execute_error
        self.status = self.execute_status
        return IntentView(intent_id=intent_id, status=self.status)


async def run(flow: PayFlow, service: FakeService, args: str, *, now: datetime = NOW) -> str:
    return await handle_pay(args, service, CHAT, flow=flow, now=now)


# --------------------------------------------------------------------------- #
# Parsen
# --------------------------------------------------------------------------- #


async def test_bare_pay_shows_usage_and_touches_nothing() -> None:
    service = FakeService()
    reply = await run(PayFlow(), service, "")
    assert "/pay <bolt11>" in reply
    assert service.calls == []


async def test_non_invoice_input_is_rejected_without_a_service_call() -> None:
    service = FakeService()
    reply = await run(PayFlow(), service, "1000 an alice")
    assert reply.startswith("❌")
    assert service.calls == []


async def test_extra_arguments_are_rejected_no_fee_or_amount_from_the_command() -> None:
    service = FakeService()
    reply = await run(PayFlow(), service, f"{BOLT11} 50")
    assert "Nur die Rechnung" in reply
    assert service.calls == []


async def test_an_amountless_invoice_is_refused() -> None:
    service = FakeService()
    reply = await run(PayFlow(), service, "lnbc1pamountless")
    assert "ohne Betrag" in reply
    assert service.calls == []


# --------------------------------------------------------------------------- #
# Vorschau
# --------------------------------------------------------------------------- #


async def test_preview_creates_the_intent_with_settings_fee_limit_and_shows_the_quote() -> None:
    service = FakeService()
    flow = PayFlow()
    reply = await run(flow, service, BOLT11)

    request = service.requests[0]
    assert request.purpose == DEFAULT_PURPOSE == pay.PAY_PURPOSE
    assert request.actor == "operator"
    assert request.destination == BOLT11
    assert request.amount.minor_units == 1000
    assert request.fee_limit.minor_units == 3  # 1000 sat * 3000 ppm
    assert request.correlation_id == f"tg:{CHAT}"

    assert "Betrag: 1000 sat" in reply
    assert "~7 sat" in reply and "Limit 3 sat" in reply
    assert "AWAITING_APPROVAL" in reply
    assert "/pay ok <hotp>" in reply
    assert flow.pending[CHAT].intent_id == "pi_test"
    assert flow.pending[CHAT].needs_hotp is True


async def test_tiny_invoice_uses_the_same_configured_fee_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pay, "bolt11_amount_sat", lambda _request: 10)
    service = FakeService(fee_limit_min_sat=3, fee_limit_max_sat=5)
    reply = await run(PayFlow(), service, BOLT11)
    assert service.requests[0].fee_limit.minor_units == 3
    assert "Limit 3 sat" in reply


async def test_preview_never_echoes_the_invoice() -> None:
    reply = await run(PayFlow(), FakeService(), BOLT11)
    assert BOLT11 not in reply


async def test_the_same_invoice_from_the_same_chat_is_the_same_idempotency_key() -> None:
    service = FakeService()
    await run(PayFlow(), service, BOLT11)
    await run(PayFlow(), service, BOLT11)
    assert service.keys[0] == service.keys[1]
    assert service.keys[0].startswith("tgpay_") and len(service.keys[0]) >= 16


async def test_an_auto_approved_intent_asks_for_ok_without_hotp() -> None:
    service = FakeService(first_status=PaymentStatus.AUTHORIZED)
    flow = PayFlow()
    reply = await run(flow, service, BOLT11)
    assert "/pay ok (" in reply or "mit /pay ok " in reply
    assert flow.pending[CHAT].needs_hotp is False


async def test_a_denied_intent_reports_the_reasons_and_keeps_nothing_pending() -> None:
    decision = SimpleNamespace(reasons=["daily cap exceeded"], rule_ids=["daily_cap"])
    service = FakeService(first_status=PaymentStatus.DENIED, decision=decision)
    flow = PayFlow()
    reply = await run(flow, service, BOLT11)
    assert reply.startswith("⛔") and "daily cap exceeded" in reply
    assert CHAT not in flow.pending
    assert ("simulate", "pi_test") not in service.calls


async def test_a_node_estimate_above_the_limit_is_flagged() -> None:
    service = FakeService(quote_fee=6, quote_source="node_estimate_route_fee")
    reply = await run(PayFlow(), service, BOLT11)
    assert "~6 sat (node_estimate_route_fee)" in reply
    assert "ueber dem Limit" in reply


async def test_a_node_probe_without_route_is_spelled_out() -> None:
    service = FakeService(quote_fee=3, quote_source="node_probe_no_route")
    reply = await run(PayFlow(), service, BOLT11)
    assert "keine Route" in reply
    assert "~3 sat" not in reply  # keine Scheinschaetzung


async def test_a_failed_probe_is_not_reported_as_no_route() -> None:
    service = FakeService(quote_fee=3, quote_source="node_probe_failed")
    reply = await run(PayFlow(), service, BOLT11)
    assert "keine Route" not in reply
    assert "Probe ohne Ergebnis" in reply


async def test_a_settings_estimate_carries_no_warning() -> None:
    reply = await run(PayFlow(), FakeService(quote_fee=3), BOLT11)
    assert "ueber dem Limit" not in reply and "keine Route" not in reply


async def test_a_failed_simulation_still_offers_the_preview() -> None:
    service = FakeService(simulate_error=PaymentServiceError("node unreachable"))
    flow = PayFlow()
    reply = await run(flow, service, BOLT11)
    assert "Vorschau fehlgeschlagen" in reply
    assert CHAT in flow.pending


async def test_a_service_refusal_is_a_reply_not_an_exception() -> None:
    class Refusing(FakeService):
        async def create_intent(self, request: PaymentRequest, key: str) -> IntentView:
            raise PaymentServiceError("journal locked")

    reply = await run(PayFlow(), Refusing(), BOLT11)
    assert reply.startswith("❌") and "journal locked" in reply


@pytest.mark.parametrize(
    "status",
    [
        PaymentStatus.FAILED_FINAL,
        PaymentStatus.FAILED_RETRYABLE,
        PaymentStatus.SETTLED,
        PaymentStatus.SETTLED_REVERSIBLE,
        PaymentStatus.REVERSED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        PaymentStatus.SUBMITTED,
        PaymentStatus.IN_FLIGHT,
        PaymentStatus.RECONCILIATION_REQUIRED,
    ],
)
async def test_a_replayed_intent_past_approval_offers_no_ok(status: PaymentStatus) -> None:
    # Live-Befund 24.09. (pi_d785a1c328f54a1e): dieselbe Rechnung nach
    # FAILED_FINAL erneut geschickt -> Vorschau bot "/pay ok" an, obwohl der
    # Service jeden zweiten Send als Replay abweist.
    service = FakeService(first_status=status, replayed=True)
    flow = PayFlow()
    reply = await run(flow, service, BOLT11)
    assert "/pay ok" not in reply
    assert status.value in reply and "pi_test" in reply
    assert CHAT not in flow.pending
    assert ("simulate", "pi_test") not in service.calls
    assert flow.last_intent[CHAT] == "pi_test"
    assert "Keine offene Vorschau" in await run(flow, service, "ok")
    assert ("execute", "pi_test") not in service.calls


async def test_a_replayed_intent_still_awaiting_approval_keeps_the_ok_offer() -> None:
    service = FakeService(replayed=True)
    flow = PayFlow()
    reply = await run(flow, service, BOLT11)
    assert "Replay" in reply and "/pay ok <hotp>" in reply
    assert CHAT in flow.pending


# --------------------------------------------------------------------------- #
# Freigabe + Send
# --------------------------------------------------------------------------- #


async def test_ok_without_a_preview_is_refused() -> None:
    service = FakeService()
    reply = await run(PayFlow(), service, "ok 123456")
    assert "Keine offene Vorschau" in reply
    assert service.calls == []


async def test_ok_without_hotp_while_awaiting_approval_does_not_send() -> None:
    service = FakeService()
    flow = PayFlow()
    await run(flow, service, BOLT11)
    reply = await run(flow, service, "ok")
    assert "/pay ok <hotp>" in reply
    assert ("execute", "pi_test") not in service.calls
    assert CHAT in flow.pending


async def test_ok_with_hotp_authorizes_then_executes_and_reports_settled() -> None:
    service = FakeService()
    flow = PayFlow()
    await run(flow, service, BOLT11)
    reply = await run(flow, service, "ok 123456")
    assert ("authorize", "123456") in service.calls
    assert service.calls[-1] == ("execute", "pi_test")
    assert reply.startswith("✅") and "1000 sat" in reply and "SETTLED" in reply
    assert CHAT not in flow.pending


async def test_a_rejected_hotp_keeps_the_preview_and_leaks_no_detail() -> None:
    service = FakeService(authorize_error=PaymentServiceError("hotp counter 41 mismatch"))
    flow = PayFlow()
    await run(flow, service, BOLT11)
    reply = await run(flow, service, "ok 000000")
    assert reply.startswith("❌")
    assert "41" not in reply
    assert ("execute", "pi_test") not in service.calls
    assert CHAT in flow.pending


async def test_shadow_mode_refusal_reaches_the_operator_verbatim() -> None:
    service = FakeService(
        first_status=PaymentStatus.AUTHORIZED,
        execute_error=PaymentServiceError("execute refused: payment mode is shadow"),
    )
    flow = PayFlow()
    await run(flow, service, BOLT11)
    reply = await run(flow, service, "ok")
    assert "payment mode is shadow" in reply
    assert CHAT not in flow.pending


@pytest.mark.parametrize(
    ("status", "marker"),
    [
        (PaymentStatus.FAILED_FINAL, "❌"),
        (PaymentStatus.RECONCILIATION_REQUIRED, "⚠️"),
        (PaymentStatus.IN_FLIGHT, "ℹ️"),
    ],
)
async def test_every_terminal_status_has_a_distinct_reply(
    status: PaymentStatus, marker: str
) -> None:
    service = FakeService(first_status=PaymentStatus.AUTHORIZED, execute_status=status)
    flow = PayFlow()
    await run(flow, service, BOLT11)
    reply = await run(flow, service, "ok")
    assert reply.startswith(marker) and status.value in reply


async def test_a_rail_exception_during_execute_is_reported_as_unknown() -> None:
    service = FakeService(first_status=PaymentStatus.AUTHORIZED, execute_error=RuntimeError("boom"))
    flow = PayFlow()
    await run(flow, service, BOLT11)
    reply = await run(flow, service, "ok")
    assert reply.startswith("⚠️") and "RuntimeError" in reply


async def test_a_stale_preview_expires_before_ok() -> None:
    service = FakeService()
    flow = PayFlow()
    await run(flow, service, BOLT11)
    reply = await run(flow, service, "ok 123456", now=NOW + PENDING_TTL + timedelta(seconds=1))
    assert "Keine offene Vorschau" in reply
    assert ("execute", "pi_test") not in service.calls


# --------------------------------------------------------------------------- #
# cancel / status
# --------------------------------------------------------------------------- #


async def test_cancel_drops_the_preview_without_sending() -> None:
    service = FakeService()
    flow = PayFlow()
    await run(flow, service, BOLT11)
    reply = await run(flow, service, "cancel")
    assert "verworfen" in reply and CHAT not in flow.pending
    assert ("execute", "pi_test") not in service.calls
    assert "Keine offene Vorschau" in await run(flow, service, "cancel")


async def test_status_reports_the_last_intent() -> None:
    service = FakeService()
    flow = PayFlow()
    assert "Kein /pay-Vorgang" in await run(flow, service, "status")
    await run(flow, service, BOLT11)
    reply = await run(flow, service, "status")
    assert "pi_test" in reply and "AWAITING_APPROVAL" in reply
