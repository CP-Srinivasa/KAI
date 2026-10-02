import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability.ai_control import circuit_export as ce

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
OFFEN = [
    {
        "route": "standard",
        "alias": "kai-standard",
        "upstream": None,
        "state": "open",
        "consecutive_failures": 5,
        "probe_in_flight": False,
    }
]


def test_schreibt_nur_bei_aenderung(tmp_path: Path) -> None:
    ce._LETZTER.clear()
    assert ce.export_circuit(OFFEN, service="kai-server", now=T0, directory=tmp_path) is True
    assert ce.export_circuit(OFFEN, service="kai-server", now=T0, directory=tmp_path) is False
    daten = json.loads((tmp_path / "circuit_kai-server.json").read_text())
    assert daten["schema"] == "ai-circuit/v1" and daten["keys"][0]["state"] == "open"


def test_offener_circuit_altert_zu_half_open(tmp_path: Path) -> None:
    ce._LETZTER.clear()
    ce.export_circuit(OFFEN, service="kai-server", now=T0, directory=tmp_path)
    frisch = ce.read_circuits(now=T0 + timedelta(seconds=10), directory=tmp_path)
    assert frisch[0].keys[0]["state"] == "open" and not frisch[0].stale
    spaeter = ce.read_circuits(
        now=T0 + timedelta(seconds=ce.DEFAULT_COOLDOWN_S + 1), directory=tmp_path
    )
    assert spaeter[0].keys[0]["state"] == "half_open"
    alt = ce.read_circuits(now=T0 + timedelta(hours=25), directory=tmp_path)
    assert alt[0].stale is True


def test_kaputte_datei_wird_uebersprungen(tmp_path: Path) -> None:
    (tmp_path / "circuit_kaputt.json").write_text("{nicht json")
    assert ce.read_circuits(now=T0, directory=tmp_path) == []


def test_haken_schreibt_nur_unter_systemd_und_in_vorhandenes_verzeichnis(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    from app.ai import runtime

    ce._LETZTER.clear()
    ziel = tmp_path / "rt"
    monkeypatch.setattr(ce, "EXPORT_DIR", ziel)
    monkeypatch.setattr("app.observability.service_name.service_name", lambda: "unbekannt")
    runtime._circuit_exportieren()
    assert not ziel.exists(), "Tests und Laptop ohne systemd schreiben nichts"
    monkeypatch.setattr("app.observability.service_name.service_name", lambda: "kai-server")
    runtime._circuit_exportieren()
    assert not ziel.exists(), "ohne vorhandenes artifacts/runtime kein Verzeichnis anlegen"
    ziel.mkdir()
    runtime._circuit_exportieren()
    assert (ziel / "circuit_kai-server.json").exists()
