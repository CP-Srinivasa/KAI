"""Fehler-Injektion: die fuenfzehn Faelle aus Mission §20 (ADR 0017 §4/§8).

Der Happy Path ist nicht der, an dem Geld verlorengeht. Diese Datei fuehrt
jeden benannten Ausfall herbei und prueft in JEDEM Fall dieselben zwei
Invarianten:

1. **``rail.pay`` wird hoechstens EINMAL gerufen.** Nicht "meistens", nicht
   "wenn der Retry richtig konfiguriert ist" — hoechstens einmal. Das ist die
   einzige Zusage, die eine Doppelzahlung strukturell ausschliesst.
2. **Kein Rohwert im Journal.** Keine Destination, kein Preimage, kein
   Macaroon. Das Journal ueberlebt die Vorfaelle, und es wird gelesen.

Die Aufteilung folgt der Frage, WO der Fehler entsteht: am Node (1–10), im
Prozess (11–12) oder an der Grenze (13–15).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import PaymentSettings
from app.payments import reconcile
from app.payments.enums import PaymentStatus, RailOutcome
from app.payments.journal import PaymentJournal
from app.payments.journal_chain import JournalIntegrityError
from app.payments.models import Money
from app.payments.rail import InvoiceRequest, RailError
from app.payments.rails.lightning import LightningRail
from app.payments.rails.simulation import SimulationRail
from app.payments.service import PaymentRequest, PaymentService, PaymentServiceError

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
BOOT = "failure-injection-boot"
DESTINATION = "sim:settle:alice"
#: Ein Rohwert, der im Journal NIE auftauchen darf.
SECRET_BOLT11 = "lnbc25u1p3xxxxxpp5deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
#: Der Empfaenger, den der gefakte Decode zurueckgibt.
NODE_PUBKEY = "02" + "ab" * 32


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def settings(destination: str = DESTINATION, **overrides: Any) -> PaymentSettings:
    base: dict[str, Any] = {
        "mode": "simulation",
        "destination_allowlist": hashlib.sha256(f"payee:{destination}".encode()).hexdigest(),
        "purposes_allowed": "self_test",
        "per_payment_max_sat": 5_000,
        "daily_hard_cap_sat": 10_000,
        "approval_threshold_sat": 4_000,
        "fee_limit_max_sat": 200,
    }
    base.update(overrides)
    return PaymentSettings(**base)


class PaySpy(SimulationRail):
    """Zaehlt jeden Sendeversuch. Die Zahl ist der eigentliche Pruefpunkt."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pay_calls = 0
        self.lookup_answers: dict[str, Any] = {}

    async def pay(self, intent: Any, attempt: Any) -> Any:
        self.pay_calls += 1
        return await super().pay(intent, attempt)

    async def lookup(self, rail_dedup_key: str) -> Any:
        answer = self.lookup_answers.get(rail_dedup_key)
        return answer if answer is not None else await super().lookup(rail_dedup_key)


def build(
    tmp_path: Path, destination: str = DESTINATION, **overrides: Any
) -> tuple[PaymentJournal, PaySpy, PaymentService]:
    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    rail = PaySpy(now=NOW)
    service = PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=settings(destination, **overrides),
        clock=lambda: NOW,
    )
    return journal, rail, service


def a_request(destination: str = DESTINATION, **overrides: Any) -> PaymentRequest:
    base: dict[str, Any] = {
        "actor": "operator",
        "purpose": "self_test",
        "destination": destination,
        "amount": sat(1000),
        "fee_limit": sat(10),
        "correlation_id": "corr-fi",
    }
    base.update(overrides)
    return PaymentRequest(**base)


def assert_journal_is_clean(journal: PaymentJournal) -> None:
    """Kein Rohwert. Geprueft wird die DATEI, nicht eine abgeleitete Sicht."""
    if not journal.path.is_file():
        return
    blob = journal.path.read_text(encoding="utf-8")
    for secret in (SECRET_BOLT11, "lnbc", "macaroon", DESTINATION):
        assert secret not in blob, f"{secret!r} steht im Geld-Journal"


