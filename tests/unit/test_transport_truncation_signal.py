"""Abgeschnitten ist weder leer noch ein Schemafehler — es ist ein eigener Zustand.

Am 2026-09-09 stand in einer echten Telemetriezeile von kai-pi5:
``finish_reason='length'``, ``reasoning_tokens=380``, ``ok=True``. Die Antwort
war abgeschnitten, weil das Denken das Ausgabebudget aufgebraucht hatte — und
sie zählte als Erfolg.

Die Aufteilung, die daraus folgt, ist eine Operator-Entscheidung und kein
Geschmack:

* **Transport**: ``finish_reason="length"`` → ``truncated=True``, sichtbar in
  der Zeile. Aber KEINE ``error_class``: der Aufruf war technisch erfolgreich.
* **Runtime**: verwirft auf ``truncated`` hin, vor dem Parser, mit einer
  Meldung, die ``max_tokens`` nennt.
* **Parser**: sieht nur vollständige Antworten — damit ``schema`` wieder
  wirklich Schema heißt.

Eine ``error_class`` an dieser Stelle hätte ``trace.ok`` auf ``False`` gesetzt
und den frühen Rückweg in ``app/ai/runtime.py`` ausgelöst. Die Diagnose mit
``max_tokens`` wäre damit unerreichbar geworden — dieselbe Falle, nur aus der
anderen Richtung.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ai.audit import record_attempt_trace
from app.integrations.litellm.provider import ist_abgeschnitten, trace_from_response

NEUZEILE = chr(10)


def _antwort(body: dict[str, Any], *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=body,
        request=httpx.Request("POST", "http://127.0.0.1:4000/v1/chat/completions"),
    )


def _koerper(*, inhalt: str | None, finish: str) -> dict[str, Any]:
    return {
        "model": "kai-standard",
        "choices": [{"message": {"content": inhalt}, "finish_reason": finish}],
    }


# ---------------------------------------------------------------------------
# Das Prädikat: eine Definition, zwei Aufrufer.
# ---------------------------------------------------------------------------


def test_das_praedikat_erkennt_die_abschneidung() -> None:
    assert ist_abgeschnitten(_koerper(inhalt="halb", finish="length")) is True
    assert ist_abgeschnitten(_koerper(inhalt="ganz", finish="stop")) is False


def test_das_praedikat_faellt_bei_kaputten_antworten_nicht_um() -> None:
    """Es wird auch von einem zweiten Aufrufer benutzt, der andere Körper sieht."""
    for kaputt in ({}, {"choices": []}, {"choices": "keine Liste"}, {"choices": [None]}):
        assert ist_abgeschnitten(kaputt) is False, kaputt


# ---------------------------------------------------------------------------
# Der Transport: sichtbar machen, nicht verwerfen.
# ---------------------------------------------------------------------------


def test_eine_abgeschnittene_antwort_bleibt_technisch_erfolgreich() -> None:
    """Sonst greift der frühe Rückweg und die Diagnose geht verloren.

    ``app/ai/runtime.py`` kehrt bei ``not trace.ok`` zurück, BEVOR der Parser
    läuft. Eine ``error_class`` hier hieße: die Meldung mit ``max_tokens``
    kommt nie im Journal an.
    """
    trace = trace_from_response(
        _antwort(_koerper(inhalt="Der groesste Risikofaktor", finish="length")),
        requested_model="kai-standard",
        latency_ms=3065.0,
        max_tokens=1024,
    )

    assert trace.truncated is True
    assert trace.ok, "der Transport war erfolgreich — unbrauchbar ist etwas anderes"
    assert trace.error_class is None
    assert trace.detail["max_tokens"] == 1024, "welcher Deckel gegriffen hat"


def test_eine_vollstaendige_antwort_ist_nicht_abgeschnitten() -> None:
    trace = trace_from_response(
        _antwort(_koerper(inhalt="fertig", finish="stop")),
        requested_model="kai-standard",
        latency_ms=696.0,
    )

    assert trace.truncated is False
    assert trace.ok


def test_ohne_finish_reason_ist_es_unbekannt_und_nicht_falsch() -> None:
    """`None` heißt unbekannt — `False` wäre eine erfundene Messung.

    Dieselbe Falle wie eine unbekannte Kostenangabe als 0: sie zieht jede
    Auswertung in die falsche Richtung, und niemand sieht, dass nie gemessen
    wurde.
    """
    trace = trace_from_response(
        _antwort({"model": "kai-standard", "choices": [{"message": {"content": "da"}}]}),
        requested_model="kai-standard",
        latency_ms=1.0,
    )

    assert trace.truncated is None


def test_abgeschnitten_und_leer_bleibt_leer() -> None:
    """Beide Signale, keins verdeckt das andere.

    Genau dieser Fall wurde am 2026-09-08 real gemessen: HTTP 200,
    ``content: null``, ``finish_reason='length'``. Ohne Inhalt gibt es nichts
    zu parsen — das muss im Transport fail-closed sein und darf nicht erst
    beim Aufrufer scheitern. Dass es zusätzlich abgeschnitten war, steht
    daneben und geht nicht verloren.
    """
    trace = trace_from_response(
        _antwort(_koerper(inhalt=None, finish="length")),
        requested_model="kai-bulk",
        latency_ms=1882.0,
        max_tokens=20,
    )

    assert trace.error_class == "empty", "ohne Inhalt bleibt es leer"
    assert not trace.ok
    assert trace.truncated is True, "und abgeschnitten war es auch"
    assert trace.detail["empty_reason"] == "empty_content_finish_length"


def test_ohne_max_tokens_wird_keiner_erfunden() -> None:
    """Der Deckel steht in der Nutzlast des Aufrufers, nicht in der Antwort."""
    trace = trace_from_response(
        _antwort(_koerper(inhalt="x", finish="length")),
        requested_model="kai-standard",
        latency_ms=1.0,
    )

    assert "max_tokens" not in trace.detail


# ---------------------------------------------------------------------------
# Die geschriebene Zeile.
# ---------------------------------------------------------------------------


def test_der_zustand_steht_in_der_telemetriezeile(tmp_path: Path) -> None:
    """`finish_reason` allein reicht nicht: ein Leser müsste wissen, dass
    "length" abgeschnitten bedeutet. Der Zustand gehört benannt."""
    sink = tmp_path / "telemetry.jsonl"
    trace = trace_from_response(
        _antwort(_koerper(inhalt="halb", finish="length")),
        requested_model="kai-standard",
        latency_ms=3065.0,
        max_tokens=4096,
    )
    record_attempt_trace(
        trace,
        correlation_id="req_1",
        purpose="analysis",
        logical_route="standard",
        mode="shadow",
        role="shadow",
        attempt_number=1,
        budget_decision="allow",
        circuit_state="closed",
        execution_authority=False,
        schema_status=None,
        outcome="success",
        path=sink,
    )

    zeile = json.loads(sink.read_text(encoding="utf-8").strip())

    assert zeile["truncated"] is True
    assert zeile["finish_reason"] == "length"
    assert zeile["ok"] is True, "Transport erfolgreich, Analyse unbrauchbar"
    assert zeile["error_class"] is None


def test_der_deckel_steht_mit_in_der_zeile(tmp_path: Path) -> None:
    """Sonst steht dort, DASS abgeschnitten wurde, aber nicht wogegen."""
    sink = tmp_path / "telemetry.jsonl"
    trace = trace_from_response(
        _antwort(_koerper(inhalt="halb", finish="length")),
        requested_model="kai-standard",
        latency_ms=1.0,
        max_tokens=1024,
    )
    record_attempt_trace(
        trace,
        correlation_id="req_1",
        purpose="analysis",
        logical_route="standard",
        mode="shadow",
        role="shadow",
        attempt_number=1,
        budget_decision="allow",
        circuit_state="closed",
        execution_authority=False,
        schema_status=None,
        outcome="success",
        path=sink,
    )

    zeile = json.loads(sink.read_text(encoding="utf-8").strip())

    # `detail` reist NICHT als Ganzes in die Zeile -- der Schreiber pickt
    # Felder einzeln heraus. Genau daran ist in #931 die Aufschluesselung
    # verloren gegangen; der Deckel darf denselben Weg nicht nehmen.
    assert zeile["max_tokens"] == 1024
    assert zeile["truncated"] is True


@pytest.mark.parametrize("finish", ["stop", "content_filter", "tool_calls"])
def test_andere_gruende_gelten_nicht_als_abgeschnitten(finish: str) -> None:
    """Eine Wache, die jeden Grund als Abschneidung liest, verwirft alles."""
    trace = trace_from_response(
        _antwort(_koerper(inhalt="da", finish=finish)),
        requested_model="kai-standard",
        latency_ms=1.0,
    )

    assert trace.truncated is False
