"""P1-C — ein Prozess muss seine geladene Revision selbst bezeugen.

Der reale Fall, gegen den diese Datei gebaut ist (2026-09-01, 21:09Z gemessen):

    kai-server gestartet 11:58:53Z          -> geladener Code  dc276bc3
    Checkout danach ff-gemergt              -> HEAD            9293c423
    ``runtime_provenance`` meldete          -> RUNTIME_CODE_DRIFT = 0

Die Sonde fragte den Checkout nach seinem heutigen HEAD, nicht den Prozess nach
dem, was er geladen hat. Sie war damit blind gegen die haeufigste Form von
„deployt ohne Deckung".

Jede Kontrolle hier muss einmal richtig fehlschlagen koennen, sonst kontrolliert
sie nichts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.observability.process_runtime_marker import (
    MARKER_SCHEMA,
    STATE_CODE_DRIFT,
    STATE_DEPENDENCY_DRIFT,
    STATE_INVALID,
    STATE_MATCH,
    STATE_STALE_NO_RESTART,
    STATE_UNKNOWN,
    VERDICT_HOLD,
    VERDICT_OK,
    ProcessObservation,
    build_deployment_marker,
    build_process_marker,
    evaluate_process_markers,
    proc_start_ticks,
    read_deployment_marker,
    read_process_markers,
    render_process_provenance,
    write_process_marker,
)

NEW = "9293c4239b80ebbfec42a39cda289ba4f60a1610"
OLD = "dc276bc32700bf1293de3eabe73a3b3b6675d9d8"
BOOT = "0b1d5f2a-0000-4000-8000-000000000001"
LOCK = "65de2c3439f9bc06e77dfa0b41427186476f0d81271784cb796e2c54370b6908"


def _obs(**over) -> ProcessObservation:
    base = {
        "unit": "kai-server.service",
        "main_pid": 4711,
        "proc_start_ticks": 90210,
        "boot_id": BOOT,
        "started_at_utc": "2026-09-02T05:00:00+00:00",
    }
    base.update(over)
    return ProcessObservation(**base)  # type: ignore[arg-type]


def _marker(**over) -> dict:
    base = {
        "unit": "kai-server.service",
        "pid": 4711,
        "proc_start_ticks": 90210,
        "boot_id": BOOT,
        "repo_root": "/home/ubuntu/ai_analyst_trading_bot",
        "runtime_code_sha": NEW,
        "python_executable": "/home/ubuntu/ai_analyst_trading_bot/.venv/bin/python3",
        "requirements_lock_sha256": LOCK,
        "started_at_utc": "2026-09-02T05:00:00+00:00",
    }
    base.update(over)
    return build_process_marker(**base)  # type: ignore[arg-type]


def _evaluate(marker: dict | None, obs: ProcessObservation | None = None, **kw):
    observation = obs or _obs()
    return evaluate_process_markers(
        [observation],
        {observation.unit: marker},
        expected_sha=kw.pop("expected_sha", NEW),
        checkout_sha=kw.pop("checkout_sha", NEW),
        expected_lock_sha256=kw.pop("expected_lock_sha256", LOCK),
        deployed_at_utc=kw.pop("deployed_at_utc", None),
    )


# --------------------------------------------------------------------------
# Positivkontrolle
# --------------------------------------------------------------------------


def test_positivkontrolle_prozess_bezeugt_den_erwarteten_code() -> None:
    p = _evaluate(_marker())
    assert p.verdict == VERDICT_OK
    assert p.all_match
    assert [f.state for f in p.findings] == [STATE_MATCH]
    assert "jeder laufende Prozess" in render_process_provenance(p)


# --------------------------------------------------------------------------
# Negativkontrolle 1 — der reale Fall vom 2026-09-01
# --------------------------------------------------------------------------


def test_negativ_1_prozess_auf_altem_commit_checkout_bereits_neu() -> None:
    """Der Fall, den die alte Sonde als ``RUNTIME_CODE_DRIFT = 0`` gemeldet hat."""
    p = _evaluate(_marker(runtime_code_sha=OLD), checkout_sha=NEW, expected_sha=NEW)
    assert p.verdict == VERDICT_HOLD
    assert p.findings[0].state == STATE_CODE_DRIFT
    assert not p.all_match
    detail = p.findings[0].detail
    assert OLD[:8] in detail
    assert NEW[:8] in detail
    assert "Checkout steht bereits" in detail


# --------------------------------------------------------------------------
# Negativkontrolle 2 — Marker gehoert zu einem anderen Prozess
# --------------------------------------------------------------------------


def test_negativ_2_marker_pid_passt_nicht_zur_mainpid() -> None:
    p = _evaluate(_marker(pid=1234))
    assert p.verdict == VERDICT_HOLD
    assert p.findings[0].state == STATE_INVALID


def test_negativ_2b_passende_sha_rettet_eine_fremde_pid_nicht() -> None:
    """Identitaet vor Inhalt: eine zufaellig richtige SHA ist kein Beweis."""
    p = _evaluate(_marker(pid=1234, runtime_code_sha=NEW))
    assert p.findings[0].state == STATE_INVALID


# --------------------------------------------------------------------------
# Negativkontrolle 3 — PID wiederverwendet
# --------------------------------------------------------------------------


def test_negativ_3_gleiche_pid_andere_startzeit() -> None:
    p = _evaluate(_marker(proc_start_ticks=11111))
    assert p.findings[0].state == STATE_INVALID
    assert "Vorgaenger" in p.findings[0].detail


def test_negativ_3b_marker_aus_einem_frueheren_boot() -> None:
    p = _evaluate(_marker(boot_id="ffffffff-0000-4000-8000-000000000002"))
    assert p.findings[0].state == STATE_INVALID
    assert "Boot" in p.findings[0].detail


# --------------------------------------------------------------------------
# Negativkontrolle 4 — Marker fehlt
# --------------------------------------------------------------------------


def test_negativ_4_fehlender_marker_ist_unbekannt_nie_in_ordnung() -> None:
    p = _evaluate(None)
    assert p.verdict == VERDICT_HOLD
    assert p.findings[0].state == STATE_UNKNOWN
    assert not p.findings[0].passing
    assert not p.all_match


# --------------------------------------------------------------------------
# Deploy-Invariante
# --------------------------------------------------------------------------


def test_prozess_aelter_als_der_deploy_ist_stale() -> None:
    p = _evaluate(
        _marker(),
        _obs(started_at_utc="2026-09-02T04:00:00+00:00"),
        deployed_at_utc="2026-09-02T06:00:00+00:00",
    )
    assert p.verdict == VERDICT_HOLD
    assert p.findings[0].state == STATE_STALE_NO_RESTART


def test_prozess_nach_dem_deploy_gestartet_ist_in_ordnung() -> None:
    p = _evaluate(
        _marker(),
        _obs(started_at_utc="2026-09-02T07:00:00+00:00"),
        deployed_at_utc="2026-09-02T06:00:00+00:00",
    )
    assert p.verdict == VERDICT_OK


def test_die_sha_schlaegt_die_zeit_wenn_beide_etwas_sagen() -> None:
    """Der Primaerbeweis ist die bezeugte Revision, nicht der Zeitstempel."""
    p = _evaluate(
        _marker(runtime_code_sha=OLD),
        _obs(started_at_utc="2026-09-02T07:00:00+00:00"),
        deployed_at_utc="2026-09-02T06:00:00+00:00",
    )
    assert p.findings[0].state == STATE_CODE_DRIFT


def test_abhaengigkeitsdrift_wird_erkannt() -> None:
    p = _evaluate(_marker(requirements_lock_sha256="a" * 64))
    assert p.findings[0].state == STATE_DEPENDENCY_DRIFT


# --------------------------------------------------------------------------
# Zerlegung: nie eine Summe ohne ihre Zeilen
# --------------------------------------------------------------------------


def test_mehrere_dienste_werden_einzeln_ausgewiesen() -> None:
    a = _obs(unit="kai-server.service")
    b = _obs(unit="kai-tg-listener.service", main_pid=99, proc_start_ticks=5)
    markers = {
        a.unit: _marker(),
        b.unit: _marker(unit=b.unit, pid=99, proc_start_ticks=5, runtime_code_sha=OLD),
    }
    p = evaluate_process_markers(
        [a, b], markers, expected_sha=NEW, checkout_sha=NEW, expected_lock_sha256=LOCK
    )
    assert p.units_total == 2
    assert p.units_matching == 1
    assert not p.all_match
    states = {f.unit: f.state for f in p.findings}
    assert states[a.unit] == STATE_MATCH
    assert states[b.unit] == STATE_CODE_DRIFT
    assert b.unit in render_process_provenance(p)


def test_ohne_dienste_ist_nichts_bewiesen() -> None:
    p = evaluate_process_markers([], {}, expected_sha=NEW)
    assert p.verdict == VERDICT_OK
    assert not p.all_match, "0 von 0 ist kein Beweis, dass alles passt"


# --------------------------------------------------------------------------
# Persistenz
# --------------------------------------------------------------------------


def test_marker_ueberlebt_den_weg_ueber_die_platte(tmp_path: Path) -> None:
    written = write_process_marker(_marker(), root=tmp_path)
    assert written.is_file()
    back = read_process_markers(["kai-server.service"], root=tmp_path)
    assert back["kai-server.service"] == _marker()


def test_ein_marker_mit_fremdem_schema_zaehlt_als_fehlend(tmp_path: Path) -> None:
    m = _marker()
    m["schema"] = "etwas/anderes"
    write_process_marker(m, root=tmp_path)
    assert read_process_markers(["kai-server.service"], root=tmp_path) == {
        "kai-server.service": None
    }


def test_kaputter_marker_zaehlt_als_fehlend(tmp_path: Path) -> None:
    path = tmp_path / "artifacts" / "runtime" / "processes" / "kai-server.service.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{kein json", encoding="utf-8")
    assert read_process_markers(["kai-server.service"], root=tmp_path) == {
        "kai-server.service": None
    }


def test_zwei_dienste_ueberschreiben_einander_nicht(tmp_path: Path) -> None:
    write_process_marker(_marker(), root=tmp_path)
    write_process_marker(_marker(unit="kai-tg-listener.service", pid=99), root=tmp_path)
    back = read_process_markers(["kai-server.service", "kai-tg-listener.service"], root=tmp_path)
    assert back["kai-server.service"]["pid"] == 4711  # type: ignore[index]
    assert back["kai-tg-listener.service"]["pid"] == 99  # type: ignore[index]


def test_der_marker_traegt_sein_schema() -> None:
    assert _marker()["schema"] == MARKER_SCHEMA


def test_deploy_marker_hin_und_zurueck(tmp_path: Path) -> None:
    doc = build_deployment_marker(
        repo_sha=NEW, requirements_lock_sha256=LOCK, deployed_at_utc="2026-09-02T06:00:00+00:00"
    )
    target = tmp_path / "artifacts" / "runtime" / "deployment_marker.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc), encoding="utf-8")
    assert read_deployment_marker(tmp_path) == doc


def test_fehlender_deploy_marker_ist_none(tmp_path: Path) -> None:
    assert read_deployment_marker(tmp_path) is None


# --------------------------------------------------------------------------
# /proc-Parser: der Kommandoname darf Leerzeichen und Klammern enthalten
# --------------------------------------------------------------------------


def test_proc_stat_wird_ab_der_letzten_klammer_geteilt(tmp_path: Path) -> None:
    # fields[0] ist Feld 3 (state); Feld 22 (starttime) liegt damit auf Index 19.
    fields = ["0"] * 40
    fields[0] = "S"
    fields[19] = "123456"
    (tmp_path / "77").mkdir()
    (tmp_path / "77" / "stat").write_text(
        "77 (python 3.12 (uvicorn)) " + " ".join(fields), encoding="utf-8"
    )
    assert proc_start_ticks(77, proc_root=tmp_path) == 123456


def test_fehlendes_proc_verzeichnis_ergibt_minus_eins(tmp_path: Path) -> None:
    assert proc_start_ticks(4711, proc_root=tmp_path) == -1


@pytest.mark.parametrize("stat", ["ohne klammer", "77 (x) S", ""])
def test_unlesbares_stat_ergibt_minus_eins(tmp_path: Path, stat: str) -> None:
    (tmp_path / "77").mkdir()
    (tmp_path / "77" / "stat").write_text(stat, encoding="utf-8")
    assert proc_start_ticks(77, proc_root=tmp_path) == -1