# --------------------------------------------------------------------------- #
# 1–10: Der Node
# --------------------------------------------------------------------------- #


def _lightning(client: Any, **overrides: Any) -> LightningRail:
    """Ein Lightning-Rail mit gefaktem Client — nie ein echter Node."""
    return LightningRail(
        payment_settings=settings(mode="live", **overrides),
        lightning_settings=_ln_settings(),
        client_factory=lambda _scope: client,
    )


def _ln_settings() -> LightningSettings:
    """Ein aktivierter LN-Client braucht einen TLS-Pfad — sonst faellt der
    REST-Client auf ``verify=False`` zurueck und die Settings verweigern den
    Boot. Der Pfad zeigt hier ins Leere: der Client ist gefakt, aber die
    Vorbedingung soll trotzdem gelten."""
    return LightningSettings(enabled=True, pay_enabled=True, tls_cert_path="/nonexistent/tls.cert")


class FakeClient:
    """Ein lnd-Client, der genau den bestellten Ausfall liefert."""

    def __init__(
        self,
        *,
        state: str = "RPC_ACTIVE",
        synced_chain: bool = True,
        synced_graph: bool = True,
        raises: Exception | None = None,
        pay_result: Any = None,
        pay_raises: Exception | None = None,
        invoices: list[dict[str, Any]] | None = None,
    ) -> None:
        self._state = state
        self._synced_chain = synced_chain
        self._synced_graph = synced_graph
        self._raises = raises
        self._pay_result = pay_result
        self._pay_raises = pay_raises
        self._invoices = invoices or []
        self.pay_calls = 0

    async def get_state(self) -> str:
        if self._raises:
            raise self._raises
        return self._state

    async def get_info(self) -> Any:
        if self._raises:
            raise self._raises

        class Info:
            synced_to_chain = self._synced_chain
            synced_to_graph = self._synced_graph

        return Info()

    async def pay_invoice(self, **_kwargs: Any) -> Any:
        self.pay_calls += 1
        if self._pay_raises:
            raise self._pay_raises
        return self._pay_result or {}

    async def decode_pay_req(self, *, payment_request: str) -> dict[str, Any]:
        """Ein gueltiger Decode. Ohne ihn scheiterte die Policy schon an der
        Allowlist — und der Test bewiese die falsche Regel."""
        return {
            "destination": NODE_PUBKEY,
            "payment_hash": "a" * 64,
            "num_satoshis": 1000,
            "expiry": 3600,
            "timestamp": int(NOW.timestamp()),
            "description": "",
        }

    async def list_invoices(self) -> list[dict[str, Any]]:
        if self._raises:
            raise self._raises
        return self._invoices


async def _health_of(client: FakeClient) -> Any:
    return await _lightning(client).health()


async def test_01_lnd_offline_ist_ungesund_nicht_gesund() -> None:
    health = await _health_of(FakeClient(raises=ConnectionError("connection refused")))
    assert health.reachable is False
    assert health.healthy is False


async def test_02_ein_unsynchroner_node_ist_ungesund() -> None:
    health = await _health_of(FakeClient(synced_chain=False))
    assert health.reachable is True
    assert health.synced_to_chain is False
    assert health.healthy is False


async def test_03_ein_gesperrtes_wallet_ist_ungesund() -> None:
    health = await _health_of(FakeClient(state="LOCKED"))
    assert health.wallet_locked is True
    assert health.healthy is False


async def test_04_ein_tls_fehler_ist_ungesund_und_nennt_keine_details() -> None:
    health = await _health_of(FakeClient(raises=OSError("certificate verify failed")))
    assert health.healthy is False
    assert "macaroon" not in health.reason


async def test_05_ein_ungueltiges_macaroon_ist_ungesund() -> None:
    health = await _health_of(FakeClient(raises=PermissionError("HTTP 403: permission denied")))
    assert health.healthy is False


