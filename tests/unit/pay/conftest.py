"""Gemeinsame Bauteile der KAI-PAY-Tests (D-CORE-006).

Ein Kern in SIMULATION, ein Store im ``tmp_path``, eine feste Uhr. Der Rail ist
der ``SimulationRail`` des Kerns und ausdruecklich kein Doppel: der einzige
Zustandswechsel, auf den es hier ankommt — jemand bezahlt eine ausgestellte
Forderung —, hat dort mit ``settle(ref_hash)`` einen echten Haken.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.pay_settings import PaySettings
from app.core.payment_settings import PaymentSettings
from app.pay.service import PayService
from app.pay.store import PayStore
from app.payments.journal import PaymentJournal
from app.payments.rails.simulation import SimulationRail
from app.payments.service import PaymentService

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def pay_settings(**overrides: object) -> PaySettings:
    base: dict[str, object] = {
        "enabled": True,
        "purpose": "kai_pay",
        "default_expiry_seconds": 900,
        "max_amount_sat": 1_000_000,
        "webhook_secret": "",
        "poll_interval_seconds": 20,
        "max_open_requests": 200,
    }
    base.update(overrides)
    return PaySettings(**base)  # type: ignore[arg-type]


def payment_settings(**overrides: object) -> PaymentSettings:
    base: dict[str, object] = {
        "mode": "simulation",
        "purposes_allowed": "kai_pay,self_test",
        "per_payment_max_sat": 5_000,
        "daily_hard_cap_sat": 10_000,
    }
    base.update(overrides)
    return PaymentSettings(**base)  # type: ignore[arg-type]


class Harness:
    """Kern, Rail, Store und Produktschicht in einem Griff."""

    def __init__(self, tmp_path: Path, *, settings: PaySettings | None = None) -> None:
        self.rail = SimulationRail(now=NOW)
        self.journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
        self.journal.open()
        self.payments = PaymentService(
            journal=self.journal,
            rails={"simulation": self.rail, "lightning": self.rail},
            settings=payment_settings(),
            clock=lambda: NOW,
        )
        self.store = PayStore(tmp_path / "pay" / "requests.jsonl")
        self.store.load()
        self.settings = settings or pay_settings()
        self.now = NOW
        self.service = PayService(
            payments=self.payments,
            store=self.store,
            settings=self.settings,
            clock=lambda: self.now,
        )

    def settled_records(self, ref_hash: str) -> list[object]:
        """Alle ``receivable_settled``-Records zu einer Forderung."""
        from app.payments.receivables import SETTLED_EVENT, receivable_intent_id

        return [
            event
            for event in self.journal.events(receivable_intent_id(ref_hash))
            if event.event_type == SETTLED_EVENT
        ]

    def reopen(self) -> PayService:
        """Ein Neustart: derselbe Strom, ein frisch gebauter Index."""
        journal = PaymentJournal(self.journal.path)
        journal.open()
        self.payments = PaymentService(
            journal=journal,
            rails={"simulation": self.rail, "lightning": self.rail},
            settings=payment_settings(),
            clock=lambda: NOW,
        )
        self.journal = journal
        self.store = PayStore(self.store.path)
        self.store.load()
        self.service = PayService(
            payments=self.payments,
            store=self.store,
            settings=self.settings,
            clock=lambda: self.now,
        )
        return self.service


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)
