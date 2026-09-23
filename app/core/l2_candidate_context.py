"""Kandidatenkontext für die L2-Messung — wer misst, für welche Entscheidung, auf welchem Preis.

**Das Problem.** Der L2-Provider (``app/signals/l2_wiring.py``) läuft INNERHALB
von ``SignalGenerator.generate`` und schreibt seine Messung in den Shadow-Log.
Dort stand bisher nur ``symbol``, ``direction``, die Rohmerkmale — und ein
``ts`` aus ``datetime.now()``. Der Kandidat dagegen wird erst nach dem Zyklus
geschrieben, mit ``candidate_id=cycle.cycle_id`` und ``ts_utc=cycle.started_at``
(``trading_loop.py``). Zwei Zeitpunkte, keine Verbindung: Der Evaluator kann
Messung und Kandidat nur über ``symbol`` + Zeitfenster paaren
(``l2_evidence_eval.pit_join``), und wie weit Messzeit und Entscheidungszeit
auseinanderliegen, ist aus den Daten nicht rekonstruierbar.

**Was dieser Kontext liefert — und was nicht.** Er reicht drei Werte durch, die
zum Messzeitpunkt bereits feststehen:

``candidate_id``
    Die Zyklus-ID. Sie entsteht in ``run_cycle`` als Allererstes
    (``cycle_id = _new_cycle_id()``), also lange VOR der Messung — die
    Forderung „ID vor der Messung verfügbar" ist damit erfüllt, ohne dass
    irgendetwas vorgezogen oder erfunden wird.
``decision_ts``
    ``started_at`` desselben Zyklus: der Zeitpunkt, auf den sich die
    Entscheidung bezieht.
``reference_price_ts``
    ``MarketDataPoint.timestamp_utc``: der Preis, auf dem die Entscheidung
    beruht.

**Keine Rückdatierung.** Alle drei Werte existieren unabhängig von diesem Modul;
es schreibt sie nur an eine Stelle, an der sie bisher fehlten. Der
Beobachtungszeitpunkt bleibt, was er war — die Uhr zum Zeitpunkt der Messung.
Historische Zeilen ohne diese Felder bleiben unverändert und gültig; ein Leser
muss sie weiterhin akzeptieren.

**Warum ``app/core`` und nicht ``app/orchestrator``.** Gesetzt wird der Kontext
im Loop (``app/orchestrator``), gelesen im Messpfad (``app/signals``). Ein Modul
in ``app/orchestrator`` zwänge ``app/signals`` zu einem Import nach oben —
``app/orchestrator`` importiert ``app/signals`` bereits, es entstünde ein
Paketzyklus. ``app/core`` importieren beide Seiten schon heute (wie
``app/core/file_lock.py``), also trägt der Kontext hier keine neue Kante ein.
``tests/unit/test_core_path_boundaries.py`` hält die Richtung fest.

**Kausalität wird geprüft, nicht unterstellt.** Ein Preis, der jünger ist als
die Entscheidung, die auf ihm beruhen soll, ist Look-ahead — genau der Fehler,
gegen den der Point-in-Time-Join gebaut wurde. Solche Fälle werden über
``causality_ok=False`` sichtbar gemacht, statt still mitzulaufen; verworfen
wird nichts, das Urteil gehört dem Evaluator.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

__all__ = ["L2CandidateContext", "bind_candidate", "current_candidate"]


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


@dataclass(frozen=True)
class L2CandidateContext:
    """Unveränderlicher Bezug einer L2-Messung auf ihren Kandidaten."""

    candidate_id: str
    decision_ts: str
    reference_price_ts: str | None = None

    @property
    def causality_ok(self) -> bool | None:
        """Lag der Referenzpreis vor der Entscheidung?

        ``None``, wenn eine der beiden Zeiten fehlt oder unlesbar ist — dann ist
        die Frage nicht beantwortbar, und ``False`` wäre eine Behauptung.
        Gleichstand gilt als in Ordnung: derselbe Tick kann Preis und
        Entscheidung tragen.
        """
        price = _parse(self.reference_price_ts)
        decision = _parse(self.decision_ts)
        if price is None or decision is None:
            return None
        if (price.tzinfo is None) != (decision.tzinfo is None):
            return None  # naiv gegen aware ist nicht vergleichbar
        return price <= decision

    def as_log_fields(self) -> dict[str, str | bool]:
        """Die Felder, die eine Messzeile zusätzlich trägt.

        Bewusst flach und additiv: ein Leser, der sie nicht kennt, ignoriert sie;
        ein Leser, der sie kennt, braucht keinen Zeitfenster-Join mehr.
        """
        fields: dict[str, str | bool] = {
            "candidate_id": self.candidate_id,
            "decision_ts": self.decision_ts,
        }
        if self.reference_price_ts:
            fields["reference_price_ts"] = self.reference_price_ts
        causality = self.causality_ok
        if causality is not None:
            fields["causality_ok"] = causality
        return fields


_CURRENT: ContextVar[L2CandidateContext | None] = ContextVar("l2_candidate_context", default=None)


def current_candidate() -> L2CandidateContext | None:
    """Der Kontext des laufenden Zyklus — ``None`` außerhalb eines Zyklus."""
    return _CURRENT.get()


@contextmanager
def bind_candidate(
    *, candidate_id: str, decision_ts: str, reference_price_ts: str | None = None
) -> Iterator[L2CandidateContext]:
    """Den Kontext für die Dauer eines Blocks setzen und danach exakt zurücksetzen.

    ``ContextVar`` statt Modulvariable: Der Loop kann nebenläufig laufen, und ein
    Token-Reset stellt auch bei einer Ausnahme den vorherigen Zustand her — ein
    Kandidat darf niemals in die Messung eines anderen Zyklus lecken.
    """
    context = L2CandidateContext(
        candidate_id=candidate_id,
        decision_ts=decision_ts,
        reference_price_ts=reference_price_ts,
    )
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)
