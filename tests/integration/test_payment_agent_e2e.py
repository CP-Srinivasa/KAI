"""Ein Agent zahlt — von Ende zu Ende (ADR 0017 §1 Agent-Flow, §6 ``actor_limits``).

Der Agent bekommt **kein Macaroon**. Er erzeugt einen Intent, die Regelkette
entscheidet, und er bekommt Status und Beleg zurueck. Das ist der ganze
Unterschied zum Bestand, in dem jeder Aufrufer mit Node-Zugriff auch die
Rechte des Node hatte.

Die Limits kommen aus ``config/payment_agent_limits.json`` — der ECHTEN Datei,
nicht aus einem Fixture-Dict. Eine Tabelle, die nur im Test existiert, beweist
nichts ueber die Anlage; hier soll gerade das Zusammenspiel aus Datei, Loader
und Regel geprueft sein.

Vier Wege durch die Kette: unter dem Limit (ALLOW), ueber ``max_amount``
(DENY), ueber der Freigabeschwelle (HOTP), ueber dem Tagesbudget (DENY). Der
letzte ist der wichtigste — er ist im Bestand ein ``needs_confirm`` gewesen,
also eine Rueckfrage statt einer Grenze.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.payment_settings import PaymentSettings
from app.payments.agent_limits import load_actor_limits
from app.payments.enums import PaymentStatus
from app.payments.journal import PaymentJournal
from app.payments.models import Money
from app.payments.rails.simulation import SimulationRail
from app.payments.service import PaymentRequest, PaymentService, PaymentServiceError

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
AGENT = "agent:research"
DESTINATION = "sim:settle:data-provider"
REPO_ROOT = Path(__file__).resolve().parents[2]
LIMITS_PATH = REPO_ROOT / "config" / "payment_agent_limits.json"


class FakeHotp:
    """Ein Verifier, der genau einen Code kennt — kein Seed, kein Journal."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def verify(self, code: str) -> object:
        self.calls.append(code)
        if code != "654321":
            raise RuntimeError("HOTP verification failed")

        class Result:
            counter_used = 11

        return Result()


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def _settings(**overrides: Any) -> PaymentSettings:
    base: dict[str, Any] = {
        "mode": "simulation",
        "destination_allowlist": hashlib.sha256(f"payee:{DESTINATION}".encode()).hexdigest(),
        "purposes_allowed": "data_subscription,api_credit",
        "per_payment_max_sat": 5_000,
        "daily_hard_cap_sat": 10_000,
        # Bewusst hoch: die STRENGERE Schwelle soll aus der Agenten-Tabelle
        # kommen (250 sat), nicht aus der globalen Konfiguration.
        "approval_threshold_sat": 5_000,
        "fee_limit_max_sat": 200,
    }
    base.update(overrides)
    return PaymentSettings(**base)


@pytest.fixture
def agent_service(tmp_path: Path) -> tuple[PaymentService, SimulationRail, PaymentJournal]:
    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    rail = SimulationRail(now=NOW)
    limits = load_actor_limits(LIMITS_PATH)
    assert AGENT in limits, "die echte Agenten-Tabelle kennt diesen Agenten nicht mehr"
    service = PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=_settings(),
        clock=lambda: NOW,
        hotp_verifier=FakeHotp(),
        actor_limits=limits,
    )
    return service, rail, journal


def _request(amount: int, **overrides: Any) -> PaymentRequest:
    base: dict[str, Any] = {
        "actor": AGENT,
        "purpose": "data_subscription",
        "destination": DESTINATION,
        "amount": sat(amount),
        "fee_limit": sat(5),
        "correlation_id": "agent-run-1",
    }
    base.update(overrides)
    return PaymentRequest(**base)


# --------------------------------------------------------------------------- #
# Der glatte Weg
# --------------------------------------------------------------------------- #


async def test_unter_dem_limit_zahlt_der_agent_und_bekommt_einen_beleg(
    agent_service: tuple[PaymentService, SimulationRail, PaymentJournal],
) -> None:
    service, _rail, journal = agent_service
    view = await service.create_intent(_request(200), "agent-idem-00000000001")
    assert view.status is PaymentStatus.AUTHORIZED
    assert view.decision is not None
    assert view.decision.verdict.value == "ALLOW"

    result = await service.execute(view.intent_id)

    assert result.status is PaymentStatus.SETTLED
    settled = [e for e in journal.events(view.intent_id) if e.event_type == "settled"]
    assert settled[-1].payload["amount_settled_minor_units"] == 200
    assert len(settled[-1].payload["proof_hash"]) == 64
    # Der Agent bekommt Status und Beleg — nicht die Destination.
    assert DESTINATION not in str(result)


