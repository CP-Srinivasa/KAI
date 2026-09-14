"""Eine Unit im Takt ist kein Ausfall.

`kai-entry-watch.service` arbeitet absichtlich in Laeufen von 55 s und startet mit
``Restart=always`` / ``RestartSec=5`` neu. Die Prozess-Sonde nimmt eine
Momentaufnahme. Gemessen auf kai-pi5 am 2026-09-14: 946 Starts an einem Tag,
jeder mit ``errors=[]`` — und trotzdem alle paar Health-Laeufe ein P0:

    EXPECTED_UNIT_NOT_RUNNING kai-entry-watch.service   (MainPID=0 in der Pause)
    INVALID kai-entry-watch.service: Marker-PID … != MainPID …   (Marker noch vom Vorlauf)

Beides ist ein Fenster von wenigen Sekunden je Takt. Die Sonde misst deshalb fuer
genau diese Units nach, statt zu melden. Was beim Nachmessen bleibt, wird
gemeldet — ein echter Ausfall ueberlebt jede Wiederholung.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.alerts.process_runtime_probe as probe
from app.observability.premium_pipeline_health import CYCLING_SERVICES
from app.observability.process_runtime_marker import (
    REASON_ACTIVE_RELEASE_NOT_DEPLOYED,
    STATE_CODE_DRIFT,
    STATE_INVALID,
    STATE_MATCH,
    STATE_NOT_RUNNING,
    VERDICT_HOLD,
    VERDICT_OK,
    ProcessFinding,
    ProcessProvenance,
    evaluate_process_markers,
)

_REPO = Path(__file__).resolve().parents[2]
_WATCH = "kai-entry-watch.service"
_SERVER = "kai-server.service"


def _hold(*findings: ProcessFinding, extra_reasons: tuple[str, ...] = ()) -> ProcessProvenance:
    offen = {f.state for f in findings if not f.passing}
    return ProcessProvenance(
        verdict=VERDICT_HOLD,
        findings=findings,
        reasons=tuple(sorted(offen | set(extra_reasons))),
    )


# ── Was als Taktluecke gilt ──────────────────────────────────────────────────


def test_pause_zwischen_zwei_laeufen_ist_eine_taktluecke() -> None:
    p = evaluate_process_markers(
        [], {}, expected_sha="a" * 40, checkout_sha="a" * 40, expected_units=[_WATCH]
    )
    assert p.findings[0].state == STATE_NOT_RUNNING

    assert probe.nur_taktluecke(p) is True


def test_marker_vom_vorlauf_ist_eine_taktluecke() -> None:
    p = _hold(
        ProcessFinding(_SERVER, STATE_MATCH),
        ProcessFinding(_WATCH, STATE_INVALID, "Marker-PID 3485947 != MainPID 3486022"),
    )

    assert probe.nur_taktluecke(p) is True


def test_lang_laufende_unit_bekommt_keine_frist() -> None:
    """Die Frist gilt nur fuer den Katalog — kai-server im Takt gibt es nicht."""
    assert probe.nur_taktluecke(_hold(ProcessFinding(_SERVER, STATE_NOT_RUNNING))) is False


def test_drift_ist_nie_eine_taktluecke() -> None:
    assert probe.nur_taktluecke(_hold(ProcessFinding(_WATCH, STATE_CODE_DRIFT))) is False


def test_ein_echter_befund_daneben_verhindert_das_nachmessen() -> None:
    p = _hold(
        ProcessFinding(_WATCH, STATE_NOT_RUNNING),
        ProcessFinding(_SERVER, STATE_CODE_DRIFT),
    )

    assert probe.nur_taktluecke(p) is False


def test_falsches_aktives_release_ist_nie_eine_taktluecke() -> None:
    p = _hold(
        ProcessFinding(_WATCH, STATE_NOT_RUNNING),
        extra_reasons=(REASON_ACTIVE_RELEASE_NOT_DEPLOYED,),
    )

    assert probe.nur_taktluecke(p) is False


def test_gruen_ist_keine_taktluecke() -> None:
    assert probe.nur_taktluecke(ProcessProvenance(verdict=VERDICT_OK)) is False


# ── Nachmessen: verschwindet die Luecke, schweigt die Sonde ──────────────────


@pytest.fixture
def pausen(monkeypatch) -> list[float]:
    geschlafen: list[float] = []
    monkeypatch.setattr(probe, "_sleep", geschlafen.append)
    return geschlafen


def test_luecke_die_beim_nachmessen_verschwindet_wird_nicht_gemeldet(
    monkeypatch, pausen: list[float]
) -> None:
    antworten = iter([("EXPECTED_UNIT_NOT_RUNNING kai-entry-watch.service", True), (None, False)])
    monkeypatch.setattr(probe, "_evaluate_once", lambda root, sha: next(antworten))

    assert probe.process_runtime_finding(_REPO, checkout_sha="a" * 40) is None
    assert pausen == [probe.CYCLING_RESAMPLE_DELAYS_SEC[0]]


def test_dauerhafter_ausfall_wird_nach_dem_nachmessen_gemeldet(
    monkeypatch, pausen: list[float]
) -> None:
    befund = "EXPECTED_UNIT_NOT_RUNNING kai-entry-watch.service"
    monkeypatch.setattr(probe, "_evaluate_once", lambda root, sha: (befund, True))

    assert probe.process_runtime_finding(_REPO, checkout_sha="a" * 40) == befund
    assert pausen == list(probe.CYCLING_RESAMPLE_DELAYS_SEC)


def test_echter_befund_wird_ohne_wartezeit_gemeldet(monkeypatch, pausen: list[float]) -> None:
    monkeypatch.setattr(probe, "_evaluate_once", lambda root, sha: ("RUNTIME_CODE_DRIFT x", False))

    assert probe.process_runtime_finding(_REPO, checkout_sha="a" * 40) == "RUNTIME_CODE_DRIFT x"
    assert pausen == []


# ── Der Katalog bleibt an seiner Quelle ──────────────────────────────────────


def test_die_wartezeit_deckt_eine_pause_und_bleibt_begrenzt() -> None:
    """RestartSec 5 + Start und Selbstbezeugung ~4 s; der Health-Lauf wartet hoechstens 30 s."""
    gesamt = sum(probe.CYCLING_RESAMPLE_DELAYS_SEC)

    assert probe.CYCLING_RESAMPLE_DELAYS_SEC
    assert 10 <= gesamt <= 30


@pytest.mark.parametrize("unit", sorted(CYCLING_SERVICES))
def test_katalog_und_unit_datei_sagen_dasselbe(unit: str) -> None:
    """Nur eine Unit, die wirklich im Takt laeuft, darf die Frist bekommen."""
    text = (_REPO / "deploy" / "systemd" / unit).read_text(encoding="utf-8")

    assert "Restart=always" in text
    assert "--duration-seconds" in text
    assert "runtime-exec" in text, "ohne Selbstbezeugung gaebe es nichts nachzumessen"
