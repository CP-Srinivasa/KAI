"""Routen-Abnahmebericht: jede genutzte Route mit Beleg -- oder mit Grund, warum nicht.

Die Tests arbeiten mit synthetischen Telemetriezeilen im Format des echten
Schreibers. Dass der Leser auch die ECHTE Zeile versteht, prueft
``test_contracts.py``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from scripts.litellm_route_report.engine import InputError, build_report
from scripts.litellm_route_report.reporting import canonical_json, markdown_summary

from tests.unit.litellm_route_report.helpers import (
    NOW,
    TRANSPORT_LOG,
    eval_report_v1,
    eval_report_v2,
    write_json,
    write_jsonl,
    write_text,
    zeile,
)


def _bericht(
    tmp_path: Path,
    rows: list[object],
    *,
    transport_log: str | None = TRANSPORT_LOG,
    eval_report: dict[str, Any] | None = None,
    max_age_hours: float = 168.0,
) -> dict[str, Any]:
    telemetry = write_jsonl(tmp_path / "llm_telemetry.jsonl", rows)
    log = write_text(tmp_path / "litellm.err.log", transport_log) if transport_log else None
    evaluation = write_json(tmp_path / "eval.json", eval_report) if eval_report else None
    report = build_report(
        telemetry=[telemetry],
        eval_report=evaluation,
        transport_log=log,
        now=NOW,
        max_age_hours=max_age_hours,
    )
    return report.to_dict()


def _vollstaendig(anzahl: int = 3) -> list[object]:
    return [
        zeile(
            stunden_alt=float(1 + nummer),
            correlation_id=f"corr-{nummer}",
            call_id=f"llmc_{nummer}",
            cost_usd=0.001 * (nummer + 1),
        )
        for nummer in range(anzahl)
    ]


# ---------------------------------------------------------------------------
# Die vier Zustaende.
# ---------------------------------------------------------------------------


def test_vollstaendig_belegte_route_ist_belegt_mit_allen_sechs_feldern(tmp_path: Path) -> None:
    bericht = _bericht(tmp_path, _vollstaendig())
    route = bericht["routes"]["standard"]
    litellm = route["transports"]["litellm"]

    assert route["status"] == "BELEGT", route["missing"]
    assert route["missing"] == []
    assert litellm["status"] == "BELEGT"
    assert litellm["calls"] == 3

    # Version: die letzte TRANSPORT_VERIFIED-Zeile gewinnt.
    version = litellm["version"]
    assert version["transport_version"] == "1.99.0"
    assert version["transport_manifest"] == "4e65cd3189abcdef"
    assert version["transport_tree"] == "/home/kai/transport/litellm/1.99.0-4e65cd31"
    assert version["release_sha"] is None
    assert litellm["null_reasons"]["version.release_sha"]

    # Letzter Erfolg.
    assert litellm["last_success_at"] == (NOW - timedelta(hours=1)).isoformat()

    # Identitaet.
    identitaet = litellm["identity"]
    assert identitaet["proven"] == 3
    assert identitaet["share"] == 1.0
    assert identitaet["successes_unproven"] == 0
    assert identitaet["actual"] == {"openai/gpt-4o-mini": 3}

    # Kosten.
    kosten = litellm["cost"]
    assert kosten["known"] == 3 and kosten["unknown"] == 0
    assert kosten["sum_known_usd"] == pytest.approx(0.006)
    assert kosten["sum_complete"] is True
    assert kosten["median_usd"] == pytest.approx(0.002)

    # Ausfallverhalten.
    ausfall = litellm["failure"]
    assert ausfall["failures"] == 0
    assert ausfall["error_classes"] == {}
    assert ausfall["retry_rows"] == 0 and ausfall["retry_unknown"] == 0
    assert ausfall["fallback_rate"] == 0.0 and ausfall["fallback_unknown"] == 0
    assert ausfall["circuit_states"] == {"closed": 3}

    # Evidenzalter gegen den injizierten Zeitpunkt.
    alter = litellm["evidence_age"]
    assert alter["newest_age_hours"] == 1.0
    assert alter["oldest_age_hours"] == 3.0
    assert alter["stale"] is False

    # Der andere Transport derselben Route ist sichtbar -- und leer.
    assert route["transports"]["direct"]["status"] == "KEINE_EVIDENZ"
    assert route["transports"]["direct"]["calls"] == 0


def test_lueckenhafte_route_nennt_die_fehlenden_felder_und_erfindet_keine_null(
    tmp_path: Path,
) -> None:
    """Kosten unbekannt, Identitaet unbelegt -- beides steht da, nichts ist 0."""
    rows = [
        zeile(
            logical_route="critical",
            purpose="intent",
            correlation_id=f"corr-{nummer}",
            identity_proven=False,
            actual_provider=None,
            actual_model=None,
            cost_usd=None,
            cost_known=False,
            cost_source=None,
            cost_status="COST_UNKNOWN",
        )
        for nummer in range(2)
    ]
    route = _bericht(tmp_path, rows)["routes"]["critical"]
    litellm = route["transports"]["litellm"]

    assert route["status"] == "LUECKENHAFT"
    assert "litellm:kosten" in route["missing"]
    assert "litellm:identitaet" in route["missing"]
    assert litellm["status"] == "LUECKENHAFT"

    kosten = litellm["cost"]
    assert kosten["known"] == 0 and kosten["unknown"] == 2
    assert kosten["sum_known_usd"] is None, "unbekannt ist nicht 0"
    assert kosten["median_usd"] is None
    assert kosten["sum_complete"] is False
    assert litellm["null_reasons"]["cost.sum_known_usd"]
    assert litellm["null_reasons"]["cost.median_usd"]

    identitaet = litellm["identity"]
    assert identitaet["proven"] == 0
    assert identitaet["share"] == 0.0
    assert identitaet["successes_unproven"] == 2
    assert identitaet["actual"] == {}
    # Was konfiguriert/angefordert war, steht getrennt und als unbelegt da.
    assert identitaet["unproven_claimed"] == {"openai/gpt-4o-mini": 2}


def test_route_ohne_evidenz_ist_keine_evidenz_mit_gruenden(tmp_path: Path) -> None:
    """Eine Katalogroute ohne Aufruf wird gefuehrt, nicht weggelassen."""
    bericht = _bericht(tmp_path, _vollstaendig())
    route = bericht["routes"]["bulk"]

    assert route["status"] == "KEINE_EVIDENZ"
    assert route["missing"] == []
    for transport in ("litellm", "direct"):
        abschnitt = route["transports"][transport]
        assert abschnitt["status"] == "KEINE_EVIDENZ"
        assert abschnitt["calls"] == 0
        assert abschnitt["last_success_at"] is None
        assert abschnitt["null_reasons"]["last_success_at"]
        assert abschnitt["cost"]["sum_known_usd"] is None
        assert abschnitt["identity"]["share"] is None
        assert abschnitt["evidence_age"]["newest_age_hours"] is None
    # Jede Katalogroute ist im Bericht.
    assert set(bericht["routes"]) >= {
        "bulk",
        "standard",
        "reasoning",
        "critical",
        "stt",
        "research",
    }
    assert bericht["status_counts"]["KEINE_EVIDENZ"] == 5


def test_veraltete_evidenz_macht_die_route_lueckenhaft(tmp_path: Path) -> None:
    rows = [zeile(stunden_alt=200.0), zeile(stunden_alt=250.0, correlation_id="corr-2")]
    route = _bericht(tmp_path, rows, max_age_hours=168.0)["routes"]["standard"]
    alter = route["transports"]["litellm"]["evidence_age"]

    assert route["status"] == "LUECKENHAFT"
    assert route["missing"] == ["litellm:evidenzalter"]
    assert alter["stale"] is True
    assert alter["newest_age_hours"] == 200.0
    assert alter["oldest_age_hours"] == 250.0
    assert alter["newest_at"] == (NOW - timedelta(hours=200)).isoformat()


def test_das_evidenzalter_folgt_dem_injizierten_zeitpunkt(tmp_path: Path) -> None:
    telemetry = write_jsonl(tmp_path / "t.jsonl", _vollstaendig(1))
    frueh = build_report(telemetry=[telemetry], now=NOW, max_age_hours=168.0)
    spaet = build_report(telemetry=[telemetry], now=NOW + timedelta(hours=10), max_age_hours=168.0)
    alter_frueh = frueh.to_dict()["routes"]["standard"]["transports"]["litellm"]["evidence_age"]
    alter_spaet = spaet.to_dict()["routes"]["standard"]["transports"]["litellm"]["evidence_age"]
    assert alter_frueh["newest_age_hours"] == 1.0
    assert alter_spaet["newest_age_hours"] == 11.0


# ---------------------------------------------------------------------------
# LiteLLM und Direkt je Route.
# ---------------------------------------------------------------------------


def test_direkt_und_litellm_stehen_je_route_getrennt(tmp_path: Path) -> None:
    """Der Direktpfad im OFF-Modus schreibt keine Route -- sie folgt aus dem Zweck."""
    direkt = [
        zeile(
            correlation_id=f"direct-{nummer}",
            logical_route=None,
            mode=None,
            transport="direct",
            identity_proven=False,
            actual_provider=None,
            actual_model=None,
            circuit_state=None,
            cost_usd=0.01,
            cost_known=True,
            cost_source="list_price:2026-09-08",
        )
        for nummer in range(4)
    ]
    route = _bericht(tmp_path, [*_vollstaendig(1), *direkt])["routes"]["standard"]

    assert route["transports"]["litellm"]["calls"] == 1
    direkt_abschnitt = route["transports"]["direct"]
    assert direkt_abschnitt["calls"] == 4
    assert direkt_abschnitt["route_from_purpose"] == 4
    assert direkt_abschnitt["cost"]["sources"] == {"list_price:2026-09-08": 4}
    # Kein Circuit auf dem Direktpfad ist kein Befund, sondern Bauart.
    assert direkt_abschnitt["failure"]["circuit_states"] is None
    assert direkt_abschnitt["null_reasons"]["failure.circuit_states"]
    assert "direct:circuit" not in route["missing"]
    # Die Identitaet ist auf dem Direktpfad unbelegt -- und das steht da.
    assert "direct:identitaet" in route["missing"]
    assert route["status"] == "LUECKENHAFT"


def test_zeilen_ohne_route_und_ohne_zweck_werden_gezaehlt_nicht_verteilt(
    tmp_path: Path,
) -> None:
    rows = [zeile(logical_route=None, purpose=None, correlation_id="lose")]
    bericht = _bericht(tmp_path, rows)
    assert bericht["inputs"]["telemetry"]["unattributed_rows"] == 1
    assert bericht["routes"]["standard"]["status"] == "KEINE_EVIDENZ"


def test_fehlender_transport_wird_nicht_als_direkt_geraten(tmp_path: Path) -> None:
    alt = zeile(schema_version="v2", correlation_id="alt")
    del alt["transport"]
    route = _bericht(tmp_path, [alt])["routes"]["standard"]
    assert route["transports"]["unbekannt"]["calls"] == 1
    assert route["transports"]["direct"]["calls"] == 0


def test_aeussere_kettenzeile_zaehlt_nicht_doppelt(tmp_path: Path) -> None:
    """Dieselbe Regel wie ``app.ai.spend.dedupe_chain_levels``."""
    innen = zeile(correlation_id="kette", chain_position=0)
    aussen = zeile(correlation_id="kette", chain_position=-1, cost_usd=0.002)
    bericht = _bericht(tmp_path, [innen, aussen])
    assert bericht["routes"]["standard"]["transports"]["litellm"]["calls"] == 1
    assert bericht["inputs"]["telemetry"]["outer_chain_rows_dropped"] == 1


def test_ausfallverhalten_aus_der_telemetrie(tmp_path: Path) -> None:
    rows = [
        zeile(
            correlation_id="a",
            ok=False,
            outcome="fallthrough",
            error_class="timeout",
            identity_proven=False,
            actual_provider=None,
            actual_model=None,
            cost_usd=None,
            cost_known=False,
            input_tokens=None,
            output_tokens=None,
            prompt_tokens=0,
            completion_tokens=0,
            circuit_state="closed",
        ),
        zeile(
            correlation_id="a",
            attempt=2,
            retry_count=1,
            ok=False,
            outcome="exhausted",
            error_class="timeout",
            identity_proven=False,
            actual_provider=None,
            actual_model=None,
            cost_usd=None,
            cost_known=False,
            input_tokens=None,
            output_tokens=None,
            prompt_tokens=0,
            completion_tokens=0,
            fallback_from="litellm",
            fallback_to="direct",
            circuit_state="open",
        ),
        zeile(correlation_id="b", stunden_alt=1.0),
        zeile(correlation_id="c", retry_count=None, fallback_from=None, circuit_state=None),
    ]
    del rows[3]["fallback_to"]
    del rows[3]["fallback_from"]
    del rows[3]["attempt"]
    litellm = _bericht(tmp_path, rows)["routes"]["standard"]["transports"]["litellm"]
    ausfall = litellm["failure"]

    assert ausfall["failures"] == 2
    assert ausfall["error_classes"] == {"timeout": 2}
    assert ausfall["retry_rows"] == 1
    assert ausfall["max_retry_count"] == 1
    assert ausfall["retry_unknown"] == 1
    assert ausfall["fallbacks"] == 1
    assert ausfall["fallback_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert ausfall["fallback_unknown"] == 1
    assert ausfall["circuit_states"] == {"closed": 2, "open": 1}
    assert ausfall["circuit_unknown"] == 1
    # Ein Fehlversuch ohne Verbrauch ist kein unbekannter Kostenfall (D-271).
    assert litellm["cost"]["failed_without_usage"] == 2
    assert litellm["cost"]["unknown"] == 0
    assert set(litellm["missing"]) >= {"retries", "fallback", "circuit"}


def test_ohne_transport_log_ist_die_litellm_version_unbelegt(tmp_path: Path) -> None:
    route = _bericht(tmp_path, _vollstaendig(), transport_log=None)["routes"]["standard"]
    litellm = route["transports"]["litellm"]
    assert litellm["version"]["transport_version"] is None
    assert "--transport-log" in litellm["null_reasons"]["version.transport_version"]
    assert route["missing"] == ["litellm:version"]


def test_transport_log_ohne_beleg_zeile(tmp_path: Path) -> None:
    bericht = _bericht(tmp_path, _vollstaendig(), transport_log="INFO: nur Rauschen\n")
    litellm = bericht["routes"]["standard"]["transports"]["litellm"]
    assert litellm["version"]["transport_version"] is None
    assert "TRANSPORT_VERIFIED" in litellm["null_reasons"]["version.transport_version"]
    assert bericht["inputs"]["transport_log"]["verified_lines"] == 0


def test_transport_log_zeile_ohne_zeitstempel_hat_kein_pruefdatum(tmp_path: Path) -> None:
    bericht = _bericht(tmp_path, _vollstaendig())
    log = bericht["inputs"]["transport_log"]
    assert log["verified_lines"] == 2
    assert log["last"]["litellm"]["verified_at"] is None
    assert log["last"]["litellm"]["null_reasons"]["verified_at"]


# ---------------------------------------------------------------------------
# Eval-Report v1 und v2: uebernommen, nicht neu bewertet.
# ---------------------------------------------------------------------------


def test_eval_report_v1_wird_uebernommen(tmp_path: Path) -> None:
    bericht = _bericht(tmp_path, _vollstaendig(), eval_report=eval_report_v1())
    auswertung = bericht["inputs"]["eval_report"]
    assert auswertung["schema_version"] == "litellm-shadow-eval-report/v1"
    assert auswertung["age_hours"] == 5.0
    assert auswertung["runtime_evidence"] is None
    assert auswertung["null_reasons"]["runtime_evidence"]
    entscheidung = bericht["routes"]["standard"]["eval_decision"]
    assert entscheidung == {
        "status": "INSUFFICIENT_EVIDENCE",
        "reasons": ["SAMPLE_COUNT_TOO_LOW"],
        "primary_ready": False,
    }
    # Uebernommen heisst: der Zustand der Route kommt aus der Telemetrie.
    assert bericht["routes"]["standard"]["status"] == "BELEGT"


def test_eval_report_v2_traegt_die_laufzeitnachweise(tmp_path: Path) -> None:
    bericht = _bericht(tmp_path, _vollstaendig(), eval_report=eval_report_v2())
    nachweise = bericht["inputs"]["eval_report"]["runtime_evidence"]
    assert nachweise["off_mode_proven"] == {
        "proven": True,
        "referenced": True,
        "artifact": "artifacts/proofs/off_mode.json",
        "artifact_sha256": "d" * 64,
        "proven_at": "2026-09-26T08:00:00+00:00",
        "version": "04046c68",
    }
    assert nachweise["rollback_proven"]["proven"] is False
    assert nachweise["trading_gate_changed"] is False


def test_route_nur_im_eval_report_ist_keine_evidenz_mit_entscheidung(tmp_path: Path) -> None:
    report = eval_report_v1()
    report["decisions"]["sonderweg"] = {
        "status": "NOT_READY",
        "reasons": ["X"],
        "primary_ready": False,
    }
    route = _bericht(tmp_path, _vollstaendig(), eval_report=report)["routes"]["sonderweg"]
    assert route["status"] == "KEINE_EVIDENZ"
    assert route["in_catalogue"] is False
    assert route["eval_decision"]["status"] == "NOT_READY"


def test_unbekanntes_eval_schema_wird_abgelehnt(tmp_path: Path) -> None:
    telemetry = write_jsonl(tmp_path / "t.jsonl", _vollstaendig(1))
    fremd = write_json(tmp_path / "eval.json", {"schema_version": "etwas-anderes/v9"})
    with pytest.raises(InputError):
        build_report(telemetry=[telemetry], eval_report=fremd, now=NOW)


# ---------------------------------------------------------------------------
# Kein null ohne Grund.
# ---------------------------------------------------------------------------


def _nulls_ohne_grund(abschnitt: dict[str, Any]) -> list[str]:
    gruende = abschnitt["null_reasons"]
    offen: list[str] = []
    for schluessel, wert in abschnitt.items():
        if schluessel == "null_reasons":
            continue
        if wert is None and schluessel not in gruende:
            offen.append(schluessel)
        if isinstance(wert, dict):
            for unter, unterwert in wert.items():
                pfad = f"{schluessel}.{unter}"
                if unterwert is None and pfad not in gruende:
                    offen.append(pfad)
    return offen


@pytest.mark.parametrize("mit_eingaben", [True, False])
def test_jedes_null_traegt_einen_grund(tmp_path: Path, mit_eingaben: bool) -> None:
    rows = [
        *_vollstaendig(),
        zeile(
            correlation_id="d",
            logical_route=None,
            transport="direct",
            identity_proven=False,
            circuit_state=None,
            ok=False,
            outcome="exhausted",
            error_class="auth",
            cost_usd=None,
            cost_known=False,
            input_tokens=None,
            output_tokens=None,
        ),
    ]
    bericht = _bericht(
        tmp_path,
        rows,
        transport_log=TRANSPORT_LOG if mit_eingaben else None,
        eval_report=eval_report_v1() if mit_eingaben else None,
    )
    for route in bericht["routes"].values():
        assert _nulls_ohne_grund(route) == [], route["logical_route"]
        for abschnitt in route["transports"].values():
            assert _nulls_ohne_grund(abschnitt) == [], (route["logical_route"], abschnitt)
    for eingabe, wert in bericht["inputs"].items():
        assert wert is not None or eingabe in bericht["null_reasons"], eingabe
    if mit_eingaben:
        assert _nulls_ohne_grund(bericht["inputs"]["eval_report"]) == []
        for beleg in bericht["inputs"]["transport_log"]["last"].values():
            assert _nulls_ohne_grund(beleg) == []


# ---------------------------------------------------------------------------
# Eingaben, Determinismus, Darstellung.
# ---------------------------------------------------------------------------


def test_defekte_zeilen_werden_gezaehlt_nicht_verwendet(tmp_path: Path) -> None:
    telemetry = tmp_path / "t.jsonl"
    telemetry.write_bytes(
        (
            json.dumps(zeile())
            + "\n{kaputt\n[1,2]\n"
            + json.dumps(zeile(ts="gestern", correlation_id="x"))
            + "\n"
            + json.dumps(zeile(schema_version="v99", correlation_id="y"))
            + "\n"
        ).encode("utf-8")
    )
    bericht = build_report(telemetry=[telemetry], now=NOW).to_dict()
    eingang = bericht["inputs"]["telemetry"]
    assert eingang["rows_read"] == 5
    assert eingang["rows_used"] == 1
    assert eingang["issues"] == {
        "JSON_DEFEKT": 1,
        "KEIN_OBJEKT": 1,
        "UNBEKANNTE_SCHEMA_VERSION": 1,
        "ZEITSTEMPEL_UNGUELTIG": 1,
    }


def test_fehlende_telemetriedatei_ist_ein_eingabefehler(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        build_report(telemetry=[tmp_path / "gibt_es_nicht.jsonl"], now=NOW)


def test_gleiche_eingabe_gleicher_bericht(tmp_path: Path) -> None:
    telemetry = write_jsonl(tmp_path / "t.jsonl", _vollstaendig())
    log = write_text(tmp_path / "l.log", TRANSPORT_LOG)
    erster = build_report(telemetry=[telemetry], transport_log=log, now=NOW)
    zweiter = build_report(telemetry=[telemetry], transport_log=log, now=NOW)
    assert canonical_json(erster) == canonical_json(zweiter)
    daten = json.loads(canonical_json(erster))
    assert daten["schema_version"] == "litellm-route-report/v1"
    assert daten["generated_at"] == NOW.isoformat()
    # Kein Pfad des Operators im Bericht -- nur Etikett und Hash.
    assert str(tmp_path) not in canonical_json(erster)


def test_markdown_eine_zeile_je_route_zustand_vorne_klartext(tmp_path: Path) -> None:
    rows = [
        *_vollstaendig(),
        zeile(
            logical_route="critical",
            purpose="intent",
            correlation_id="k",
            identity_proven=False,
            actual_provider=None,
            actual_model=None,
            cost_usd=None,
            cost_known=False,
        ),
    ]
    telemetry = write_jsonl(tmp_path / "t.jsonl", rows)
    log = write_text(tmp_path / "l.log", TRANSPORT_LOG)
    text = markdown_summary(build_report(telemetry=[telemetry], transport_log=log, now=NOW))

    zeilen = text.splitlines()
    routenzeilen = [z for z in zeilen if z.startswith("- ") and " · " in z]
    assert any(z.startswith("- BELEGT · standard") for z in routenzeilen), routenzeilen
    assert any(z.startswith("- LUECKENHAFT · critical") for z in routenzeilen)
    assert any(z.startswith("- KEINE_EVIDENZ · bulk") for z in routenzeilen)
    kritisch = next(z for z in routenzeilen if "critical" in z)
    assert "Kosten unbekannt" in kritisch
    assert "Identität unbelegt" in kritisch
    # Klartext: keine Maschinenschluessel in der Operatorsicht.
    assert "litellm:kosten" not in text
    assert "successes_unproven" not in text
    assert "LiteLLM" in text and "Direkt" in text
    assert "trifft keine Freigabe" in text