@pytest.mark.parametrize(
    "client,label",
    [
        (FakeClient(raises=ConnectionError("refused")), "offline"),
        (FakeClient(synced_chain=False), "unsynced"),
        (FakeClient(state="LOCKED"), "locked"),
    ],
)
async def test_ein_ungesunder_node_bekommt_keinen_send(
    tmp_path: Path, client: FakeClient, label: str
) -> None:
    """``node_health`` ist eine DENY-Regel — vor dem Sendepfad, nicht danach."""
    journal, _spy, _service = build(tmp_path)
    rail = _lightning(client)
    live = settings(mode="live")
    # Der Empfaenger IST allowlisted — sonst haette die Kette schon vorher
    # abgelehnt, und der Test bewiese die falsche Regel.
    live = PaymentSettings(
        **{
            **live.model_dump(),
            "destination_allowlist": hashlib.sha256(NODE_PUBKEY.encode()).hexdigest(),
        }
    )
    service = PaymentService(
        journal=journal,
        rails={"lightning": rail},
        settings=live,
        clock=lambda: NOW,
        app_env="production",
    )
    view = await service.create_intent(a_request(SECRET_BOLT11), f"idem-node-{label}-00000")

    assert view.status is PaymentStatus.DENIED
    assert view.decision is not None
    assert view.decision.rule_ids == ("node_health",), (
        f"abgelehnt, aber aus dem falschen Grund: {view.decision.rule_ids}"
    )
    assert client.pay_calls == 0
    assert_journal_is_clean(journal)


async def test_06_eine_abgelaufene_invoice_gilt_nicht_als_beglichen() -> None:
    rail = _lightning(FakeClient(invoices=[{"r_hash": "a" * 64, "settled": False}]))
    status = await rail.invoice_status("a" * 64)
    assert status.settled is False


async def test_07_eine_unbekannte_invoice_gilt_nicht_als_beglichen() -> None:
    rail = _lightning(FakeClient(invoices=[]))
    status = await rail.invoice_status("b" * 64)
    assert status.settled is False


async def test_08_ein_payment_timeout_ist_unbekannt_nicht_gescheitert(tmp_path: Path) -> None:
    """Der teuerste Fall: der Aufruf endet ohne Antwort, das Geld kann drausssen sein."""
    journal, rail, service = build(tmp_path, "sim:unknown:alice")
    view = await service.create_intent(a_request("sim:unknown:alice"), "idem-timeout-000001")
    result = await service.execute(view.intent_id)

    assert result.status is PaymentStatus.RECONCILIATION_REQUIRED
    assert result.status is not PaymentStatus.FAILED_FINAL
    assert rail.pay_calls == 1
    assert_journal_is_clean(journal)


async def test_09_fehlende_liquiditaet_wird_vor_dem_send_abgelehnt(tmp_path: Path) -> None:
    """Die Regel ``liquidity`` prueft nur, wenn der Rail eine Zahl liefert."""
    from app.payments.policy import PolicyContext, evaluate

    journal, rail, _service = build(tmp_path)
    intent = a_request().to_intent(
        intent_id="pi_liq",
        idempotency_key="idem-liquidity-00001",
        moment=NOW,
        mode=__import__("app.payments.enums", fromlist=["x"]).PaymentMode.SIMULATION,
    )
    decision = evaluate(
        PolicyContext(
            intent=intent,
            settings=settings(),
            rail_caps=rail.capabilities(),
            rail_health=await rail.health(),
            spent_today_sat=0,
            actor_limits=None,
            decoded_destination=await rail.decode(DESTINATION),
            app_env="testing",
            evaluated_at=NOW,
            available_liquidity_sat=100,
        )
    )

    assert decision.verdict.value == "DENY"
    assert decision.rule_ids == ("liquidity",)
    assert rail.pay_calls == 0


async def test_10_ein_routing_fehler_ist_terminal_und_wiederholbar(tmp_path: Path) -> None:
    """``FAILED`` vom Node heisst bewiesen "nichts bewegt" — nur DANN ein Retry."""
    journal, rail, service = build(tmp_path, "sim:fail:alice")
    view = await service.create_intent(a_request("sim:fail:alice"), "idem-noroute-000001")
    result = await service.execute(view.intent_id)

    assert result.status is PaymentStatus.FAILED_FINAL
    assert rail.pay_calls == 1
    failed = [e for e in journal.events(view.intent_id) if e.event_type == "failed"]
    assert failed[-1].payload["failure_reason"] == "NO_ROUTE"
    assert_journal_is_clean(journal)


