"""Budget auf verbuchten Kosten — und auf der ehrlichen Antwort „unbekannt".

Der Defekt des ersten Anlaufs hiess „wirkungsloser Per-Call-Budget-Gate ohne
belastbare Kostenschätzung": jeder Aufruf wurde gegen eine Schätzung geprüft,
die es gar nicht gab, unbekannte Kosten wurden als 0 verbucht, und das
Tagesbudget bestand aus lauter Nullen — während real Geld abfloss.

Zwei Regeln, die das ausschliessen:

1. **Verbucht wird, was gemessen wurde.** Unbekannte Kosten erhöhen den
   Verbrauch nicht, aber sie werden GEZÄHLT. Ein Budget, dessen Deckung man
   nicht kennt, ist kein gedecktes Budget — :attr:`BudgetState.unknown_calls`
   macht die Lücke sichtbar, statt sie mit Nullen zu füllen.
2. **Hart abgelehnt wird nur mit Beleg.** Ohne belastbare Schätzung gibt es
   keine Vorab-Ablehnung. Wer ohne Zahlen ablehnt, lehnt nach Gefühl ab —
   und wer ohne Zahlen durchlässt, obwohl das Limit bereits überschritten IST,
   ebenso. Beide Fälle sind hier getrennt.

Rein: keine Uhr, kein I/O. Der Aufrufer bringt Fenster und Einträge mit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.ai.models import AttemptTrace
from app.ai.routes import Route

#: ``allow``            im Rahmen, Kosten belegt oder Limit unberührt
#: ``allow_unbudgeted`` erlaubt, aber ohne Deckungsnachweis — sichtbar machen
#: ``reject``           belegbar über dem Limit
BudgetDecision = Literal["allow", "allow_unbudgeted", "reject"]


@dataclass(frozen=True)
class BudgetPolicy:
    """Limits in USD. ``None`` heisst: dieses Fenster begrenzt nichts."""

    daily_limit_usd: float | None = None
    monthly_limit_usd: float | None = None


@dataclass(frozen=True)
class BudgetEntry:
    """Eine verbuchte Position. ``cost_usd=None`` heisst UNBEKANNT, nicht 0."""

    route: Route
    cost_usd: float | None = None

    @classmethod
    def from_attempt(cls, route: Route, attempt: AttemptTrace) -> BudgetEntry:
        return cls(route=route, cost_usd=attempt.cost_usd)


@dataclass(frozen=True)
class BudgetState:
    """Was in einem Fenster tatsächlich bekannt ist."""

    booked_usd: float
    known_calls: int
    unknown_calls: int

    @property
    def total_calls(self) -> int:
        return self.known_calls + self.unknown_calls

    @property
    def cost_known_rate(self) -> float | None:
        """``None`` ohne Aufrufe — nicht 1.0, denn nichts ist auch nichts belegt."""
        if self.total_calls == 0:
            return None
        return self.known_calls / self.total_calls

    @property
    def fully_accounted(self) -> bool:
        """Ist jeder Aufruf des Fensters mit Kosten belegt?

        Nur dann trägt ``booked_usd`` die ganze Wahrheit. Sonst ist es eine
        Untergrenze — und eine Untergrenze rechtfertigt keine harte Ablehnung
        und erst recht keine Entwarnung.
        """
        return self.total_calls > 0 and self.unknown_calls == 0


def accumulate(entries: Sequence[BudgetEntry]) -> BudgetState:
    """Einträge zu einem Fensterzustand — unbekannte Kosten werden gezählt, nicht genullt."""
    booked = 0.0
    known = 0
    unknown = 0
    for entry in entries:
        if entry.cost_usd is None:
            unknown += 1
            continue
        booked += entry.cost_usd
        known += 1
    return BudgetState(booked_usd=booked, known_calls=known, unknown_calls=unknown)


def _limit_breached(state: BudgetState, limit: float | None, estimate: float | None) -> bool:
    if limit is None:
        return False
    if state.booked_usd >= limit:
        return True
    return estimate is not None and state.booked_usd + estimate > limit


def decide(
    *,
    daily: BudgetState,
    monthly: BudgetState,
    policy: BudgetPolicy,
    estimated_request_cost_usd: float | None = None,
) -> BudgetDecision:
    """Darf dieser Aufruf laufen?

    ``reject`` nur, wenn es belegbar ist: entweder ist das Limit mit bereits
    VERBUCHTEN Kosten schon erreicht, oder eine vorhandene Schätzung führt
    darüber hinaus. Ohne Schätzung und unterhalb des Limits wird nicht
    abgelehnt — aber die Antwort heisst dann ``allow_unbudgeted`` und nicht
    ``allow``, damit „wir wissen es nicht" nicht als „alles in Ordnung"
    protokolliert wird.
    """
    if _limit_breached(daily, policy.daily_limit_usd, estimated_request_cost_usd):
        return "reject"
    if _limit_breached(monthly, policy.monthly_limit_usd, estimated_request_cost_usd):
        return "reject"
    limited = policy.daily_limit_usd is not None or policy.monthly_limit_usd is not None
    if limited and estimated_request_cost_usd is None:
        return "allow_unbudgeted"
    if not daily.fully_accounted and daily.total_calls > 0 and limited:
        return "allow_unbudgeted"
    return "allow"


def headroom_usd(state: BudgetState, limit: float | None) -> float | None:
    """Verbleibender Spielraum — ``None``, wenn unbegrenzt oder unbelegbar.

    Bewusst ``None`` statt einer Zahl, sobald das Fenster unbelegte Aufrufe
    enthält: der wahre Verbrauch liegt dann irgendwo über ``booked_usd``, und
    eine Restgrösse auszuweisen wäre eine Genauigkeit, die es nicht gibt.
    """
    if limit is None:
        return None
    if not state.fully_accounted and state.total_calls > 0:
        return None
    return max(0.0, limit - state.booked_usd)


__all__ = [
    "BudgetDecision",
    "BudgetEntry",
    "BudgetPolicy",
    "BudgetState",
    "accumulate",
    "decide",
    "headroom_usd",
]
