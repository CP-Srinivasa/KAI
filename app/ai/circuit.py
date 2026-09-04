"""Circuit-Breaker auf dem *tatsächlichen* Upstream, nicht auf dem Alias.

Der Defekt des ersten Anlaufs hiess „alias-only Circuit-Breaker": der Zustand
hing an ``Route:Alias``. Fiel ein Anbieter hinter dem Alias aus, sperrte der
Breaker den Alias — und damit auch jeden anderen Anbieter, der ihn hätte
bedienen können. Ein einzelner kaputter Upstream nahm genau die Ausweichwege
mit, die es in dem Moment brauchte.

Hier ist der Schlüssel dreiteilig:

    logische Route  +  angeforderter Alias  +  tatsächlicher Upstream

Meldet das Gateway den tatsächlichen Upstream, wird auf ihn gesperrt und die
Alternativen bleiben offen. Meldet es ihn nicht, ist der Alias das Genaueste,
was man hat — dann wird auf den Alias gesperrt, und das ist eine bewusst
gröbere Sperre und kein Versehen. :func:`circuit_key` macht diesen Unterschied
sichtbar, statt ihn zu verwischen.

Rein: keine Uhr, kein I/O, kein globaler Zustand. Jede Funktion bekommt ``now_s``
übergeben; wer die Zeit liefert, entscheidet der Aufrufer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Final, Literal

from app.ai.models import AttemptTrace
from app.ai.routes import Route

CircuitState = Literal["closed", "open", "half_open"]

#: Wie viele aufeinanderfolgende Fehlschläge öffnen den Kreis.
DEFAULT_FAILURE_THRESHOLD: Final = 5
#: Wie lange er offen bleibt, bevor ein einzelner Versuch erlaubt wird.
DEFAULT_COOLDOWN_S: Final = 60.0


@dataclass(frozen=True)
class CircuitPolicy:
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    cooldown_s: float = DEFAULT_COOLDOWN_S


@dataclass(frozen=True)
class CircuitKey:
    """Worauf gesperrt wird — und wie genau diese Sperre ist."""

    route: Route
    alias: str
    upstream: str = ""

    @property
    def precise(self) -> bool:
        """Sperrt dieser Schlüssel den tatsächlichen Upstream?

        ``False`` heisst: der Upstream war unbekannt, gesperrt wird der ganze
        Alias. Das ist die gröbere Sperre — sie darf vorkommen, aber sie soll
        beim Lesen sofort als solche erkennbar sein.
        """
        return bool(self.upstream)


def circuit_key(route: Route, alias: str, attempt: AttemptTrace | None = None) -> CircuitKey:
    """Der Schlüssel für einen Versuch — so genau wie belegbar.

    Der Upstream wird nur übernommen, wenn der Versuch ihn BEWIESEN hat
    (Anbieter und Modell aus der Antwort). Den angeforderten Alias als
    Upstream einzutragen wäre eine Behauptung über etwas Ungemessenes und
    machte die feine Sperre zu einer verkleideten groben.
    """
    if attempt is not None and attempt.identity_proven:
        return CircuitKey(route, alias, f"{attempt.actual_provider}/{attempt.actual_model}")
    return CircuitKey(route, alias)


@dataclass(frozen=True)
class CircuitRecord:
    """Der Zustand EINES Schlüssels. Unveränderlich; Übergänge geben Neues zurück."""

    consecutive_failures: int = 0
    opened_at_s: float | None = None
    half_open_in_flight: bool = False

    def state(self, *, now_s: float, policy: CircuitPolicy) -> CircuitState:
        if self.opened_at_s is None:
            return "closed"
        if now_s - self.opened_at_s < policy.cooldown_s:
            return "open"
        return "half_open"


@dataclass(frozen=True)
class CircuitBook:
    """Alle Schlüssel nebeneinander — ein defekter sperrt die anderen nicht."""

    records: Mapping[CircuitKey, CircuitRecord] = field(default_factory=dict)

    def record_for(self, key: CircuitKey) -> CircuitRecord:
        return self.records.get(key, CircuitRecord())

    def state(self, key: CircuitKey, *, now_s: float, policy: CircuitPolicy) -> CircuitState:
        return self.record_for(key).state(now_s=now_s, policy=policy)

    def allows(self, key: CircuitKey, *, now_s: float, policy: CircuitPolicy) -> bool:
        """Darf jetzt ein Versuch gegen diesen Schlüssel laufen?

        ``half_open`` lässt GENAU EINEN Versuch durch. Ohne diese Begrenzung
        stürmt nach jedem Cooldown die volle Last gegen einen Upstream, der sich
        gerade erst erholt — und öffnet ihn sofort wieder.
        """
        record = self.record_for(key)
        match record.state(now_s=now_s, policy=policy):
            case "closed":
                return True
            case "half_open":
                return not record.half_open_in_flight
            case _:
                return False

    def _with(self, key: CircuitKey, record: CircuitRecord) -> CircuitBook:
        merged = dict(self.records)
        if record == CircuitRecord():
            merged.pop(key, None)
        else:
            merged[key] = record
        return CircuitBook(merged)

    def on_attempt(self, key: CircuitKey, *, now_s: float, policy: CircuitPolicy) -> CircuitBook:
        """Ein Versuch startet — im halboffenen Zustand die eine erlaubte Probe."""
        record = self.record_for(key)
        if (
            record.state(now_s=now_s, policy=policy) == "half_open"
            and not record.half_open_in_flight
        ):
            return self._with(key, replace(record, half_open_in_flight=True))
        return self

    def on_success(self, key: CircuitKey) -> CircuitBook:
        """Erfolg schliesst den Kreis vollständig — kein Rest-Zähler bleibt stehen."""
        return self._with(key, CircuitRecord())

    def on_failure(self, key: CircuitKey, *, now_s: float, policy: CircuitPolicy) -> CircuitBook:
        """Fehlschlag zählt hoch und öffnet bei Erreichen der Schwelle.

        Ein Fehlschlag im halboffenen Zustand öffnet sofort wieder — die Probe
        war die Frage, und sie ist beantwortet.
        """
        record = self.record_for(key)
        if record.state(now_s=now_s, policy=policy) == "half_open":
            return self._with(
                key,
                CircuitRecord(
                    consecutive_failures=record.consecutive_failures + 1,
                    opened_at_s=now_s,
                    half_open_in_flight=False,
                ),
            )
        failures = record.consecutive_failures + 1
        opened = now_s if failures >= policy.failure_threshold else record.opened_at_s
        return self._with(
            key,
            CircuitRecord(
                consecutive_failures=failures,
                opened_at_s=opened,
                half_open_in_flight=False,
            ),
        )

    def open_keys(self, *, now_s: float, policy: CircuitPolicy) -> tuple[CircuitKey, ...]:
        return tuple(
            k
            for k, r in sorted(
                self.records.items(), key=lambda kv: (kv[0].route, kv[0].alias, kv[0].upstream)
            )
            if r.state(now_s=now_s, policy=policy) == "open"
        )


__all__ = [
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_FAILURE_THRESHOLD",
    "CircuitBook",
    "CircuitKey",
    "CircuitPolicy",
    "CircuitRecord",
    "CircuitState",
    "circuit_key",
]