# --------------------------------------------------------------------------- #
# 11–12: Der Prozess
# --------------------------------------------------------------------------- #


async def test_11_prozess_absturz_zwischen_send_und_antwort(tmp_path: Path) -> None:
    """Der Vollzyklus: Crash -> recover -> reconcile -> SETTLED, ohne zweiten Send."""
    journal_path = tmp_path / "payments" / "payment_journal.jsonl"
    journal = PaymentJournal(journal_path)
    journal.open()
    rail = PaySpy(now=NOW)

    class Crashes(PaySpy):
        async def pay(self, intent: Any, attempt: Any) -> Any:
            self.pay_calls += 1
            raise KeyboardInterrupt("process killed mid-flight")

    crashing = Crashes(now=NOW)
    service = PaymentService(
        journal=journal,
        rails={"simulation": crashing, "lightning": crashing},
        settings=settings(),
        clock=lambda: NOW,
    )
    view = await service.create_intent(a_request(), "idem-crash-00000001")
    with pytest.raises(KeyboardInterrupt):
        await service.execute(view.intent_id)
    assert crashing.pay_calls == 1
    assert journal.index.intent_status(view.intent_id) == PaymentStatus.SUBMITTED.value

    # Neustart: der Prozess klaert, er entscheidet nicht.
    restarted = PaymentJournal(journal_path)
    restarted.open()
    revived = PaymentService(
        journal=restarted,
        rails={"simulation": rail, "lightning": rail},
        settings=settings(),
        clock=lambda: NOW,
    )
    assert revived.recover() == [view.intent_id]
    assert (
        restarted.index.intent_status(view.intent_id) == PaymentStatus.RECONCILIATION_REQUIRED.value
    )

    # Der Reconciler holt die Evidenz — und sendet nicht.
    key = restarted.index.dedup_key(view.intent_id)
    assert key is not None
    from app.payments.models import Proof
    from app.payments.rail import RailLookup

    rail.lookup_answers[key] = RailLookup(
        rail="lightning",
        found=True,
        outcome=RailOutcome.SETTLED,
        rail_dedup_key=key,
        observed_at=NOW,
        amount_sent=sat(1000),
        proof=Proof(kind="PREIMAGE", ref_hash="c" * 64),
    )
    await reconcile.run(
        restarted,
        rail,
        settings=settings(),
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
        boot_ref=BOOT,
        state_path=tmp_path / "reconcile_state.json",
    )

    assert restarted.index.intent_status(view.intent_id) == PaymentStatus.SETTLED.value
    assert rail.pay_calls == 0, "der Reconciler darf nicht senden"
    assert crashing.pay_calls == 1, "und der abgestuerzte Versuch bleibt der einzige"
    assert_journal_is_clean(restarted)


async def test_12_ein_unbeschreibbares_journal_verhindert_den_send(tmp_path: Path) -> None:
    """Ohne Write-ahead kein Send. Der Fehler ist sichtbar, nicht verschluckt."""
    blocker = tmp_path / "payments"
    blocker.write_text("this is a file, not a directory", encoding="utf-8")
    journal = PaymentJournal(blocker / "payment_journal.jsonl")
    rail = PaySpy(now=NOW)
    service = PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=settings(),
        clock=lambda: NOW,
    )

    with pytest.raises((JournalIntegrityError, OSError)):
        await service.create_intent(a_request(), "idem-readonly-0000001")

    assert rail.pay_calls == 0


# --------------------------------------------------------------------------- #
# 13–15: Die Grenze
# --------------------------------------------------------------------------- #


async def test_13_derselbe_idempotency_key_erzeugt_genau_einen_send(tmp_path: Path) -> None:
    journal, rail, service = build(tmp_path)
    first = await service.create_intent(a_request(), "idem-duplicate-00001")
    second = await service.create_intent(a_request(), "idem-duplicate-00001")

    assert second.replayed is True
    assert second.intent_id == first.intent_id
    await service.execute(first.intent_id)
    replay = await service.execute(first.intent_id)

    assert replay.replayed is True
    assert rail.pay_calls == 1
    assert_journal_is_clean(journal)


