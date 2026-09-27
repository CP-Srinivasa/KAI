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
übergeben; wer die Zeit liefert, entscheidet der Aufrufer. Der EINE veränderliche
Teil ist :class:`CircuitStore` — ein Halter, der das unveränderliche Buch über
Aufrufe hinweg trägt. Ohne ihn begann bis 04046c68 jeder Aufruf mit einem leeren
Buch, und der Breaker öffnete nie (LiteLLM-Audit 27.09., Befund B).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
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
    #: Wann die laufende Probe gestartet ist. Eine Probe, die nie zurückkommt
    #: (Abbruch, Absturz, verlorene Task), darf den Kreis nicht für immer
    #: halboffen-belegt halten: nach einem weiteren Cooldown verfällt sie.
    probe_started_at_s: float | None = None

    def state(self, *, now_s: float, policy: CircuitPolicy) -> CircuitState:
        if self.opened_at_s is None:
            return "closed"
        if now_s - self.opened_at_s < policy.cooldown_s:
            return "open"
        return "half_open"

    def probe_pending(self, *, now_s: float, policy: CircuitPolicy) -> bool:
        """Läuft gerade eine Probe, die noch zählt?"""
        if not self.half_open_in_flight:
            return False
        if self.probe_started_at_s is None:
            return True
        return now_s - self.probe_started_at_s < policy.cooldown_s


@dataclass(frozen=True)
class CircuitBook:
    """Alle Schlüssel nebeneinander — ein defekter sperrt die anderen nicht."""

    records: Mapping[CircuitKey, CircuitRecord] = field(default_factory=dict)
    #: Genaue Schlüssel, deren Upstream sich für seinen Alias schon einmal
    #: benannt hat. Ohne dieses Gedächtnis wüsste :meth:`admit` nach einem
    #: Erfolg nicht mehr, dass es eine Alternative gibt — der Erfolg löscht den
    #: Datensatz ja vollständig.
    known_upstreams: frozenset[CircuitKey] = frozenset()

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
                return not record.probe_pending(now_s=now_s, policy=policy)
            case _:
                return False

    def _with(self, key: CircuitKey, record: CircuitRecord) -> CircuitBook:
        merged = dict(self.records)
        if record == CircuitRecord():
            merged.pop(key, None)
        else:
            merged[key] = record
        known = self.known_upstreams | {key} if key.precise else self.known_upstreams
        return CircuitBook(merged, known)

    def _upstreams(self, route: Route, alias: str) -> tuple[CircuitKey, ...]:
        keys = set(self.known_upstreams) | {k for k in self.records if k.precise}
        return tuple(
            sorted(
                (k for k in keys if k.route == route and k.alias == alias),
                key=lambda k: k.upstream,
            )
        )

    def admit(
        self, route: Route, alias: str, *, now_s: float, policy: CircuitPolicy
    ) -> tuple[bool, CircuitBook]:
        """Darf ein Versuch gegen diesen Alias hinaus — und wenn ja, als Probe?

        Vor dem Aufruf ist nur der Alias bekannt. Gesperrt wird deshalb, wenn
        der grobe Schlüssel zu ist ODER wenn JEDER bekannte Upstream des Alias
        zu ist: dann gibt es keinen Beleg für einen Weg, der antworten würde,
        und der nächste Versuch träfe mit hoher Wahrscheinlichkeit denselben
        kaputten Upstream. Hat eine Alternative schon geantwortet, bleibt der
        Alias offen — die Zusicherung aus ADR 0017 gilt weiter.

        Zugelassen heisst zugleich: jeder halboffene Schlüssel des Alias
        vergibt hier seine eine Probe. Entscheidung und Markierung sind EIN
        Schritt; getrennt könnten zwei Aufrufe dieselbe Probe bekommen.
        """
        coarse = CircuitKey(route, alias)
        if not self.allows(coarse, now_s=now_s, policy=policy):
            return False, self
        upstreams = self._upstreams(route, alias)
        if upstreams and not any(self.allows(key, now_s=now_s, policy=policy) for key in upstreams):
            return False, self
        book = self.on_attempt(coarse, now_s=now_s, policy=policy)
        for key in upstreams:
            book = book.on_attempt(key, now_s=now_s, policy=policy)
        return True, book

    def on_attempt(self, key: CircuitKey, *, now_s: float, policy: CircuitPolicy) -> CircuitBook:
        """Ein Versuch startet — im halboffenen Zustand die eine erlaubte Probe."""
        record = self.record_for(key)
        if record.state(now_s=now_s, policy=policy) == "half_open" and not record.probe_pending(
            now_s=now_s, policy=policy
        ):
            return self._with(
                key, replace(record, half_open_in_flight=True, probe_started_at_s=now_s)
            )
        return self

    def release_probes(self, route: Route, alias: str) -> CircuitBook:
        """Eine abgebrochene Probe gibt ihren Platz frei — ohne Urteil.

        Ein Abbruch ist weder Erfolg noch Fehlschlag des Upstreams: gezählt
        wird nichts, nur die Belegung fällt weg, damit der nächste Aufruf
        proben darf.
        """
        book = self
        for key, record in self.records.items():
            if key.route == route and key.alias == alias and record.half_open_in_flight:
                book = book._with(
                    key, replace(record, half_open_in_flight=False, probe_started_at_s=None)
                )
        return book

    def on_success(self, key: CircuitKey) -> CircuitBook:
        """Erfolg schliesst den Kreis vollständig — kein Rest-Zähler bleibt stehen."""
        return self._with(key, CircuitRecord())

    def on_result(
        self,
        *,
        coarse: CircuitKey,
        precise: CircuitKey,
        ok: bool,
        now_s: float,
        policy: CircuitPolicy,
    ) -> CircuitBook:
        """Einen zurückgekehrten Versuch buchen — fein, und bei Erfolg auch grob.

        Gebucht wird auf dem feinen Schlüssel, damit ein defekter Anbieter
        nicht den Alias mitnimmt. Ein Erfolg schliesst zusätzlich den groben:
        der Alias hat geantwortet, egal über welchen Upstream.
        """
        book = (
            self.on_success(precise) if ok else self.on_failure(precise, now_s=now_s, policy=policy)
        )
        if coarse != precise and ok:
            book = book.on_success(coarse)
        return book

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