# --------------------------------------------------------------------------- #
# Die drei Grenzen
# --------------------------------------------------------------------------- #


async def test_ueber_max_amount_wird_abgelehnt_mit_regel(
    agent_service: tuple[PaymentService, SimulationRail, PaymentJournal],
) -> None:
    service, rail, _journal = agent_service
    calls: list[str] = []
    original = rail.pay

    async def spy(intent: Any, attempt: Any) -> Any:  # pragma: no cover - darf nie
        calls.append("pay")
        return await original(intent, attempt)

    rail.pay = spy  # type: ignore[method-assign]

    view = await service.create_intent(_request(4_000), "agent-idem-00000000002")

    assert view.status is PaymentStatus.DENIED
    assert view.decision is not None
    assert view.decision.rule_ids == ("actor_limits",)
    assert "agent per-payment limit exceeded" in view.decision.reasons[0]
    with pytest.raises(PaymentServiceError):
        await service.execute(view.intent_id)
    assert calls == []


async def test_ueber_der_freigabeschwelle_wartet_der_agent_auf_einen_menschen(
    agent_service: tuple[PaymentService, SimulationRail, PaymentJournal],
) -> None:
    """250 sat aus der Agenten-Tabelle schlagen die globalen 5.000."""
    service, _rail, journal = agent_service
    view = await service.create_intent(_request(300), "agent-idem-00000000003")

    assert view.status is PaymentStatus.AWAITING_APPROVAL
    assert view.decision is not None
    assert view.decision.rule_ids == ("approval_threshold",)

    with pytest.raises(PaymentServiceError, match="expected AUTHORIZED"):
        await service.execute(view.intent_id)
    with pytest.raises(PaymentServiceError, match="approval refused"):
        service.authorize(view.intent_id, "000000")

    service.authorize(view.intent_id, "654321")
    result = await service.execute(view.intent_id)

    assert result.status is PaymentStatus.SETTLED
    types = [e.event_type for e in journal.events(view.intent_id)]
    assert "approval_denied" in types
    assert "approval_granted" in types


async def test_der_tages_cap_ist_eine_grenze_keine_rueckfrage(
    agent_service: tuple[PaymentService, SimulationRail, PaymentJournal],
) -> None:
    """Der Bestand machte daraus ein ``needs_confirm`` — also eine Frage."""
    service, _rail, _journal = agent_service
    for index in range(4):
        view = await service.create_intent(_request(500), f"agent-idem-0000000010{index}")
        assert view.status is PaymentStatus.AWAITING_APPROVAL
        service.authorize(view.intent_id, "654321")
        assert (await service.execute(view.intent_id)).status is PaymentStatus.SETTLED

    over = await service.create_intent(_request(500), "agent-idem-00000000200")

    assert over.status is PaymentStatus.DENIED
    assert over.decision is not None
    assert over.decision.rule_ids == ("actor_limits",)
    assert "agent daily limit exceeded" in over.decision.reasons[0]


async def test_ein_unbekannter_agent_darf_nichts(
    agent_service: tuple[PaymentService, SimulationRail, PaymentJournal],
) -> None:
    """Die Tabelle ist eine Allowlist: fehlt die Zeile, fehlt die Erlaubnis."""
    service, _rail, _journal = agent_service
    view = await service.create_intent(
        _request(100, actor="agent:unknown"), "agent-idem-00000000300"
    )

    assert view.status is PaymentStatus.DENIED
    assert view.decision is not None
    assert view.decision.rule_ids == ("actor_limits",)


async def test_ein_fremder_zweck_wird_abgelehnt(
    agent_service: tuple[PaymentService, SimulationRail, PaymentJournal],
) -> None:
    service, _rail, _journal = agent_service
    view = await service.create_intent(
        _request(100, purpose="api_credit"), "agent-idem-00000000400"
    )
    assert view.status is PaymentStatus.AUTHORIZED

    other = await service.create_intent(
        _request(100, purpose="self_test"), "agent-idem-00000000500"
    )
    assert other.status is PaymentStatus.DENIED
    assert other.decision is not None
    assert other.decision.rule_ids == ("actor_limits",)


def test_die_agenten_tabelle_ist_eine_erlaubnisliste() -> None:
    """Eine unlesbare Tabelle ergibt ``{}`` — und damit keinen erlaubten Agenten."""
    assert load_actor_limits(Path("does-not-exist.json")) == {}