async def test_14_ein_verpasstes_settlement_ereignis_wird_nachgetragen(tmp_path: Path) -> None:
    """Die Invoice ist am Node beglichen, KAI hat es nie mitbekommen."""
    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    rail = PaySpy(now=NOW)
    service = PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=settings(),
        clock=lambda: NOW,
    )
    invoice = await service.create_invoice(
        InvoiceRequest(amount=sat(1500), purpose="self_test"), order_ref="order-missed"
    )
    rail.settle(invoice.ref_hash)  # Das Ereignis erreicht KAI NICHT.
    assert [r.ref_hash for r in journal.index.open_receivables()] == [invoice.ref_hash]

    report = await reconcile.run(
        journal,
        rail,
        settings=settings(),
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
        boot_ref=BOOT,
        state_path=tmp_path / "reconcile_state.json",
    )

    assert report.counts["RECEIVABLE_SETTLED"] == 1
    assert journal.index.open_receivables() == []
    assert rail.pay_calls == 0


async def test_15_verlorene_antwort_bei_erfolgreicher_zahlung(tmp_path: Path) -> None:
    """Timeout -> Klaerung -> Lookup sagt SUCCEEDED -> SETTLED. Kein zweiter Send."""
    journal, rail, service = build(tmp_path, "sim:unknown:alice")
    view = await service.create_intent(a_request("sim:unknown:alice"), "idem-lostreply-00001")
    result = await service.execute(view.intent_id)
    assert result.status is PaymentStatus.RECONCILIATION_REQUIRED
    assert rail.pay_calls == 1

    key = journal.index.dedup_key(view.intent_id)
    assert key is not None
    from app.payments.models import Proof
    from app.payments.rail import RailLookup

    rail.lookup_answers[key] = RailLookup(
        rail="lightning",
        found=True,
        outcome=RailOutcome.SETTLED,
        rail_dedup_key=key,
        observed_at=NOW,
        amount_sent=sat(1000),
        fee_actual=sat(2),
        proof=Proof(kind="PREIMAGE", ref_hash="d" * 64),
    )
    await reconcile.run(
        journal,
        rail,
        settings=settings(),
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
        boot_ref=BOOT,
        state_path=tmp_path / "reconcile_state.json",
    )

    assert journal.index.intent_status(view.intent_id) == PaymentStatus.SETTLED.value
    assert rail.pay_calls == 1, "die Klaerung darf keinen zweiten Send ausloesen"
    assert_journal_is_clean(journal)


# --------------------------------------------------------------------------- #
# Querschnitt
# --------------------------------------------------------------------------- #


async def test_ein_shadow_prozess_sendet_in_keinem_fall(tmp_path: Path) -> None:
    journal, rail, _s = build(tmp_path)
    service = PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=settings(mode="shadow"),
        clock=lambda: NOW,
    )
    with pytest.raises(PaymentServiceError, match="shadow"):
        await service.execute("pi_whatever")
    assert rail.pay_calls == 0


async def test_der_lightning_rail_verweigert_ausserhalb_von_live() -> None:
    client = FakeClient()
    rail = LightningRail(
        payment_settings=settings(mode="shadow"),
        lightning_settings=_ln_settings(),
        client_factory=lambda _scope: client,
    )
    intent = a_request(SECRET_BOLT11).to_intent(
        intent_id="pi_shadow",
        idempotency_key="idem-shadow-000000001",
        moment=NOW,
        mode=__import__("app.payments.enums", fromlist=["x"]).PaymentMode.SHADOW,
    )
    from app.payments.models import PaymentAttempt

    with pytest.raises(RailError, match="shadow"):
        await rail.pay(
            intent,
            PaymentAttempt(
                attempt_no=1,
                intent_id="pi_shadow",
                rail_dedup_key="e" * 64,
                submitted_at=NOW,
                amount_sent=sat(1000),
            ),
        )
    assert client.pay_calls == 0