class CircuitStore:
    """Der eine veränderliche Halter für das unveränderliche Buch.

    Das Buch bleibt rein und prüfbar; dieser Halter sorgt nur dafür, dass der
    nächste Aufruf das Buch des vorigen sieht. Jede Änderung ist EIN Schritt
    unter einer Sperre — zwischen Lesen und Zurückschreiben liegt kein
    ``await`` und kein anderer Thread, sonst überschriebe ein paralleler
    Aufruf die Buchung des anderen.
    """

    def __init__(self, book: CircuitBook | None = None) -> None:
        self._book = book or CircuitBook()
        self._lock = threading.Lock()

    @property
    def book(self) -> CircuitBook:
        return self._book

    def update(self, change: Callable[[CircuitBook], CircuitBook]) -> CircuitBook:
        with self._lock:
            self._book = change(self._book)
            return self._book

    def admit(self, route: Route, alias: str, *, now_s: float, policy: CircuitPolicy) -> bool:
        with self._lock:
            admitted, self._book = self._book.admit(route, alias, now_s=now_s, policy=policy)
            return admitted

    def reset(self) -> None:
        with self._lock:
            self._book = CircuitBook()


__all__ = [
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_FAILURE_THRESHOLD",
    "CircuitBook",
    "CircuitKey",
    "CircuitPolicy",
    "CircuitRecord",
    "CircuitState",
    "CircuitStore",
    "circuit_key",
]
