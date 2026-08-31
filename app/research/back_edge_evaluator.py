"""Evaluator der Praereg ``operator_back_edge_v1`` (G8, KMA-20260827).

**Die Frage.** KAI hat 19 Hypothesen praeregistriert und die eine nie, an der
alles haengt: ob es die Entscheidungen seines einzigen Nutzers messbar
veraendert. 141 Stroeme messen KAI, null messen seinen Nutzen (R2-07, R2-25).

**Warum ein Evaluator und keine Auswertung von Hand.** Am 01.07. entstand ein
Verdikt-Fehler aus freihaendiger Auslegung; seither gilt: die Regel steht VOR
den Daten, sie ist committet, und sie hat eine Positivkontrolle. Dieses Modul
ist diese Regel. Es faellt sie mechanisch und ohne Ermessen.

**Die Regel (versiegelt, nicht nachverhandelbar).**

* **Population:** jeder Health-Befund, der im Fenster **zugestellt** wurde und
  dabei einen ``alert_emitted``-Satz mit Auslöser-ID hinterlassen hat.
  Zugestellt, nicht erzeugt — ein Alarm, den niemand bekam, kann keine
  Handlung ausloesen und darf den Nenner nicht aufblaehen (A4-017: 15 von 19
  Alarmen kamen nie an).
* **Treffer:** eine Operator-Handlung, die **dieselbe** Auslöser-ID traegt und
  **nach** dem Aussenden liegt, innerhalb von ``REACTION_WINDOW_HOURS``.
  Identitaet UND Reihenfolge — eine Handlung ohne ID ist eine Vermutung, eine
  davor ist keine Reaktion.
* **Verdikt:** ``MET`` bei ``acted >= MIN_ACTED`` **und** ``emitted >=
  MIN_EMITTED``; ``NOT_MET`` bei ``acted < MIN_ACTED`` und ``emitted >=
  MIN_EMITTED``; sonst ``INVALID`` — zu wenige Befunde heissen, dass die Frage
  im Fenster nicht gestellt werden konnte, nicht dass sie beantwortet waere.

**Was dieses Modul ausdruecklich NICHT behauptet.** Es misst **Reaktion**, nicht
**Verbesserung**. Ob eine Handlung die Entscheidung besser gemacht hat, sagt
keine Zahl hier — die Rueckkante ist die notwendige, nicht die hinreichende
Bedingung. Und ``INVALID`` ist ein moegliches Ergebnis: eine Messung, die nicht
scheitern kann, misst nichts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.operator_feedback import (
    OPERATOR_ACTION_STREAM,
    REACTION_WINDOW_HOURS,
    correlate,
    emitted_triggers,
    is_trigger_id,
    load_jsonl,
    summarise,
)

#: Praereg-Kennung; wird beim Versiegeln im Ledger vergeben.
PREREG_NAME = "operator_back_edge_v1"

#: Untergrenze der Population. Unter fuenf zugestellten Befunden ist das
#: Fenster keine Probe, sondern ein Zufall — dann lautet das Verdikt INVALID
#: und das Fenster wird neu angesetzt, nicht interpretiert.
MIN_EMITTED = 5

#: Schwelle S. **Eine** protokollierte Reaktion genuegt fuer MET.
#:
#: Bewusst so niedrig: gemessen wurden ueber 5,3 Monate 643 schreibende
#: Operator-Handlungen, in den letzten 90 Tagen 44 — und in den letzten 30
#: Tagen **null**. Gegen eine Ausgangslage von null ist schon die Eins ein
#: Unterschied. Eine hoehere Schwelle wuerde die Frage „veraendert KAI
#: ueberhaupt etwas" durch die Frage „wie oft" ersetzen, bevor die erste
#: beantwortet ist.
MIN_ACTED = 1


@dataclass(frozen=True)
class BackEdgeEvaluation:
    """Maschinenlesbares Verdikt — die einzige zitierfaehige Form."""

    prereg: str
    verdict: str  # "MET" | "NOT_MET" | "INVALID"
    reason: str
    window_start_utc: str
    window_end_utc: str
    emitted: int
    acted: int
    action_rate: float | None
    median_latency_minutes: float | None
    by_channel: tuple[tuple[str, int], ...]
    unanswered: tuple[str, ...]
    measured_at_utc: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def _action_records(paths: list[Path]) -> list[dict[str, Any]]:
    """Handlungen aus allen Quellen, die eine Auslöser-ID tragen koennen.

    Zwei Quellen, weil der Operator zwei Wege hat: der Dashboard-Klick landet
    im Request-Audit, ein Kommando im Operator-Strom. Wer nur eine Quelle
    liest, misst den Kanal statt die Handlung.
    """
    out: list[dict[str, Any]] = []
    for path in paths:
        for rec in load_jsonl(path):
            if rec.get("record_type") == "alert_emitted":
                continue  # das ist die linke Seite, keine Handlung
            trigger = rec.get("trigger_id")
            if not (isinstance(trigger, str) and is_trigger_id(trigger)):
                continue
            channel = rec.get("channel")
            if not isinstance(channel, str) or not channel:
                rec = {**rec, "channel": "dashboard" if "path" in rec else "unknown"}
            out.append(rec)
    return out


def evaluate_back_edge(
    artifacts_dir: Path,
    *,
    window_start: datetime,
    window_end: datetime,
    now: datetime | None = None,
    min_emitted: int = MIN_EMITTED,
    min_acted: int = MIN_ACTED,
    window_hours: int = REACTION_WINDOW_HOURS,
) -> BackEdgeEvaluation:
    """Faelle das Verdikt mechanisch. Wirft nie — ein Fehler ist ein Verdikt."""
    measured_at = (now or datetime.now(UTC)).astimezone(UTC)
    operator_stream = artifacts_dir / OPERATOR_ACTION_STREAM
    request_stream = artifacts_dir / "api_request_audit.jsonl"

    emitted_all = emitted_triggers(load_jsonl(operator_stream))
    emitted = [(trigger, ts) for trigger, ts in emitted_all if window_start <= ts <= window_end]
    actions = _action_records([operator_stream, request_stream])
    verdict_data = summarise(correlate(emitted, actions, window_hours=window_hours))

    if verdict_data.emitted < min_emitted:
        verdict, reason = (
            "INVALID",
            f"nur {verdict_data.emitted} zugestellte Befunde im Fenster "
            f"(Untergrenze {min_emitted}) — das Fenster ist keine Probe; "
            "es wird neu angesetzt, nicht interpretiert",
        )
    elif verdict_data.acted >= min_acted:
        verdict, reason = (
            "MET",
            f"{verdict_data.acted} von {verdict_data.emitted} zugestellten Befunden "
            f"loesten eine protokollierte Handlung mit derselben Auslöser-ID aus "
            f"(Schwelle {min_acted})",
        )
    else:
        verdict, reason = (
            "NOT_MET",
            f"{verdict_data.emitted} Befunde zugestellt, {verdict_data.acted} Handlungen "
            f"mit Auslöser-ID (Schwelle {min_acted}) — die Rueckkante bleibt geschlossen",
        )

    return BackEdgeEvaluation(
        prereg=PREREG_NAME,
        verdict=verdict,
        reason=reason,
        window_start_utc=window_start.astimezone(UTC).isoformat(),
        window_end_utc=window_end.astimezone(UTC).isoformat(),
        emitted=verdict_data.emitted,
        acted=verdict_data.acted,
        action_rate=verdict_data.action_rate,
        median_latency_minutes=verdict_data.median_latency_minutes,
        by_channel=verdict_data.by_channel,
        unanswered=verdict_data.unanswered,
        measured_at_utc=measured_at.isoformat(),
    )
