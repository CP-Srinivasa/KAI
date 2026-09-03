"""Ein- und Ausgaben des Control Plane (ADR 0017 §3/§10).

Getrennt von :mod:`app.payments.service`, weil hier steht, WAS ueber die
Grenze geht, und dort, WAS damit geschieht. Der API-Layer und der Reconciler
brauchen diese Typen, ohne die Orchestrierung zu importieren.

Die Ausgaben tragen bewusst weder Destination noch Idempotency-Key: was ein
Aufrufer zurueckbekommt, soll man ohne Nachdenken loggen koennen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.payments.enums import PaymentMode, PaymentStatus
from app.payments.models import Money, PaymentIntent, PaymentPolicyDecision, Quote
from app.payments.rail import DecodedDestination


class PaymentServiceError(RuntimeError):
    """Der Control Plane verweigert eine Anfrage."""


@dataclass(frozen=True)
class PaymentRequest:
    """Was ein Aufrufer angibt. Die IDs vergibt der Service.

    ``intent_id`` steht ausdruecklich NICHT hier: eine vom Aufrufer gewaehlte
    ID waere ein zweiter Idempotenz-Kanal neben dem Key — mit anderer Semantik
    und ohne Journal-Bindung.
    """

    actor: str
    purpose: str
    destination: str
    amount: Money
    fee_limit: Money
    correlation_id: str = "unset"
    rail: str = "lightning"
    ttl_seconds: int = 3600

    def to_intent(
        self, *, intent_id: str, idempotency_key: str, moment: datetime, mode: PaymentMode
    ) -> PaymentIntent:
        """Aus der Anfrage einen Intent — mit den Feldern, die NUR der Service kennt.

        Der Modus kommt aus der Konfiguration, nicht aus der Anfrage: ein
        Aufrufer, der seinen eigenen Modus mitgeben duerfte, koennte den
        SIMULATION-Default umgehen.
        """
        return PaymentIntent(
            intent_id=intent_id,
            idempotency_key=idempotency_key,
            correlation_id=self.correlation_id,
            actor=self.actor,
            purpose=self.purpose,
            rail=self.rail,
            destination=self.destination,
            amount_requested=self.amount,
            fee_limit=self.fee_limit,
            created_at=moment,
            expires_at=moment + timedelta(seconds=self.ttl_seconds),
            mode=mode,
        )


@dataclass(frozen=True)
class IntentView:
    """Was ein Aufrufer zurueckbekommt — nie die Destination, nie ein Secret.

    ``replayed=True`` heisst: der Vorgang existierte schon. Das ist eine
    Antwort, kein Fehler (ADR §5: HTTP 200 mit ``replayed=true``).
    """

    intent_id: str
    status: PaymentStatus
    replayed: bool = False
    decision: PaymentPolicyDecision | None = None
    detail: str = ""


@dataclass(frozen=True)
class SimulationView:
    """Vorschau: was WUERDE passieren (ADR §1 SHADOW)."""

    intent_id: str
    status: PaymentStatus
    quote: Quote | None = None
    decision: PaymentPolicyDecision | None = None


@dataclass
class Tracked:
    """Der Intent im Prozessspeicher plus sein aktueller Zustand.

    Bewusst NICHT persistiert: das Journal traegt die Destination nur als Hash,
    also ist ein Intent nach einem Neustart nicht mehr sendbar. Das ist die
    Absicht — der Weg zurueck in den Sendepfad fuehrt ueber Reconciliation mit
    Node-Evidenz, nicht ueber einen wiederhergestellten Speicherzustand.
    """

    intent: PaymentIntent
    status: PaymentStatus
    decision: PaymentPolicyDecision | None = None
    decoded: DecodedDestination | None = None
    attempts: int = 0


__all__ = [
    "IntentView",
    "PaymentRequest",
    "PaymentServiceError",
    "SimulationView",
    "Tracked",
]
