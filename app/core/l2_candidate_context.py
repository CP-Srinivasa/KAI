"""Kandidatenkontext für die L2-Messung — wer misst, für welchen Zyklus, mit welchen Eingaben.

**Das Problem.** Der L2-Provider (``app/signals/l2_wiring.py``) läuft INNERHALB
von ``SignalGenerator.generate`` und schreibt seine Messung in den Shadow-Log.
Der Kandidat wird erst nach dem Zyklus geschrieben, mit
``candidate_id=cycle.cycle_id`` und ``ts_utc=cycle.started_at``
(``trading_loop.py``). Dieser Kontext verbindet beide über die Zyklus-ID.

**Zeitmodell (Befund 3, Operator-Entscheid 2026-09-30: L2-interne Korrektur).**
Bis dahin galt ``decision_ts`` = Zyklusbeginn als Entscheidungszeitpunkt. Der Kurs
wird aber erst NACH dem Zyklusbeginn abgerufen (gemessen: Median 0,17 s, max. 2 s),
also fiel fast jede Messung durch die Kausalitätsprüfung (364 von 366). Jetzt:

``cycle_started_at``
    Reine Betriebszeit: der Zyklusbeginn. Er ist zugleich der Anker der
    Kandidatenzeile (``ts_utc``) und damit des Outcomes.
``input_cutoff_ts``
    Der Eingabeschnitt: gesetzt NACH dem Kursabruf, direkt vor der
    Signalerzeugung. Nur Preis- und L1-Daten, die bis dahin vorlagen, gehen
    in die Messung ein. Er ist der Entscheidungszeitpunkt der Messung.
``reference_price_ts``
    ``MarketDataPoint.timestamp_utc``: der Preis, auf dem das Signal beruht.

Der L2-Provider ergänzt ``l1_observed_ts`` (den L1-Datensatz „Stand Schnitt“).
Kausal ist eine Messung, wenn Preis UND L1-Datensatz nicht jünger als der Schnitt
sind. Ein Zeitstempel aus der Zukunft (Uhrenversatz) fällt damit ebenfalls durch.

**Keine Rückdatierung.** Alle Werte existieren unabhängig von diesem Modul. Alte
Zeilen (ohne Kontext oder mit ``decision_ts``) bleiben unverändert; der Leser
(``l2_evidence_eval.pit_join``) behandelt jede Form mit ihrer eigenen Regel.

**Warum ``app/core`` und nicht ``app/orchestrator``.** Gesetzt wird der Kontext
im Loop (``app/orchestrator``), gelesen im Messpfad (``app/signals``). Ein Modul
in ``app/orchestrator`` zwänge ``app/signals`` zu einem Import nach oben —
``app/orchestrator`` importiert ``app/signals`` bereits, es entstünde ein
Paketzyklus. ``tests/unit/test_core_path_boundaries.py`` hält die Richtung fest.

**Kausalität wird geprüft, nicht unterstellt.** Verletzungen werden über
``causality_ok=False`` sichtbar gemacht, statt still mitzulaufen; verworfen wird
hier nichts, das Urteil gehört dem Evaluator.
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


def _not_after(earlier: str | None, later: str | None) -> bool | None:
    """``earlier <= later``; ``None``, wenn eine Zeit fehlt, unlesbar oder unvergleichbar ist."""
    a, b = _parse(earlier), _parse(later)
    if a is None or b is None or (a.tzinfo is None) != (b.tzinfo is None):
        return None
    return a <= b


@dataclass(frozen=True)
class L2CandidateContext:
    """Unveränderlicher Bezug einer L2-Messung auf ihren Zyklus und ihren Eingabeschnitt."""

    candidate_id: str
    cycle_started_at: str
    input_cutoff_ts: str
    reference_price_ts: str | None = None

    def causality_ok(self, l1_observed_ts: str | None = None) -> bool | None:
        """Lagen Preis (und, falls angegeben, der L1-Datensatz) nicht nach dem Schnitt?

        ``None``, wenn der Preis fehlt oder eine Zeit unlesbar ist — dann ist die
        Frage nicht beantwortbar, und ``False`` wäre eine Behauptung. Gleichstand
        gilt als in Ordnung. Ein L1-Datensatz nach dem Schnitt ist immer ``False``.
        """
        price = _not_after(self.reference_price_ts, self.input_cutoff_ts)
        if l1_observed_ts is None:
            return price
        l1 = _not_after(l1_observed_ts, self.input_cutoff_ts)
        if price is False or l1 is False:
            return False
        if price is None or l1 is None:
            return None
        return True

    def as_log_fields(self, l1_observed_ts: str | None = None) -> dict[str, str | bool]:
        """Die Felder, die eine Messzeile zusätzlich trägt (flach und additiv)."""
        fields: dict[str, str | bool] = {
            "candidate_id": self.candidate_id,
            "cycle_started_at": self.cycle_started_at,
            "input_cutoff_ts": self.input_cutoff_ts,
        }
        if self.reference_price_ts:
            fields["reference_price_ts"] = self.reference_price_ts
        if l1_observed_ts:
            fields["l1_observed_ts"] = l1_observed_ts
        causality = self.causality_ok(l1_observed_ts)
        if causality is not None:
            fields["causality_ok"] = causality
        return fields


_CURRENT: ContextVar[L2CandidateContext | None] = ContextVar("l2_candidate_context", default=None)


def current_candidate() -> L2CandidateContext | None:
    """Der Kontext des laufenden Zyklus — ``None`` außerhalb eines Zyklus."""
    return _CURRENT.get()


@contextmanager
def bind_candidate(
    *,
    candidate_id: str,
    cycle_started_at: str,
    input_cutoff_ts: str,
    reference_price_ts: str | None = None,
) -> Iterator[L2CandidateContext]:
    """Den Kontext für die Dauer eines Blocks setzen und danach exakt zurücksetzen.

    ``ContextVar`` statt Modulvariable: Der Loop kann nebenläufig laufen, und ein
    Token-Reset stellt auch bei einer Ausnahme den vorherigen Zustand her — ein
    Kandidat darf niemals in die Messung eines anderen Zyklus lecken.
    """
    context = L2CandidateContext(
        candidate_id=candidate_id,
        cycle_started_at=cycle_started_at,
        input_cutoff_ts=input_cutoff_ts,
        reference_price_ts=reference_price_ts,
    )
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)
