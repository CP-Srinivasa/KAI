"""Der Bericht liest, was KAI schreibt -- und haengt nicht an der Produktion.

Das Paket importiert ``app`` nicht (wie ``scripts/litellm_shadow_eval``). Wo es
deshalb etwas aus ``app`` spiegeln muss -- Routenkatalog, Zweck-Zuordnung,
Kettenregel, Schemaversionen --, haelt ein Test hier beide Seiten gleich.
Passt etwas nicht, wird der LESER angepasst, nie der Schreiber.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from scripts.litellm_route_report.cli import main
from scripts.litellm_route_report.engine import (
    CATALOGUE_ROUTES,
    KNOWN_TELEMETRY_SCHEMAS,
    PURPOSE_ROUTE,
    build_report,
)

from app.ai.audit import Purpose, record_attempt_trace
from app.ai.models import AttemptTrace
from app.ai.routes import ROUTES, route_for
from app.ai.spend import dedupe_chain_levels
from app.observability.llm_telemetry import SCHEMA_VERSION, record_llm_call
from tests.unit.litellm_route_report.helpers import (
    NOW,
    TRANSPORT_LOG,
    write_jsonl,
    write_text,
    zeile,
)

PAKET = Path("scripts/litellm_route_report")


def test_routenkatalog_ist_der_von_app_ai_routes() -> None:
    assert tuple(CATALOGUE_ROUTES) == tuple(ROUTES)


def test_zweck_zuordnung_ist_die_von_route_for() -> None:
    assert set(PURPOSE_ROUTE) == set(get_args(Purpose))
    for zweck in get_args(Purpose):
        assert PURPOSE_ROUTE[zweck] == route_for(zweck), zweck


def test_der_leser_kennt_die_version_des_schreibers() -> None:
    """Ein Bump des Schreibers soll HIER ankommen, nicht still durchrutschen."""
    assert SCHEMA_VERSION in KNOWN_TELEMETRY_SCHEMAS


def test_kettenregel_ist_die_von_app_ai_spend(tmp_path: Path) -> None:
    """Ausserhalb des Altpaar-Fensters (07.-14.09.) gilt nur die Hauptregel."""
    ts = "2026-09-20T10:00:00+00:00"
    varianten: list[tuple[str | None, object]] = [
        ("k1", -1),
        ("k1", 0),
        ("k2", -1),
        (None, -1),
        (None, "kaputt"),
        ("k3", 1),
    ]
    rows = [
        zeile(ts=ts, correlation_id=kette, chain_position=ebene, call_id=f"c{nummer}")
        for nummer, (kette, ebene) in enumerate(varianten)
    ]
    ohne_ebene = zeile(ts=ts, correlation_id="k4", call_id="c-ohne")
    del ohne_ebene["chain_position"]
    rows.append(ohne_ebene)
    erwartet = dedupe_chain_levels(rows)
    assert len(erwartet) == len(rows) - 1, "nur die Huelle von k1 entfaellt"

    bericht = build_report(telemetry=[write_jsonl(tmp_path / "t.jsonl", rows)], now=NOW)
    eingang = bericht.to_dict()["inputs"]["telemetry"]
    assert eingang["rows_used"] == len(erwartet)
    assert eingang["outer_chain_rows_dropped"] == len(rows) - len(erwartet)


def test_der_leser_verdaut_was_record_attempt_trace_schreibt(tmp_path: Path) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    record_attempt_trace(
        AttemptTrace(
            transport="litellm",
            requested_model="kai-standard",
            latency_ms=321.0,
            actual_provider="openai",
            actual_model="gpt-4o-mini",
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.0012,
            request_id="req-1",
        ),
        correlation_id="corr-echt",
        purpose="analysis",
        logical_route="standard",
        mode="shadow",
        role="shadow",
        attempt_number=1,
        budget_decision="allow",
        circuit_state="closed",
        execution_authority=False,
        schema_status="valid",
        outcome="success",
        path=pfad,
    )
    log = write_text(tmp_path / "litellm.err.log", TRANSPORT_LOG)
    bericht = build_report(telemetry=[pfad], transport_log=log, now=datetime.now(UTC))
    litellm = bericht.to_dict()["routes"]["standard"]["transports"]["litellm"]

    assert litellm["status"] == "BELEGT", litellm["missing"]
    assert litellm["identity"]["actual"] == {"openai/gpt-4o-mini": 1}
    assert litellm["cost"]["sum_known_usd"] == 0.0012
    assert litellm["cost"]["sources"] == {"upstream": 1}
    assert litellm["failure"]["circuit_states"] == {"closed": 1}
    assert litellm["modes"] == {"shadow": 1}


def test_der_leser_verdaut_die_direktzeile_des_altpfads(tmp_path: Path) -> None:
    """``llm_call_scope`` im OFF-Modus: keine Route, kein Circuit, keine Identitaet."""
    pfad = tmp_path / "llm_telemetry.jsonl"
    record_llm_call(
        provider="openai",
        model="gpt-4o-mini",
        ok=True,
        latency_ms=50.0,
        path=pfad,
        correlation_id="corr-direkt",
        purpose="analysis",
        prompt_tokens=100,
        completion_tokens=20,
        outcome="success",
    )
    bericht = build_report(telemetry=[pfad], now=datetime.now(UTC)).to_dict()
    direkt = bericht["routes"]["standard"]["transports"]["direct"]

    assert direkt["calls"] == 1
    assert direkt["route_from_purpose"] == 1
    assert direkt["identity"]["proven"] == 0
    assert direkt["identity"]["unproven_claimed"] == {"openai/gpt-4o-mini": 1}
    assert direkt["failure"]["retry_unknown"] == 0
    assert direkt["failure"]["fallback_unknown"] == 0
    assert "direct:identitaet" in bericht["routes"]["standard"]["missing"]


def test_das_paket_geht_nicht_ins_netz_und_haengt_nicht_an_app() -> None:
    verboten = {"httpx", "requests", "urllib", "socket", "http", "aiohttp", "openai", "litellm"}
    dateien = sorted(PAKET.rglob("*.py"))
    assert dateien
    for datei in dateien:
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            namen: list[str] = []
            if isinstance(knoten, ast.Import):
                namen = [alias.name for alias in knoten.names]
            elif isinstance(knoten, ast.ImportFrom) and knoten.module:
                namen = [knoten.module]
            for name in namen:
                assert name.split(".")[0] not in verboten, (datei.name, name)
                assert not name.startswith("app"), (datei.name, name)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def test_cli_schreibt_json_und_markdown(tmp_path: Path) -> None:
    telemetry = write_jsonl(tmp_path / "t.jsonl", [zeile()])
    log = write_text(tmp_path / "litellm.err.log", TRANSPORT_LOG)
    json_out = tmp_path / "out" / "route_report.json"
    md_out = tmp_path / "out" / "route_report.md"

    code = main(
        [
            "--telemetry",
            str(telemetry),
            "--transport-log",
            str(log),
            "--now",
            NOW.isoformat(),
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
        ]
    )

    assert code == 0
    daten = json.loads(json_out.read_text(encoding="utf-8"))
    assert daten["generated_at"] == NOW.isoformat()
    assert daten["routes"]["standard"]["status"] == "BELEGT"
    assert b"\r\n" not in md_out.read_bytes(), "LF, plattformunabhaengig"
    assert "- BELEGT · standard" in md_out.read_text(encoding="utf-8")


def test_cli_verlangt_zeitzone_im_zeitpunkt(tmp_path: Path) -> None:
    telemetry = write_jsonl(tmp_path / "t.jsonl", [zeile()])
    code = main(
        [
            "--telemetry",
            str(telemetry),
            "--now",
            "2026-09-27T12:00:00",
            "--json-out",
            str(tmp_path / "a.json"),
            "--md-out",
            str(tmp_path / "a.md"),
        ]
    )
    assert code == 2


def test_cli_meldet_eingabefehler_mit_exit_2(tmp_path: Path) -> None:
    code = main(
        [
            "--telemetry",
            str(tmp_path / "fehlt.jsonl"),
            "--json-out",
            str(tmp_path / "a.json"),
            "--md-out",
            str(tmp_path / "a.md"),
        ]
    )
    assert code == 2
    assert not (tmp_path / "a.json").exists()
