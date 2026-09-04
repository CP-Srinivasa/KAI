"""Der Control Plane (ADR 0018 §5/§9).

Die eine Kette: Intent -> Policy -> Autorisierung -> Rail -> Settlement. Was
hier geprueft wird, sind die Stellen, an denen sie reissen kann:

* Der ``submitted``-Record steht VOR dem Rail-Aufruf auf Platte. Ohne diesen
  Write-ahead ist ein Crash zwischen Send und Antwort ein Geldverlust ohne
  Spur.
* Ein zweites ``execute`` sendet NICHT erneut — es gibt den bestehenden
  Zustand zurueck.
* Nach einem Neustart ist ein Intent, dessen ``submitted`` ohne Antwort blieb,
  ``RECONCILIATION_REQUIRED`` — nicht ``FAILED``, nicht wiederholbar.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.payment_settings import PaymentSettings
from app.payments.enums import PaymentStatus, Verdict
from app.payments.intent_vault import INTENT_VAULT_FILENAME, IntentVault, IntentVaultError
from app.payments.journal import PaymentJournal
from app.payments.models import Money
from app.payments.policy import ActorLimits
from app.payments.rail import InvoiceRequest, RailError
from app.payments.rails.simulation import SimulationRail
from app.payments.service import PaymentRequest, PaymentService, PaymentServiceError

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


class FakeHotp:
    """Ein HOTP-Verifier, der genau einen Code kennt."""

    def __init__(self, good_code: str = "123456") -> None:
        self.good_code = good_code
        self.counter = 41
        self.calls: list[str] = []

    def verify(self, code: str) -> object:
        self.calls.append(code)
        if code != self.good_code:
            raise RuntimeError("HOTP verification failed")
        self.counter += 1

        class Result:
            counter_used = self.counter

        return Result()


def allowlisted(destination: str) -> str:
    import hashlib

    return hashlib.sha256(f"payee:{destination}".encode()).hexdigest()


def settings(destination: str = "sim:settle:alice", **overrides: object) -> PaymentSettings:
    base: dict[str, object] = {
        "mode": "simulation",
        "destination_allowlist": allowlisted(destination),
        "purposes_allowed": "self_test",
        "per_payment_max_sat": 5000,
        "daily_hard_cap_sat": 10_000,
        "approval_threshold_sat": 4000,
        "fee_limit_max_sat": 200,
    }
    base.update(overrides)
    return PaymentSettings(**base)  # type: ignore[arg-type]


def a_request(destination: str = "sim:settle:alice", **overrides: object) -> PaymentRequest:
    base: dict[str, object] = {
        "actor": "operator",
        "purpose": "self_test",
        "destination": destination,
        "amount": sat(1000),
        "fee_limit": sat(10),
        "correlation_id": "corr-1",
    }
    base.update(overrides)
    return PaymentRequest(**base)  # type: ignore[arg-type]


VAULT_KEY = b"v" * 32


def a_vault(tmp_path: Path, key: bytes = VAULT_KEY) -> IntentVault:
    return IntentVault(tmp_path / INTENT_VAULT_FILENAME, key=key)


def a_service(
    tmp_path: Path,
    *,
    destination: str = "sim:settle:alice",
    rail: object | None = None,
    hotp: object | None = None,
    vault_key: bytes = VAULT_KEY,
    **setting_overrides: object,
) -> PaymentService:
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    return PaymentService(
        journal=journal,
        rails={"simulation": rail or SimulationRail(now=NOW)},
        settings=settings(destination, **setting_overrides),
        clock=lambda: NOW,
        app_env="development",
        hotp_verifier=hotp,
        vault=a_vault(tmp_path, vault_key),
    )


def event_types(service: PaymentService, intent_id: str) -> list[str]:
    return [event.event_type for event in service.audit(intent_id)]


# --------------------------------------------------------------------------- #
# create_intent
# --------------------------------------------------------------------------- #


async def test_a_clean_intent_is_authorized(tmp_path: Path) -> None:
    service = a_service(tmp_path)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    assert view.status is PaymentStatus.AUTHORIZED
    assert view.decision is not None
    assert view.decision.verdict is Verdict.ALLOW
    assert event_types(service, view.intent_id) == ["intent_created", "policy_decided"]


async def test_a_denied_intent_is_denied_and_journalled(tmp_path: Path) -> None:
    service = a_service(tmp_path, per_payment_max_sat=100, daily_hard_cap_sat=100)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    assert view.status is PaymentStatus.DENIED
    assert view.decision is not None
    assert view.decision.rule_ids == ("amount_limits",)
    assert event_types(service, view.intent_id) == ["intent_created", "policy_decided"]


async def test_an_amount_above_the_threshold_awaits_approval(tmp_path: Path) -> None:
    service = a_service(tmp_path, approval_threshold_sat=500)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    assert view.status is PaymentStatus.AWAITING_APPROVAL


async def test_an_unknown_destination_is_denied_not_crashed(tmp_path: Path) -> None:
    """Ein Decode, der nicht gelingt, ist ein DENY — kein Stacktrace."""
    service = a_service(tmp_path)
    view = await service.create_intent(a_request(destination="   "), "idem-0123456789abcdef")
    assert view.status is PaymentStatus.DENIED
    assert view.decision is not None
    assert view.decision.rule_ids == ("destination_allowlist",)


async def test_a_repeated_idempotency_key_replays(tmp_path: Path) -> None:
    service = a_service(tmp_path)
    first = await service.create_intent(a_request(), "idem-0123456789abcdef")
    second = await service.create_intent(a_request(), "idem-0123456789abcdef")
    assert second.replayed is True
    assert second.intent_id == first.intent_id
    assert len(service.audit(first.intent_id)) == 2, "kein zweiter Satz Records"


async def test_an_agent_over_its_limit_is_denied(tmp_path: Path) -> None:
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    service = PaymentService(
        journal=journal,
        rails={"simulation": SimulationRail(now=NOW)},
        settings=settings(),
        clock=lambda: NOW,
        actor_limits={
            "agent:research": ActorLimits(
                actor="agent:research",
                max_amount_sat=100,
                daily_max_sat=100,
                purposes=frozenset({"self_test"}),
                rails=frozenset({"lightning"}),
            )
        },
    )
    view = await service.create_intent(a_request(actor="agent:research"), "idem-0123456789abcdef")
    assert view.status is PaymentStatus.DENIED
    assert view.decision is not None
    assert view.decision.rule_ids == ("actor_limits",)


# --------------------------------------------------------------------------- #
# simulate
# --------------------------------------------------------------------------- #


async def test_simulate_previews_without_sending(tmp_path: Path) -> None:
    service = a_service(tmp_path)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    preview = await service.simulate(view.intent_id)
    assert preview.quote is not None
    assert preview.decision is not None
    assert service.get(view.intent_id).status is PaymentStatus.AUTHORIZED
    assert "submitted" not in event_types(service, view.intent_id)


# --------------------------------------------------------------------------- #
# authorize
# --------------------------------------------------------------------------- #


async def test_a_valid_hotp_code_authorizes(tmp_path: Path) -> None:
    hotp = FakeHotp()
    service = a_service(tmp_path, approval_threshold_sat=500, hotp=hotp)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    assert view.status is PaymentStatus.AWAITING_APPROVAL

    authorized = service.authorize(view.intent_id, "123456")
    assert authorized.status is PaymentStatus.AUTHORIZED
    assert hotp.calls == ["123456"]
    assert "approval_granted" in event_types(service, view.intent_id)


async def test_a_wrong_hotp_code_does_not_authorize(tmp_path: Path) -> None:
    service = a_service(tmp_path, approval_threshold_sat=500, hotp=FakeHotp())
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    with pytest.raises(PaymentServiceError):
        service.authorize(view.intent_id, "999999")
    assert service.get(view.intent_id).status is PaymentStatus.AWAITING_APPROVAL
    assert "approval_denied" in event_types(service, view.intent_id)


async def test_authorize_without_a_verifier_is_refused(tmp_path: Path) -> None:
    """Ohne HOTP-Seed kann niemand freigeben — und niemand wird durchgewunken."""
    service = a_service(tmp_path, approval_threshold_sat=500, hotp=None)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    with pytest.raises(PaymentServiceError, match="no HOTP"):
        service.authorize(view.intent_id, "123456")


# --------------------------------------------------------------------------- #
# execute
# --------------------------------------------------------------------------- #


async def test_happy_path_settles(tmp_path: Path) -> None:
    service = a_service(tmp_path)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    executed = await service.execute(view.intent_id)
    assert executed.status is PaymentStatus.SETTLED
    assert event_types(service, view.intent_id) == [
        "intent_created",
        "policy_decided",
        "submitted",
        "rail_responded",
        "settled",
    ]


async def test_submitted_is_written_before_the_rail_is_called(tmp_path: Path) -> None:
    """Write-ahead (ADR §4): ohne diesen Record ist ein Crash spurlos."""
    seen: list[list[str]] = []
    rail = SimulationRail(now=NOW)
    original_pay = rail.pay

    async def spy(intent, attempt):  # type: ignore[no-untyped-def]
        rows = [
            json.loads(line)
            for line in journal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        seen.append([row["event_type"] for row in rows])
        return await original_pay(intent, attempt)

    rail.pay = spy  # type: ignore[method-assign]
    service = a_service(tmp_path, rail=rail)
    journal_path = tmp_path / "payment_journal.jsonl"
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    await service.execute(view.intent_id)
    assert seen and seen[0][-1] == "submitted"


async def test_an_unknown_outcome_becomes_reconciliation_required(tmp_path: Path) -> None:
    service = a_service(tmp_path, destination="sim:unknown:timeout")
    view = await service.create_intent(
        a_request(destination="sim:unknown:timeout"), "idem-0123456789abcdef"
    )
    executed = await service.execute(view.intent_id)
    assert executed.status is PaymentStatus.RECONCILIATION_REQUIRED
    assert executed.status is not PaymentStatus.FAILED_FINAL
    assert "reconciled" in event_types(service, view.intent_id)


async def test_a_proven_failure_becomes_failed_final(tmp_path: Path) -> None:
    service = a_service(tmp_path, destination="sim:fail:noroute")
    view = await service.create_intent(
        a_request(destination="sim:fail:noroute"), "idem-0123456789abcdef"
    )
    executed = await service.execute(view.intent_id)
    assert executed.status is PaymentStatus.FAILED_FINAL
    assert "failed" in event_types(service, view.intent_id)


async def test_an_in_flight_send_stays_in_flight(tmp_path: Path) -> None:
    service = a_service(tmp_path, destination="sim:inflight:slow")
    view = await service.create_intent(
        a_request(destination="sim:inflight:slow"), "idem-0123456789abcdef"
    )
    executed = await service.execute(view.intent_id)
    assert executed.status is PaymentStatus.IN_FLIGHT


async def test_a_second_execute_never_sends_again(tmp_path: Path) -> None:
    calls: list[str] = []
    rail = SimulationRail(now=NOW)
    original_pay = rail.pay

    async def counting(intent, attempt):  # type: ignore[no-untyped-def]
        calls.append(intent.intent_id)
        return await original_pay(intent, attempt)

    rail.pay = counting  # type: ignore[method-assign]
    service = a_service(tmp_path, rail=rail)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    first = await service.execute(view.intent_id)
    second = await service.execute(view.intent_id)
    assert len(calls) == 1, "der zweite execute darf den Rail nicht beruehren"
    assert second.replayed is True
    assert second.status is first.status


async def test_execute_is_refused_before_authorization(tmp_path: Path) -> None:
    service = a_service(tmp_path, approval_threshold_sat=500)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    with pytest.raises(PaymentServiceError, match="AUTHORIZED"):
        await service.execute(view.intent_id)


async def test_execute_of_a_denied_intent_is_refused(tmp_path: Path) -> None:
    service = a_service(tmp_path, per_payment_max_sat=100, daily_hard_cap_sat=100)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    with pytest.raises(PaymentServiceError):
        await service.execute(view.intent_id)


async def test_execute_of_an_unknown_intent_is_refused(tmp_path: Path) -> None:
    service = a_service(tmp_path)
    with pytest.raises(PaymentServiceError, match="unknown intent"):
        await service.execute("pi_does_not_exist")


# --------------------------------------------------------------------------- #
# Neustart nach einem Crash
# --------------------------------------------------------------------------- #


async def test_a_crash_between_submit_and_answer_ends_in_reconciliation(
    tmp_path: Path,
) -> None:
    """Der Prozess stirbt nach dem Write-ahead. Beim Start gilt: unbekannt."""

    class CrashingRail(SimulationRail):
        async def pay(self, intent, attempt):  # type: ignore[no-untyped-def]
            raise KeyboardInterrupt("process killed mid-send")

    service = a_service(tmp_path, rail=CrashingRail(now=NOW))
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    with pytest.raises(KeyboardInterrupt):
        await service.execute(view.intent_id)

    rows = [
        json.loads(line)
        for line in (tmp_path / "payment_journal.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["event_type"] for row in rows][-1] == "submitted"

    restarted = a_service(tmp_path)
    recovered = restarted.recover()
    assert view.intent_id in recovered
    assert restarted.get(view.intent_id).status is PaymentStatus.RECONCILIATION_REQUIRED
    with pytest.raises(PaymentServiceError):
        await restarted.execute(view.intent_id)


async def test_recover_leaves_settled_intents_alone(tmp_path: Path) -> None:
    service = a_service(tmp_path)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    await service.execute(view.intent_id)

    restarted = a_service(tmp_path)
    assert restarted.recover() == []
    assert restarted.get(view.intent_id).status is PaymentStatus.SETTLED


# --------------------------------------------------------------------------- #
# Mode-Gating
# --------------------------------------------------------------------------- #


async def test_simulation_always_uses_the_simulation_rail(tmp_path: Path) -> None:
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()

    class Exploding:
        name = "lightning"

        def capabilities(self):  # type: ignore[no-untyped-def]
            raise AssertionError("SIMULATION darf den Lightning-Rail nie beruehren")

    service = PaymentService(
        journal=journal,
        rails={"simulation": SimulationRail(now=NOW), "lightning": Exploding()},
        settings=settings(),
        clock=lambda: NOW,
    )
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    assert view.status is PaymentStatus.AUTHORIZED


async def test_shadow_mode_refuses_to_execute(tmp_path: Path) -> None:
    """SHADOW liest und rechnet — es sendet nicht."""
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    service = PaymentService(
        journal=journal,
        rails={"lightning": SimulationRail(now=NOW)},
        settings=settings(mode="shadow"),
        clock=lambda: NOW,
    )
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    with pytest.raises(PaymentServiceError, match="shadow"):
        await service.execute(view.intent_id)


async def test_a_missing_rail_for_the_mode_is_an_error(tmp_path: Path) -> None:
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    service = PaymentService(journal=journal, rails={}, settings=settings(), clock=lambda: NOW)
    with pytest.raises(PaymentServiceError, match="no rail"):
        await service.create_intent(a_request(), "idem-0123456789abcdef")


# --------------------------------------------------------------------------- #
# Empfangsseite
# --------------------------------------------------------------------------- #


async def test_create_invoice_and_settlement(tmp_path: Path) -> None:
    rail = SimulationRail(now=NOW)
    service = a_service(tmp_path, rail=rail)
    invoice = await service.create_invoice(InvoiceRequest(amount=sat(2500), purpose="self_test"))
    assert (await service.invoice_status(invoice.ref_hash)).settled is False

    rail.settle(invoice.ref_hash)
    assert (await service.invoice_status(invoice.ref_hash)).settled is True


async def test_the_receivable_record_carries_the_hash_of_the_sent_memo(tmp_path: Path) -> None:
    """Der ``memo_hash`` im Journal muss ein Urbild haben, das der Node sah.

    Vorher stand dort der Hash, den der Aufrufer mitgab (meist keiner), waehrend
    der Node ueberhaupt kein Memo bekam — zwei Aussagen ueber dieselbe Invoice,
    die nichts miteinander zu tun hatten.
    """
    import hashlib

    service = a_service(tmp_path)
    invoice = await service.create_invoice(
        InvoiceRequest(amount=sat(2500), purpose="self_test"), order_ref="order-memo"
    )
    expected = hashlib.sha256(b"kai-pay: self_test").hexdigest()
    assert invoice.memo_hash == expected

    raw = (tmp_path / "payment_journal.jsonl").read_text(encoding="utf-8")
    record = next(json.loads(line) for line in raw.splitlines() if expected in line)
    assert record["payload"]["memo_hash"] == expected
    assert "kai-pay" not in raw, "das Journal traegt den Hash, nicht den Text"


async def test_a_rail_error_while_creating_an_invoice_surfaces(tmp_path: Path) -> None:
    class BrokenRail(SimulationRail):
        async def create_invoice(self, request):  # type: ignore[no-untyped-def]
            raise RailError("node unavailable")

    service = a_service(tmp_path, rail=BrokenRail(now=NOW))
    with pytest.raises(RailError):
        await service.create_invoice(InvoiceRequest(amount=sat(100), purpose="self_test"))


# --------------------------------------------------------------------------- #
# Journal
# --------------------------------------------------------------------------- #


async def test_the_journal_never_carries_the_raw_destination(tmp_path: Path) -> None:
    service = a_service(tmp_path)
    await service.create_intent(a_request(), "idem-0123456789abcdef")
    text = (tmp_path / "payment_journal.jsonl").read_text(encoding="utf-8")
    assert "sim:settle:alice" not in text
    assert "idem-0123456789abcdef" not in text


async def test_the_chain_stays_valid_across_the_whole_flow(tmp_path: Path) -> None:
    service = a_service(tmp_path)
    view = await service.create_intent(a_request(), "idem-0123456789abcdef")
    await service.simulate(view.intent_id)
    await service.execute(view.intent_id)
    assert PaymentJournal(tmp_path / "payment_journal.jsonl").verify_chain().ok


# --------------------------------------------------------------------------- #
# Neustart (Befund LIVE-Fenster 2026-09-04)
# --------------------------------------------------------------------------- #


async def test_an_awaiting_intent_is_still_executable_after_a_restart(tmp_path: Path) -> None:
    """Der eigentliche Befund: ein freigabebereiter Intent ueberlebt keinen Neustart.

    Vorher hielt nur der Prozessspeicher die Ziel-BOLT11; das Journal traegt sie
    aus gutem Grund nur als Hash. Nach jedem Neustart von kai-server antwortete
    ``execute`` mit "unknown intent", und der Operator legte den Vorgang mitten
    im scharfen Fenster neu an.
    """
    first = a_service(tmp_path, hotp=FakeHotp(), approval_threshold_sat=1)
    view = await first.create_intent(a_request(), "idem-restart-000001")
    assert view.status is PaymentStatus.AWAITING_APPROVAL

    # Der Neustart: ein NEUER Dienst auf demselben Journal und demselben Vault.
    second = a_service(tmp_path, hotp=FakeHotp(), approval_threshold_sat=1)
    assert second.recover() == []
    assert second.get(view.intent_id).status is PaymentStatus.AWAITING_APPROVAL

    second.authorize(view.intent_id, "123456")
    result = await second.execute(view.intent_id)
    assert result.status is PaymentStatus.SETTLED
    assert "settled" in event_types(second, view.intent_id)


async def test_an_authorized_intent_is_still_executable_after_a_restart(tmp_path: Path) -> None:
    first = a_service(tmp_path, approval_threshold_sat=5000)
    view = await first.create_intent(a_request(), "idem-restart-000002")
    assert view.status is PaymentStatus.AUTHORIZED

    second = a_service(tmp_path, approval_threshold_sat=5000)
    second.recover()
    assert (await second.execute(view.intent_id)).status is PaymentStatus.SETTLED


async def test_the_rail_dedup_key_is_unchanged_by_a_restart(tmp_path: Path) -> None:
    """Ohne die gebundene Destination waere ein Vorgang nach dem Neustart unter
    einem ZWEITEN Rail-Schluessel unterwegs — und genau daran haengt die
    Rail-Dedup, die einen zweiten Send verhindert."""
    rail = SimulationRail(now=NOW)
    first = a_service(tmp_path, rail=rail, approval_threshold_sat=5000)
    view = await first.create_intent(a_request(), "idem-restart-000003")

    second = a_service(tmp_path, rail=rail, approval_threshold_sat=5000)
    second.recover()
    await second.execute(view.intent_id)

    submitted = next(
        event for event in second.audit(view.intent_id) if event.event_type == "submitted"
    )
    expected = (await rail.decode("sim:settle:alice")).rail_dedup_key
    assert submitted.payload["rail_dedup_key"] == expected


async def test_the_vault_on_disk_never_shows_the_destination(tmp_path: Path) -> None:
    service = a_service(tmp_path, destination="sim:settle:secret-payee")
    await service.create_intent(
        a_request(destination="sim:settle:secret-payee"), "idem-restart-000004"
    )
    raw = (tmp_path / INTENT_VAULT_FILENAME).read_bytes()
    assert b"secret-payee" not in raw
    assert b"idem-restart" not in raw


async def test_a_denied_intent_leaves_nothing_in_the_vault(tmp_path: Path) -> None:
    """Datensparsamkeit: ein abgelehnter Vorgang wird nie gesendet, also braucht
    niemand sein Ziel — auch nicht verschluesselt."""
    service = a_service(tmp_path, destination="sim:settle:alice")
    view = await service.create_intent(
        a_request(destination="sim:settle:mallory"), "idem-restart-000005"
    )
    assert view.status is PaymentStatus.DENIED
    assert not (tmp_path / INTENT_VAULT_FILENAME).exists()


async def test_a_wrong_vault_key_after_a_restart_is_fail_closed(tmp_path: Path) -> None:
    """Ein leeres Ergebnis saehe aus wie "keine offenen Vorgaenge" — und der
    Operator wuerde die Intents neu anlegen, statt den Schluessel zu suchen."""
    first = a_service(tmp_path, approval_threshold_sat=5000)
    await first.create_intent(a_request(), "idem-restart-000006")

    second = a_service(tmp_path, approval_threshold_sat=5000, vault_key=b"w" * 32)
    with pytest.raises(IntentVaultError, match="cannot be opened"):
        second.recover()


async def test_a_submitted_intent_never_comes_back_through_the_vault(tmp_path: Path) -> None:
    """Die Grenze des Vaults: er traegt Material fuer Vorgaenge VOR dem Send.

    Ein Vorgang, dessen ``submitted`` ohne Antwort blieb, geht den Weg ueber die
    Reconciliation und keinen anderen. Ihn aus dem Vault zurueckzuholen hiesse,
    einen zweiten Send auf einer Speicherannahme zu erlauben — der 25k-Spend vom
    07-02.
    """
    first = a_service(tmp_path, destination="sim:unknown:alice", approval_threshold_sat=5000)
    view = await first.create_intent(
        a_request(destination="sim:unknown:alice"), "idem-restart-000007"
    )
    await first.execute(view.intent_id)

    second = a_service(tmp_path, destination="sim:unknown:alice", approval_threshold_sat=5000)
    # Nichts zu klaeren: der erste Dienst hat die ausbleibende Aussage schon
    # journalliert. Der Vorgang steht in der Klaerung, und dort bleibt er.
    assert second.recover() == []
    assert second.get(view.intent_id).status is PaymentStatus.RECONCILIATION_REQUIRED
    with pytest.raises(PaymentServiceError, match="unknown intent"):
        await second.execute(view.intent_id)


async def test_a_service_without_a_vault_keeps_the_old_behaviour(tmp_path: Path) -> None:
    """Ohne Vault bleibt alles wie vorher — kein stiller Zwang, kein Absturz."""
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    service = PaymentService(
        journal=journal,
        rails={"simulation": SimulationRail(now=NOW)},
        settings=settings("sim:settle:alice", approval_threshold_sat=5000),
        clock=lambda: NOW,
    )
    view = await service.create_intent(a_request(), "idem-restart-000008")
    assert view.status is PaymentStatus.AUTHORIZED
    assert not (tmp_path / INTENT_VAULT_FILENAME).exists()
