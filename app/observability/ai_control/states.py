"""Ein Zustand je Objekt -- Regeln in fester Reihenfolge (Spec §3).

Circuit: ``open`` (gerade nach Fehlern geoeffnet) heisst GESTOERT, ``half_open``
(prueft den Wiederanlauf) heisst PAUSIERT -- die Spec nennt beides, ohne es zu trennen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

ACTIVE_WINDOW: Final = timedelta(minutes=15)
FAILURE_RATE: Final = 0.20
MIN_CALLS_FOR_RATE: Final = 10
MAX_CONSECUTIVE: Final = 5


class State(StrEnum):
    AKTIV = "aktiv"
    BEREIT = "bereit"
    PAUSIERT = "pausiert"
    GESTOERT = "gestoert"
    AUSSER_KRAFT = "ausser_kraft"
    DEAKTIVIERT = "deaktiviert"


@dataclass(frozen=True)
class Signals:
    now: datetime
    configured: bool
    disabled_reason: str = ""
    override_reason: str = ""
    proxy_down: bool = False
    circuit_open: bool = False
    consecutive_failures: int = 0
    calls_1h: int = 0
    failures_1h: int = 0
    balance_exhausted: bool = False
    paused_reason: str = ""
    paused_until: datetime | None = None
    last_ok: datetime | None = None


@dataclass(frozen=True)
class Verdict:
    state: State
    reason: str
    since: datetime | None


def classify(s: Signals) -> Verdict:
    if not s.configured:
        return Verdict(State.DEAKTIVIERT, s.disabled_reason or "laut Konfiguration aus", None)
    if s.override_reason:
        return Verdict(State.AUSSER_KRAFT, s.override_reason, None)
    gruende: list[str] = []
    if s.proxy_down:
        gruende.append("Proxy nicht erreichbar")
    if s.circuit_open:
        gruende.append("Circuit offen nach Fehlern")
    if s.consecutive_failures >= MAX_CONSECUTIVE:
        gruende.append(f"{s.consecutive_failures} Fehlversuche in Folge")
    if s.calls_1h >= MIN_CALLS_FOR_RATE and s.failures_1h / s.calls_1h > FAILURE_RATE:
        gruende.append(f"Fehlerquote {round(100 * s.failures_1h / s.calls_1h)} % in 1 h")
    if s.balance_exhausted:
        gruende.append("Guthaben leer")
    if gruende:
        return Verdict(State.GESTOERT, " · ".join(gruende), None)
    if s.paused_reason:
        bis = f" bis {s.paused_until:%H:%M} UTC" if s.paused_until else ""
        return Verdict(State.PAUSIERT, s.paused_reason + bis, s.paused_until)
    if s.last_ok is not None and s.now - s.last_ok <= ACTIVE_WINDOW:
        return Verdict(State.AKTIV, f"letzter Aufruf {s.last_ok:%H:%M} UTC", s.last_ok)
    if s.last_ok is None:
        return Verdict(State.BEREIT, "noch kein Aufruf", None)
    return Verdict(State.BEREIT, f"letzter Aufruf {s.last_ok:%d.%m. %H:%M} UTC", s.last_ok)


__all__ = [
    "ACTIVE_WINDOW",
    "FAILURE_RATE",
    "MAX_CONSECUTIVE",
    "MIN_CALLS_FOR_RATE",
    "Signals",
    "State",
    "Verdict",
    "classify",
]
