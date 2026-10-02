"""KI-Transport (LiteLLM) im Kontrollcenter -- nur lesend, ohne Modellaufruf.

Der Endpunkt stellt zusammen, was es schon gibt: die letzte TRANSPORT_VERIFIED-Zeile,
den Abgleich Baum gegen Lock, das Lebenszeichen des Proxys, Modi und Fristen der Routen,
den Circuit-Zustand dieses Prozesses und den Routenbericht. Bewertet wird hier NICHTS:
BELEGT/LUECKENHAFT/KEINE_EVIDENZ kommt ausschliesslich aus dem Bericht-Artefakt.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.config import InferenceSettings
from app.ai.transport_status import ROUTE_ORDER, TransportPaths, ai_transport_snapshot

NOW = datetime(2026, 9, 30, 17, 0, tzinfo=UTC)
LOCK = b"litellm==1.102.1 --hash=sha256:abc\n"
LOCK_SHA = hashlib.sha256(LOCK).hexdigest()


def _paths(
    tmp_path: Path,
    *,
    log: str | None = None,
    spec: str | None = LOCK_SHA,
    report: dict[str, Any] | str | None = None,
    lock: bytes | None = LOCK,
) -> TransportPaths:
    logfile = tmp_path / "litellm.err.log"
    if log is not None:
        logfile.write_text(log, encoding="utf-8")
    tree = tmp_path / "transport" / "litellm" / "current"
    tree.mkdir(parents=True)
    if spec is not None:
        (tree / "transport.json").write_text(json.dumps({"spec_sha256": spec}), encoding="utf-8")
    lockfile = tmp_path / "requirements-transport.lock"
    if lock is not None:
        lockfile.write_bytes(lock)
    reportfile = tmp_path / "litellm_route_report.json"
    if isinstance(report, dict):
        reportfile.write_text(json.dumps(report), encoding="utf-8")
    elif isinstance(report, str):
        reportfile.write_text(report, encoding="utf-8")
    return TransportPaths(
        transport_log=logfile,
        transports_root=tmp_path / "transport",
        lock_candidates=(lockfile,),
        route_report=reportfile,
    )


DATIERT = (
    "noise\n"
    "TRANSPORT_VERIFIED name=litellm version=1.102.1 "
    "tree=/t/1.102.1-0d33a313 manifest=0d33a313aaaa0000\n"
    "2026-09-30T16:21:34Z TRANSPORT_VERIFIED name=litellm version=1.102.1 "
    "tree=/t/1.102.1-8fe49f6f manifest=8fe49f6fd0352080\n"
)


def _settings(**over: Any) -> InferenceSettings:
    werte: dict[str, Any] = {
        "enabled": True,
        "mode_ceiling": "advisory",
        "route_modes": {"research": "advisory", "standard": "shadow"},
        "route_timeout_seconds": {"research": 180.0},
    }
    werte.update(over)
    return InferenceSettings(**werte)


def _bericht() -> dict[str, Any]:
    abschnitt = {
        "status": "LUECKENHAFT",
        "calls": 2,
        "last_success_at": "2026-09-10T14:59:00+00:00",
        "identity": {"share": 1.0},
        "cost": {"sum_known_usd": 0.0105, "unknown": 0},
        "failure": {"failures": 0, "fallback_rate": 0.0, "max_retry_count": 0},
        "evidence_age": {"newest_age_hours": 480.1, "stale": True},
        "version": {"release_sha": None},
        "null_reasons": {"version.release_sha": "keine Telemetriezeile traegt runtime_commit"},
    }
    return {
        "schema_version": "litellm-route-report/v1",
        "generated_at": "2026-09-30T16:30:00+00:00",
        "status_counts": {"BELEGT": 0, "LUECKENHAFT": 1, "KEINE_EVIDENZ": 5},
        "routes": {
            "standard": {
                "status": "LUECKENHAFT",
                "missing": ["litellm:evidence_age"],
                "transports": {"litellm": abschnitt},
            },
            "bulk": {"status": "KEINE_EVIDENZ", "missing": [], "transports": {}},
        },
    }


async def _lebt(url: str, timeout: float) -> int:
    assert url == "http://127.0.0.1:4000/health/liveliness"
    return 200


async def _tot(url: str, timeout: float) -> int:
    raise ConnectionError("refused")


async def _snapshot(tmp_path: Path, **kw: Any) -> dict[str, Any]:
    paths = kw.pop("paths", None) or _paths(tmp_path, log=DATIERT, report=_bericht())
    return await ai_transport_snapshot(
        settings=kw.pop("settings", _settings()),
        paths=paths,
        now=NOW,
        http_status=kw.pop("http_status", _lebt),
        circuit=kw.pop("circuit", lambda: []),
        runtime=kw.pop("runtime", lambda: ("c" * 40, "release")),
        detached_shadows=kw.pop("detached_shadows", lambda: 0),
    )


def _nulls_ohne_grund(wert: Any, pfad: str, gruende: dict[str, str]) -> list[str]:
    offen: list[str] = []
    if isinstance(wert, dict):
        for key, inner in wert.items():
            offen += _nulls_ohne_grund(inner, f"{pfad}.{key}" if pfad else key, gruende)
    elif wert is None and pfad not in gruende:
        offen.append(pfad)
    return offen


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


async def test_die_letzte_datierte_beleg_zeile_gewinnt(tmp_path: Path) -> None:
    transport = (await _snapshot(tmp_path))["transport"]
    assert transport["version"] == "1.102.1"
    assert transport["tree"] == "/t/1.102.1-8fe49f6f"
    assert transport["manifest"] == "8fe49f6fd0352080"
    assert transport["verified_at"] == "2026-09-30T16:21:34+00:00"
    assert transport["proxy_alive"] is True and transport["proxy_status_code"] == 200


async def test_eine_undatierte_zeile_hat_kein_pruefdatum_aber_einen_grund(tmp_path: Path) -> None:
    alt = "TRANSPORT_VERIFIED name=litellm version=1.99.0 tree=/t/x manifest=abcd\n"
    snap = await _snapshot(tmp_path, paths=_paths(tmp_path, log=alt, report=_bericht()))
    assert snap["transport"]["verified_at"] is None
    assert "Zeitstempel" in snap["null_reasons"]["transport.verified_at"]


async def test_die_beleg_zeile_ueberlebt_die_log_rotation(tmp_path: Path) -> None:
    # Mitternachts-Rotation (copytruncate): die Startzeile steht dann in ``.1``.
    paths = _paths(tmp_path, log="ERROR: No api key passed in.\n", report=_bericht())
    (tmp_path / "litellm.err.log.1").write_text(DATIERT, encoding="utf-8")
    transport = (await _snapshot(tmp_path, paths=paths))["transport"]
    assert transport["manifest"] == "8fe49f6fd0352080"
    assert transport["verified_at"] == "2026-09-30T16:21:34+00:00"


async def test_auch_eine_komprimierte_rotation_belegt_die_version(tmp_path: Path) -> None:
    import gzip

    paths = _paths(tmp_path, log="", report=_bericht())
    (tmp_path / "litellm.err.log.2.gz").write_bytes(gzip.compress(DATIERT.encode("utf-8")))
    transport = (await _snapshot(tmp_path, paths=paths))["transport"]
    assert transport["version"] == "1.102.1"


async def test_ohne_log_ist_die_version_unbelegt(tmp_path: Path) -> None:
    snap = await _snapshot(tmp_path, paths=_paths(tmp_path, log=None, report=_bericht()))
    assert snap["transport"]["version"] is None
    assert snap["null_reasons"]["transport.version"]


async def test_baum_und_lock_werden_abgeglichen(tmp_path: Path) -> None:
    transport = (await _snapshot(tmp_path))["transport"]
    assert transport["lock_matches"] is True
    assert transport["lock_sha256"] == transport["tree_spec_sha256"] == LOCK_SHA


async def test_ein_veralteter_baum_faellt_auf(tmp_path: Path) -> None:
    paths = _paths(tmp_path, log=DATIERT, spec="0" * 64, report=_bericht())
    assert (await _snapshot(tmp_path, paths=paths))["transport"]["lock_matches"] is False


async def test_ohne_transport_json_ist_der_abgleich_unbelegt(tmp_path: Path) -> None:
    snap = await _snapshot(
        tmp_path, paths=_paths(tmp_path, log=DATIERT, spec=None, report=_bericht())
    )
    assert snap["transport"]["lock_matches"] is None
    assert snap["null_reasons"]["transport.lock_matches"]


async def test_ein_toter_proxy_ist_false_mit_grund(tmp_path: Path) -> None:
    snap = await _snapshot(tmp_path, http_status=_tot)
    assert snap["transport"]["proxy_alive"] is False
    assert snap["transport"]["proxy_status_code"] is None
    assert "ConnectionError" in snap["null_reasons"]["transport.proxy_status_code"]


async def test_ein_nicht_lokaler_proxy_wird_nicht_angefragt(tmp_path: Path) -> None:
    async def verboten(url: str, timeout: float) -> int:
        raise AssertionError("keine Anfrage an nicht-lokale Adressen")

    snap = await _snapshot(
        tmp_path,
        settings=_settings(litellm_base_url="http://10.0.0.5:4000"),
        http_status=verboten,
    )
    assert snap["transport"]["proxy_alive"] is None
    assert "lokal" in snap["null_reasons"]["transport.proxy_alive"]


# --------------------------------------------------------------------------
# Routen
# --------------------------------------------------------------------------


async def test_alle_sechs_routen_in_fester_reihenfolge_mit_modus_und_frist(tmp_path: Path) -> None:
    routen = (await _snapshot(tmp_path))["routes"]
    assert [r["route"] for r in routen] == list(ROUTE_ORDER)
    nach = {r["route"]: r for r in routen}
    assert nach["research"]["mode"] == "advisory"
    assert nach["research"]["timeout_seconds"] == 180.0
    assert nach["research"]["deadline_seconds"] > 180.0 * 3
    assert nach["standard"]["mode"] == "shadow"
    assert nach["bulk"]["mode"] == "off"


async def test_ohne_freigabe_ist_alles_off(tmp_path: Path) -> None:
    routen = (await _snapshot(tmp_path, settings=_settings(enabled=False)))["routes"]
    assert {r["mode"] for r in routen} == {"off"}


async def test_der_circuit_zustand_landet_bei_seiner_route(tmp_path: Path) -> None:
    kreis = [
        {
            "route": "standard",
            "alias": "kai-standard",
            "upstream": None,
            "state": "open",
            "consecutive_failures": 5,
            "probe_in_flight": False,
        },
    ]
    nach = {r["route"]: r for r in (await _snapshot(tmp_path, circuit=lambda: kreis))["routes"]}
    assert nach["standard"]["circuit"] == [
        {"upstream": None, "state": "open", "consecutive_failures": 5, "probe_in_flight": False}
    ]
    assert nach["bulk"]["circuit"] == []


async def test_der_berichtsstatus_wird_uebernommen_nicht_berechnet(tmp_path: Path) -> None:
    snap = await _snapshot(tmp_path)
    nach = {r["route"]: r for r in snap["routes"]}
    standard = nach["standard"]["report"]
    assert standard["status"] == "LUECKENHAFT"
    assert standard["missing"] == ["litellm:evidence_age"]
    litellm = standard["transports"]["litellm"]
    assert litellm == {
        "status": "LUECKENHAFT",
        "calls": 2,
        "last_success_at": "2026-09-10T14:59:00+00:00",
        "identity_share": 1.0,
        "cost_sum_known_usd": 0.0105,
        "cost_unknown": 0,
        "failures": 0,
        "fallback_rate": 0.0,
        "max_retry_count": 0,
        "newest_age_hours": 480.1,
        "stale": True,
        "release_sha": None,
    }
    assert "keine Telemetriezeile" in snap["null_reasons"]["routes.standard.litellm.release_sha"]
    assert snap["report"]["available"] is True
    assert snap["report"]["age_hours"] == pytest.approx(0.5)
    assert snap["report"]["status_counts"]["KEINE_EVIDENZ"] == 5


async def test_ohne_bericht_steht_das_so_da(tmp_path: Path) -> None:
    snap = await _snapshot(tmp_path, paths=_paths(tmp_path, log=DATIERT, report=None))
    assert snap["report"]["available"] is False
    assert all(r["report"] is None for r in snap["routes"])
    assert "kai-litellm-route-report" in snap["null_reasons"]["report.generated_at"]


async def test_ein_kaputter_bericht_wird_nicht_halb_gelesen(tmp_path: Path) -> None:
    snap = await _snapshot(tmp_path, paths=_paths(tmp_path, log=DATIERT, report='{"schema'))
    assert snap["report"]["available"] is False
    assert "unlesbar" in snap["null_reasons"]["report.generated_at"]


async def test_ein_fremdes_berichtsschema_wird_abgelehnt(tmp_path: Path) -> None:
    fremd = {**_bericht(), "schema_version": "litellm-route-report/v99"}
    snap = await _snapshot(tmp_path, paths=_paths(tmp_path, log=DATIERT, report=fremd))
    assert snap["report"]["available"] is False


# --------------------------------------------------------------------------
# Laufzeit und Invarianten
# --------------------------------------------------------------------------


async def test_laufzeit_nennt_release_decke_und_schatten(tmp_path: Path) -> None:
    runtime = (await _snapshot(tmp_path, detached_shadows=lambda: 2))["runtime"]
    assert runtime["runtime_commit"] == "c" * 40
    assert runtime["runtime_source"] == "release"
    assert runtime["enabled"] is True
    assert runtime["mode_ceiling"] == "advisory"
    assert runtime["shadow_grace_seconds"] == 1.0
    assert runtime["detached_shadows"] == 2
    assert runtime["max_detached_shadows"] == 8


@pytest.mark.parametrize("mit_eingaben", [True, False])
async def test_jedes_null_traegt_einen_grund(tmp_path: Path, mit_eingaben: bool) -> None:
    paths = (
        _paths(tmp_path, log=DATIERT, report=_bericht())
        if mit_eingaben
        else _paths(tmp_path, log=None, spec=None, report=None, lock=None)
    )
    snap = await _snapshot(
        tmp_path,
        paths=paths,
        http_status=_lebt if mit_eingaben else _tot,
        runtime=lambda: (None, None),
    )
    gruende = snap["null_reasons"]
    offen = _nulls_ohne_grund(snap["transport"], "transport", gruende)
    offen += _nulls_ohne_grund(snap["runtime"], "runtime", gruende)
    offen += _nulls_ohne_grund(snap["report"], "report", gruende)
    for route in snap["routes"]:
        if route["report"] is None:
            assert f"routes.{route['route']}.report" in gruende
            continue
        for name, abschnitt in route["report"]["transports"].items():
            offen += _nulls_ohne_grund(abschnitt, f"routes.{route['route']}.{name}", gruende)
    assert offen == []


def test_der_endpunkt_ist_nicht_oeffentlich() -> None:
    """Wie /health/ai: Kette, Fehler und Fristen gehoeren nicht ins offene Netz."""
    from app.api.main import create_app

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("APP_ENV", "production")
        mp.setenv("APP_API_KEY", "geheim-test-key-0123456789")
        from app.core.settings import get_settings

        get_settings.cache_clear()
        app = create_app()
        antwort = TestClient(app).get("/health/ai/transport")
        get_settings.cache_clear()
    assert antwort.status_code in (401, 403)


def test_der_endpunkt_liefert_den_vertrag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routers import health

    monkeypatch.setattr(
        health,
        "default_transport_paths",
        lambda _configured: _paths(tmp_path, log=DATIERT, report=_bericht()),
    )

    async def ohne_netz(url: str, timeout: float) -> int:
        return 200

    monkeypatch.setattr("app.ai.transport_status.local_http_status", ohne_netz)
    app = FastAPI()
    app.include_router(health.router)
    antwort = TestClient(app).get("/health/ai/transport")
    assert antwort.status_code == 200
    assert antwort.headers["Cache-Control"].startswith("no-store")
    body = antwort.json()
    assert body["schema_version"] == "ai-transport/v1"
    assert set(body) == {
        "schema_version",
        "generated_at",
        "transport",
        "runtime",
        "routes",
        "report",
        "null_reasons",
    }
    assert [r["route"] for r in body["routes"]] == list(ROUTE_ORDER)
