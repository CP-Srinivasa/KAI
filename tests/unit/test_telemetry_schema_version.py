"""Eine Zeile muss sagen, was sie ist — und der Leser muss sie annehmen.

Der Schreiber stempelte ``"schema_version": "v2"`` auf jede Zeile, während sie
längst v3- (``evaluation_id``), v4- (``use_case``, ``escalation_reason``,
``source``) und v5-Felder (``reasoning_tokens``, ``cost_reasoning_usd``,
``finish_reason``, ``empty_reason``, ``transport_retries``) trug. Jede
Erweiterung war additiv und hat den Wert nicht mitgezogen. Die Zeile beschrieb
sich selbst falsch, und wer auf die Version sah, suchte die neuen Felder gar
nicht erst.

Der Bump allein hätte es schlimmer gemacht: ``SUPPORTED_SCHEMA_VERSIONS`` im
S6-Harness kannte nur ``v1`` und ``v2``. Jede neue Zeile wäre als
``UNKNOWN_SCHEMA_VERSION`` verworfen worden — ausgerechnet im Werkzeug, das die
SHADOW-Evidenz auswerten soll. Schreiber und Leser gehören deshalb in denselben
Schritt, und diese Datei hält beide Seiten zusammen fest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.litellm_shadow_eval.loader import SUPPORTED_SCHEMA_VERSIONS

from app.observability.llm_telemetry import SCHEMA_VERSION, record_llm_call


def _zeile(tmp_path: Path) -> dict[str, Any]:
    sink = tmp_path / "telemetry.jsonl"
    record_llm_call(
        provider="gemini",
        model="gemini/gemini-2.5-flash",
        ok=True,
        latency_ms=696.0,
        path=sink,
        reasoning_tokens=382,
        cost_reasoning_usd=0.000955,
        finish_reason="stop",
        transport_retries=0,
    )
    return json.loads(sink.read_text(encoding="utf-8").strip())


def test_die_zeile_nennt_die_version_die_sie_traegt(tmp_path: Path) -> None:
    """Sonst sucht ein Leser Felder nicht, die dastehen."""
    zeile = _zeile(tmp_path)

    # Der wörtliche Wert steht hier mit Absicht: er ist die Stolperstelle, an
    # der ein Formatwechsel ankommt. Genau dieser Test hat den v6-Bump gemeldet.
    # Im SCHREIBER dagegen waere ein Literal der Fehler -- dort ist es zur
    # Konstante geworden, weil es sonst wieder stehen bleibt.
    assert zeile["schema_version"] == "v8"
    assert zeile["reasoning_tokens"] == 382, "die v5-Felder sind auch wirklich da"
    assert zeile["transport_retries"] == 0
    assert "truncated" in zeile, "und das v6-Feld"
    assert "analysis_system_prompt_version" in zeile, "und die v7-Felder"
    assert "analysis_system_prompt_hash" in zeile
    assert "budget_pot" in zeile, "und das v8-Feld"


def test_der_stempel_kommt_aus_einer_konstante(tmp_path: Path) -> None:
    """Zwei Orte für dieselbe Version wären zwei Wahrheiten.

    Genau so ist der Wert stehengeblieben: als Zeichenkette mitten im
    Zeilenaufbau, weit weg von den Feldern, die dazukamen.
    """
    assert _zeile(tmp_path)["schema_version"] == SCHEMA_VERSION


def test_der_leser_nimmt_die_neue_version_an() -> None:
    """Ein Bump ohne diese Zeile hätte jede neue Zeile verworfen.

    Und zwar im S6-Graduation-Harness, also dort, wo die SHADOW-Evidenz
    ausgewertet wird — der Schaden wäre still gewesen: keine Rohdaten fehlen,
    nur jede Zeile trägt eine Beanstandung.
    """
    assert SCHEMA_VERSION in SUPPORTED_SCHEMA_VERSIONS


def test_alte_zeilen_bleiben_lesbar() -> None:
    """Die vorhandenen Daten sind echte v2-Zeilen und bleiben es.

    Additiv heißt, dass ein v2-Leser eine v5-Zeile verarbeiten kann — nicht,
    dass sie dasselbe sind. Auf kai-pi5 liegen mehrere Megabyte v2.
    """
    for alt in ("v1", "v2", "v5", "v6", "v7"):
        assert alt in SUPPORTED_SCHEMA_VERSIONS, alt


def test_eine_unbekannte_version_bleibt_unbekannt() -> None:
    """Die Gegenprobe: der Leser nimmt nicht einfach alles.

    Die Menge ist bewusst eine Aufzählung und kein Präfix-Vergleich. Eine
    künftige Version soll hier ANKOMMEN, nicht stillschweigend durchrutschen —
    wer das Format ändert, sieht dann diese Stelle und entscheidet bewusst. Das
    hat bisher zweimal funktioniert: bei v6 und bei v8.
    """
    assert "v9" not in SUPPORTED_SCHEMA_VERSIONS
    assert "v99" not in SUPPORTED_SCHEMA_VERSIONS


def test_eine_zeile_ohne_neue_felder_bleibt_gueltig(tmp_path: Path) -> None:
    """Ein Aufrufer, der die v5-Argumente nicht kennt, schreibt weiter.

    Die Felder stehen dann auf ``None`` — unbekannt, nicht null. Die Version
    beschreibt das FORMAT der Zeile, nicht die Vollständigkeit ihrer Messwerte.
    """
    sink = tmp_path / "alt.jsonl"
    record_llm_call(provider="openai", model="gpt-4o", ok=True, latency_ms=1.0, path=sink)

    zeile = json.loads(sink.read_text(encoding="utf-8").strip())

    assert zeile["schema_version"] == "v8"
    assert zeile["reasoning_tokens"] is None
    assert zeile["transport_retries"] is None
    assert zeile["truncated"] is None, "kein finish_reason gemeldet = unbekannt, nicht False"
    assert zeile["analysis_system_prompt_version"] is None, "kein stilles v1-Defaulting"
    assert zeile["analysis_system_prompt_hash"] is None
    assert zeile["budget_pot"] is None, "keine Budgetentscheidung = nicht zugeordnet"
