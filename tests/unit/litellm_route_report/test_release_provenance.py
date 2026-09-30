"""Release- und Zeitstempel-Provenienz im Routenbericht (Nachtrag LiteLLM-Audit, 30.09.).

Bis hierher stand die Release-SHA jeder Route auf ``null`` mit dem Grund, dass keine
Eingabe sie traegt, und eine ``TRANSPORT_VERIFIED``-Zeile belegte einen Baum, aber keinen
Zeitpunkt. Seit Telemetrie v9 traegt jede Zeile ``runtime_commit``; seit
``pi_transport_exec.sh`` einen Zeitstempel voranstellt, ist die Zeile datiert.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.litellm_route_report.engine import build_report

from tests.unit.litellm_route_report.helpers import NOW, write_jsonl, write_text, zeile

A = "a" * 40
B = "b" * 40

DATIERTES_LOG = (
    "2026-09-27T11:00:00Z TRANSPORT_VERIFIED name=litellm version=1.102.1 "
    "tree=/home/ubuntu/transport/litellm/1.102.1-0d33a313 manifest=0d33a3138d235252\n"
)


def _abschnitt(tmp_path: Path, rows: list[Any], log: str = DATIERTES_LOG) -> dict[str, Any]:
    telemetry = write_jsonl(tmp_path / "llm_telemetry.jsonl", rows)
    transport_log = write_text(tmp_path / "litellm.err.log", log)
    report = build_report(
        telemetry=[telemetry], transport_log=transport_log, now=NOW, max_age_hours=168.0
    ).to_dict()
    return report


def _zeilen(*commits: str | None) -> list[Any]:
    rows = []
    for nummer, commit in enumerate(commits):
        extra = {"schema_version": "v9", "runtime_commit": commit, "runtime_source": "release"}
        if commit is None:
            extra = {}
        rows.append(
            zeile(
                stunden_alt=float(len(commits) - nummer),  # die letzte ist die juengste
                correlation_id=f"corr-{nummer}",
                call_id=f"llmc_{nummer}",
                **extra,
            )
        )
    return rows


def test_die_release_sha_kommt_aus_der_telemetrie(tmp_path: Path) -> None:
    litellm = _abschnitt(tmp_path, _zeilen(A, A, A))["routes"]["standard"]["transports"]["litellm"]
    assert litellm["version"]["release_sha"] == A
    assert "version.release_sha" not in litellm["null_reasons"]


def test_bei_mehreren_releases_gilt_die_juengste_zeile_und_der_wechsel_steht_da(
    tmp_path: Path,
) -> None:
    litellm = _abschnitt(tmp_path, _zeilen(A, A, B))["routes"]["standard"]["transports"]["litellm"]
    assert litellm["version"]["release_sha"] == B
    assert "2 Releases" in litellm["version"]["release_source"]


def test_altzeilen_ohne_runtime_commit_bleiben_ehrlich_null(tmp_path: Path) -> None:
    litellm = _abschnitt(tmp_path, _zeilen(None, None))["routes"]["standard"]["transports"][
        "litellm"
    ]
    assert litellm["version"]["release_sha"] is None
    assert "v9" in litellm["null_reasons"]["version.release_sha"]


def test_teilweise_belegte_releases_werden_ausgewiesen(tmp_path: Path) -> None:
    litellm = _abschnitt(tmp_path, _zeilen(None, A))["routes"]["standard"]["transports"]["litellm"]
    assert litellm["version"]["release_sha"] == A
    assert "1 von 2" in litellm["version"]["release_source"]


def test_ein_ungueltiger_commit_wird_nicht_uebernommen(tmp_path: Path) -> None:
    rows = _zeilen(A)
    rows[0]["runtime_commit"] = "main"
    litellm = _abschnitt(tmp_path, rows)["routes"]["standard"]["transports"]["litellm"]
    assert litellm["version"]["release_sha"] is None


def test_auch_der_direktpfad_nennt_sein_release(tmp_path: Path) -> None:
    rows = _zeilen(A, A)
    for row in rows:
        row["transport"] = "direct"
    direkt = _abschnitt(tmp_path, rows)["routes"]["standard"]["transports"]["direct"]
    assert direkt["version"]["release_sha"] == A


def test_die_datierte_beleg_zeile_hat_ein_pruefdatum(tmp_path: Path) -> None:
    log = _abschnitt(tmp_path, _zeilen(A))["inputs"]["transport_log"]
    assert log["last"]["litellm"]["verified_at"] == "2026-09-27T11:00:00+00:00"
    assert "verified_at" not in log["last"]["litellm"]["null_reasons"]
