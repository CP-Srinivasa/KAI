"""Was der Transport weiß, muss auch in der Datei stehen.

Am 2026-09-09 im echten Lauf auf kai-pi5 aufgefallen: die Telemetriezeile trug
Kosten und Modell, aber nicht die Aufschlüsselung, aus der man sie versteht.
``AttemptTrace.detail`` war korrekt gefüllt — Unit-Tests belegten das — und
``record_attempt_trace`` reichte es nie weiter. Der Schreiber pickt Felder
einzeln heraus, und der Rest fiel still zu Boden.

Dieselbe Klasse wie mehrere Befunde dieser Woche: der Baustein stimmte, die
Verdrahtung nicht, und kein Test sah dazwischen. Diese Datei prüft deshalb die
**geschriebene Zeile**, nicht den Aufruf.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.ai.audit import record_attempt_trace
from app.ai.models import AttemptTrace


def _schreibe(tmp_path: Path, detail: dict[str, Any]) -> dict[str, Any]:
    sink = tmp_path / "telemetry.jsonl"
    record_attempt_trace(
        AttemptTrace(
            transport="litellm",
            requested_model="kai-bulk",
            latency_ms=645.0,
            actual_provider="gemini",
            actual_model="gemini/gemini-2.5-flash",
            input_tokens=19,
            output_tokens=396,
            cost_usd=0.0009957,
            detail=detail,
        ),
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
    return json.loads(sink.read_text(encoding="utf-8").strip())


def test_die_kostenaufschluesselung_steht_in_der_zeile(tmp_path: Path) -> None:
    """Ohne sie sieht eine Aggregation eine Summe und keinen Hebel.

    Bei denkenden Modellen liegt der Preis im Weg zur Antwort: von 0,0009957 USD
    entfielen auf kai-pi5 0,000715 auf ``reasoning``. Wer nur die Summe hat,
    kann nicht erkennen, dass ein knapperes Denkbudget fast alles davon spart.
    """
    zeile = _schreibe(tmp_path, {"reasoning_tokens": 381, "cost_reasoning_usd": 0.000715})

    assert zeile["reasoning_tokens"] == 381
    assert zeile["cost_reasoning_usd"] == 0.000715


def test_der_grund_einer_leeren_antwort_ueberlebt(tmp_path: Path) -> None:
    """``error_class="empty"`` ohne Grund wäre die halbe Nachricht.

    Die Unterscheidung zwischen "abgeschnitten" und "verstummt" ist der ganze
    Zweck dieser Klasse — sie verlangt verschiedene nächste Schritte. Ging sie
    beim Schreiben verloren, stand in der Datei nur, dass nichts kam.
    """
    zeile = _schreibe(
        tmp_path,
        {"empty_reason": "empty_content_finish_length", "finish_reason": "length"},
    )

    assert zeile["empty_reason"] == "empty_content_finish_length"
    assert zeile["finish_reason"] == "length"


def test_eine_wiederholung_im_transport_steht_als_zahl_da(tmp_path: Path) -> None:
    """Der Header liefert eine Zeichenkette; die Zeile soll eine Zahl tragen.

    Sonst müsste jeder Leser sie erst deuten — und ein Leser, der es vergisst,
    vergleicht ``"2"`` mit ``0`` und findet nie etwas.
    """
    zeile = _schreibe(tmp_path, {"transport_retries": "2"})

    assert zeile["transport_retries"] == 2


def test_ohne_angaben_stehen_die_felder_auf_none(tmp_path: Path) -> None:
    """Nicht 0 — unbekannt ist nicht null.

    Ein ``reasoning_tokens: 0`` für einen Aufruf, der nie danach gefragt wurde,
    behauptete eine Messung, die es nicht gab, und zöge jeden Durchschnitt nach
    unten.
    """
    zeile = _schreibe(tmp_path, {"status_code": 200})

    for feld in (
        "reasoning_tokens",
        "cost_reasoning_usd",
        "finish_reason",
        "empty_reason",
        "transport_retries",
    ):
        assert zeile[feld] is None, feld


def test_unbrauchbare_werte_werden_nicht_geraten(tmp_path: Path) -> None:
    """Ein Wert, der keine Zahl ist, wird ``None`` — nicht 0 und nicht geschätzt."""
    zeile = _schreibe(
        tmp_path,
        {
            "reasoning_tokens": "keine Zahl",
            "cost_reasoning_usd": "teuer",
            "finish_reason": "",
            "transport_retries": True,
        },
    )

    assert zeile["reasoning_tokens"] is None
    assert zeile["cost_reasoning_usd"] is None
    assert zeile["finish_reason"] is None
    assert zeile["transport_retries"] is None, "bool ist keine Anzahl"


def test_die_bestehenden_felder_bleiben_unveraendert(tmp_path: Path) -> None:
    """Additiv heißt additiv: kein Leser der v4-Felder darf etwas merken."""
    zeile = _schreibe(tmp_path, {"status_code": 200, "reasoning_tokens": 381})

    assert zeile["actual_model"] == "gemini/gemini-2.5-flash"
    assert zeile["actual_provider"] == "gemini"
    assert zeile["cost_usd"] == 0.0009957
    assert zeile["completion_tokens"] == 396
    assert zeile["mode"] == "shadow"
    assert zeile["role"] == "shadow"
