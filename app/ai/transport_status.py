"""KI-Transport (LiteLLM) auf einen Blick -- fuer das Kontrollcenter, nur lesend.

Stellt zusammen, was es schon gibt, und bewertet selbst nichts:

* die letzte ``TRANSPORT_VERIFIED``-Zeile (welcher Baum, welche Version, seit wann),
* den Abgleich des aktiven Transportbaums gegen ``requirements-transport.lock``,
* das Lebenszeichen des Proxys (``/health/liveliness`` -- kein Modellaufruf, keine Kosten),
* Modus, Alias, Timeout und Gesamtfrist je Route aus der wirksamen Konfiguration,
* den Circuit-Zustand DIESES Prozesses (kai-server fuehrt die Aufrufe),
* den Routenbericht (``scripts/litellm_route_report``) als Artefakt.

BELEGT / LUECKENHAFT / KEINE_EVIDENZ steht ausschliesslich im Bericht. Eine zweite
Bewertung hier waere ein zweiter Wahrheitszustand ueber dieselbe Frage. Jedes ``null``
traegt einen Grund in ``null_reasons`` -- nie eine stille 0.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx

from app.ai.config import InferenceSettings
from app.ai.modes import resolve_mode

SCHEMA: Final = "ai-transport/v1"
REPORT_SCHEMA: Final = "litellm-route-report/v1"
#: Feste Reihenfolge der Katalogrouten im Kontrollcenter.
ROUTE_ORDER: Final = ("bulk", "standard", "reasoning", "critical", "stt", "research")
MARKER: Final = "TRANSPORT_VERIFIED"
_LOG_TAIL_BYTES: Final = 256 * 1024
#: logrotate (``deploy/logrotate/kai``): taeglich, ``rotate 14``, ``delaycompress``.
#: Die Beleg-Zeile entsteht nur beim Start -- nach Mitternacht steht sie in ``.1``.
_ROTATION_DEPTH: Final = 14
_PROBE_TIMEOUT_S: Final = 2.0
_KEIN_BERICHT: Final = (
    "noch kein Routenbericht (artifacts/litellm_route_report.json) -- "
    "der Timer kai-litellm-route-report erzeugt ihn stuendlich"
)

HttpStatus = Callable[[str, float], Awaitable[int]]


@dataclass(frozen=True)
class TransportPaths:
    transport_log: Path
    transports_root: Path
    #: Kandidaten fuer requirements-transport.lock, der erste vorhandene gilt.
    lock_candidates: tuple[Path, ...]
    route_report: Path


def default_transport_paths(configured: InferenceSettings) -> TransportPaths:
    """Pfade relativ zum Arbeitsverzeichnis von kai-server.

    Im Release sind ``logs/`` und ``artifacts/`` Verweise auf den Status-Checkout; das
    Transport-Lock liegt nur dort, nicht im Release-Baum. Im Entwicklungs-Checkout liegt
    es direkt im Arbeitsverzeichnis.
    """
    logs = Path("logs")
    try:
        checkout = logs.resolve().parent
    except OSError:
        checkout = Path.cwd()
    root = (
        Path(configured.transports_root)
        if configured.transports_root
        else (Path.home() / "transport")
    )
    return TransportPaths(
        transport_log=logs / "litellm.err.log",
        transports_root=root,
        lock_candidates=(
            Path("requirements-transport.lock"),
            checkout / "requirements-transport.lock",
        ),
        route_report=Path("artifacts") / "litellm_route_report.json",
    )


async def local_http_status(url: str, timeout: float) -> int:
    """Statuscode einer lokalen GET-Anfrage (nur fuer das Lebenszeichen)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        return (await client.get(url)).status_code


def _zeit(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _log_ende(path: Path) -> str:
    if path.suffix == ".gz":
        return gzip.decompress(path.read_bytes())[-_LOG_TAIL_BYTES:].decode(
            "utf-8", errors="replace"
        )
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        groesse = handle.tell()
        handle.seek(max(0, groesse - _LOG_TAIL_BYTES))
        return handle.read().decode("utf-8", errors="replace")


def _letzte_beleg_zeile(path: Path) -> tuple[str | None, str]:
    """Die letzte TRANSPORT_VERIFIED-Zeile -- oder (None, Grund).

    Erst die aktuelle Datei, dann ihre Rotationen, neueste zuerst.
    """
    try:
        text = _log_ende(path)
    except OSError:
        return None, f"{path.as_posix()} fehlt oder ist nicht lesbar"
    rotationen = (
        path.with_name(name)
        for nummer in range(1, _ROTATION_DEPTH + 1)
        for name in (f"{path.name}.{nummer}", f"{path.name}.{nummer}.gz")
    )
    for kandidat in (None, *rotationen):
        if kandidat is not None:
            try:
                text = _log_ende(kandidat)
            except (OSError, EOFError, gzip.BadGzipFile):
                continue
        for zeile in reversed(text.splitlines()):
            if MARKER in zeile:
                return zeile, ""
    return None, f"keine {MARKER}-Zeile in {path.as_posix()} und seinen Rotationen"


def _transport(paths: TransportPaths, gruende: dict[str, str]) -> dict[str, Any]:
    feld: dict[str, Any] = dict.fromkeys(("version", "tree", "manifest", "verified_at"))
    zeile, grund = _letzte_beleg_zeile(paths.transport_log)
    if zeile is None:
        for key in feld:
            gruende[f"transport.{key}"] = grund
    else:
        position = zeile.find(MARKER)
        for token in zeile[position + len(MARKER) :].split():
            key, _, value = token.partition("=")
            if key in ("version", "tree", "manifest") and value:
                feld[key] = value
        vorne = zeile[:position].split()
        moment = _zeit(vorne[0]) if vorne else None
        feld["verified_at"] = moment.astimezone(UTC).isoformat() if moment else None
        if moment is None:
            gruende["transport.verified_at"] = (
                "Zeile traegt keinen Zeitstempel (vor dem Nachtrag vom 30.09. geschrieben)"
            )
        for key in ("version", "tree", "manifest"):
            if feld[key] is None:
                gruende[f"transport.{key}"] = f"{MARKER}-Zeile nennt kein {key}="

    spec: str | None = None
    try:
        daten = json.loads(
            (paths.transports_root / "litellm" / "current" / "transport.json").read_text(
                encoding="utf-8"
            )
        )
        wert = daten.get("spec_sha256") if isinstance(daten, dict) else None
        spec = wert if isinstance(wert, str) and len(wert) == 64 else None
    except (OSError, ValueError):
        spec = None
    lock = next(
        (sha for sha in (_sha256(kandidat) for kandidat in paths.lock_candidates) if sha), None
    )
    if spec is None:
        gruende["transport.tree_spec_sha256"] = (
            "transport.json des aktiven Baums fehlt oder nennt kein spec_sha256"
        )
    if lock is None:
        gruende["transport.lock_sha256"] = "requirements-transport.lock nicht gefunden"
    if spec is None or lock is None:
        gruende["transport.lock_matches"] = "Baum oder Lock nicht lesbar -- kein Abgleich moeglich"
    feld.update(
        tree_spec_sha256=spec,
        lock_sha256=lock,
        lock_matches=(spec == lock) if spec is not None and lock is not None else None,
    )
    return feld


async def _proxy(
    configured: InferenceSettings, probe: HttpStatus, gruende: dict[str, str]
) -> dict[str, Any]:
    from app.integrations.litellm.provider import LiteLLMConfig

    basis = configured.litellm_base_url
    if not LiteLLMConfig(base_url=basis).is_local:
        grund = "Proxy-Adresse liegt nicht lokal -- wird nicht angefragt"
        gruende["transport.proxy_alive"] = grund
        gruende["transport.proxy_status_code"] = grund
        return {"proxy_alive": None, "proxy_status_code": None}
    try:
        status = await probe(f"{basis.rstrip('/')}/health/liveliness", _PROBE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - ein toter Proxy ist ein Befund, kein 500
        gruende["transport.proxy_status_code"] = f"nicht erreichbar ({type(exc).__name__})"
        return {"proxy_alive": False, "proxy_status_code": None}
    return {"proxy_alive": status == 200, "proxy_status_code": status}


def _bericht(
    path: Path, now: datetime, gruende: dict[str, str]
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    kopf: dict[str, Any] = {
        "available": False,
        "generated_at": None,
        "age_hours": None,
        "status_counts": {},
    }

    def fehlt(grund: str) -> tuple[None, dict[str, Any]]:
        gruende["report.generated_at"] = grund
        gruende["report.age_hours"] = grund
        return None, kopf

    try:
        roh = path.read_text(encoding="utf-8")
    except OSError:
        return fehlt(_KEIN_BERICHT)
    try:
        daten = json.loads(roh)
    except ValueError:
        return fehlt(f"Routenbericht unlesbar ({path.as_posix()}) -- {_KEIN_BERICHT}")
    if not isinstance(daten, dict) or daten.get("schema_version") != REPORT_SCHEMA:
        return fehlt(f"Routenbericht mit unbekanntem Schema -- erwartet {REPORT_SCHEMA}")
    erzeugt = _zeit(
        daten.get("generated_at") if isinstance(daten.get("generated_at"), str) else None
    )
    zaehler = daten.get("status_counts")
    kopf.update(
        available=True,
        generated_at=erzeugt.astimezone(UTC).isoformat() if erzeugt else None,
        age_hours=round((now - erzeugt).total_seconds() / 3600.0, 2) if erzeugt else None,
        status_counts=dict(zaehler) if isinstance(zaehler, dict) else {},
    )
    if erzeugt is None:
        gruende["report.generated_at"] = "Routenbericht nennt keinen gueltigen Erzeugungszeitpunkt"
        gruende["report.age_hours"] = gruende["report.generated_at"]
    return daten, kopf


#: Feld im Kontrollcenter -> (Pfad im Bericht, Schluessel in dessen null_reasons).
_BERICHTSFELDER: Final = {
    "calls": (("calls",), "calls"),
    "last_success_at": (("last_success_at",), "last_success_at"),
    "identity_share": (("identity", "share"), "identity.share"),
    "cost_sum_known_usd": (("cost", "sum_known_usd"), "cost.sum_known_usd"),
    "cost_unknown": (("cost", "unknown"), "cost.unknown"),
    "failures": (("failure", "failures"), "failure.failures"),
    "fallback_rate": (("failure", "fallback_rate"), "failure.fallback_rate"),
    "max_retry_count": (("failure", "max_retry_count"), "failure.max_retry_count"),
    "newest_age_hours": (("evidence_age", "newest_age_hours"), "evidence_age.newest_age_hours"),
    "stale": (("evidence_age", "stale"), "evidence_age.stale"),
    "release_sha": (("version", "release_sha"), "version.release_sha"),
}


def _auszug(
    route: str, transport: str, abschnitt: dict[str, Any], gruende: dict[str, str]
) -> dict[str, Any]:
    ergebnis: dict[str, Any] = {"status": abschnitt.get("status")}
    berichtsgruende = abschnitt.get("null_reasons")
    berichtsgruende = berichtsgruende if isinstance(berichtsgruende, dict) else {}
    for feld, (pfad, schluessel) in _BERICHTSFELDER.items():
        wert: Any = abschnitt
        for teil in pfad:
            wert = wert.get(teil) if isinstance(wert, dict) else None
        ergebnis[feld] = wert
        if wert is None:
            gruende[f"routes.{route}.{transport}.{feld}"] = str(
                berichtsgruende.get(schluessel) or "im Routenbericht nicht belegt"
            )
    if ergebnis["status"] is None:
        gruende[f"routes.{route}.{transport}.status"] = "Routenbericht nennt keinen Zustand"
    return ergebnis


def _routen(
    configured: InferenceSettings,
    bericht: dict[str, Any] | None,
    kreise: list[dict[str, Any]],
    gruende: dict[str, str],
) -> list[dict[str, Any]]:
    from app.ai.runtime import route_deadline_seconds

    decke = configured.mode_ceiling if configured.enabled else "off"
    berichtsrouten = bericht.get("routes") if bericht else None
    berichtsrouten = berichtsrouten if isinstance(berichtsrouten, dict) else {}
    routen = []
    for route in ROUTE_ORDER:
        eintrag = berichtsrouten.get(route)
        auszug: dict[str, Any] | None = None
        if isinstance(eintrag, dict):
            transporte = eintrag.get("transports")
            transporte = transporte if isinstance(transporte, dict) else {}
            auszug = {
                "status": eintrag.get("status"),
                "missing": list(eintrag.get("missing") or []),
                "transports": {
                    name: _auszug(route, name, abschnitt, gruende)
                    for name, abschnitt in sorted(transporte.items())
                    if isinstance(abschnitt, dict)
                },
            }
        else:
            gruende[f"routes.{route}.report"] = (
                _KEIN_BERICHT if bericht is None else "Route steht nicht im Routenbericht"
            )
        routen.append(
            {
                "route": route,
                "mode": resolve_mode(route, per_route=configured.route_modes, ceiling=decke),
                "alias": configured.route_aliases.get(route, route),
                "timeout_seconds": configured.route_timeout_seconds.get(
                    route, configured.timeout_seconds
                ),
                "deadline_seconds": round(route_deadline_seconds(configured, route), 1),
                "circuit": [
                    {
                        "upstream": kreis.get("upstream"),
                        "state": kreis.get("state"),
                        "consecutive_failures": kreis.get("consecutive_failures"),
                        "probe_in_flight": kreis.get("probe_in_flight"),
                    }
                    for kreis in kreise
                    if kreis.get("route") == route
                ],
                "report": auszug,
            }
        )
    return routen


def _laufzeit_default() -> tuple[str | None, str | None]:
    try:
        from app.core.runtime_identity import get_runtime_identity

        identitaet = get_runtime_identity()
    except Exception:  # noqa: BLE001 - die Anzeige stirbt nicht an der Identitaet
        return None, None
    return identitaet.runtime_commit, identitaet.runtime_source


def _kreise_default() -> list[dict[str, Any]]:
    from app.ai.runtime import circuit_state

    return circuit_state()


def _schatten_default() -> int:
    from app.ai.gateway import detached_shadow_count

    return detached_shadow_count()


async def ai_transport_snapshot(
    *,
    settings: InferenceSettings,
    paths: TransportPaths,
    now: datetime | None = None,
    http_status: HttpStatus | None = None,
    circuit: Callable[[], list[dict[str, Any]]] | None = None,
    runtime: Callable[[], tuple[str | None, str | None]] | None = None,
    detached_shadows: Callable[[], int] | None = None,
) -> dict[str, Any]:
    """Der Vertrag ``ai-transport/v1`` -- jede Quelle injizierbar, keine Ausnahme nach aussen."""
    from app.ai.gateway import MAX_DETACHED_SHADOWS

    jetzt = now or datetime.now(UTC)
    gruende: dict[str, str] = {}
    transport = _transport(paths, gruende)
    transport.update(await _proxy(settings, http_status or local_http_status, gruende))
    commit, quelle = (runtime or _laufzeit_default)()
    if commit is None:
        gruende["runtime.runtime_commit"] = "Release des laufenden Prozesses nicht bestimmbar"
    if quelle is None:
        gruende["runtime.runtime_source"] = "Release des laufenden Prozesses nicht bestimmbar"
    bericht, kopf = _bericht(paths.route_report, jetzt, gruende)
    return {
        "schema_version": SCHEMA,
        "generated_at": jetzt.astimezone(UTC).isoformat(),
        "transport": transport,
        "runtime": {
            "runtime_commit": commit,
            "runtime_source": quelle,
            "enabled": settings.enabled,
            "mode_ceiling": settings.mode_ceiling,
            "shadow_grace_seconds": settings.shadow_grace_seconds,
            "detached_shadows": (detached_shadows or _schatten_default)(),
            "max_detached_shadows": MAX_DETACHED_SHADOWS,
        },
        "routes": _routen(settings, bericht, (circuit or _kreise_default)(), gruende),
        "report": kopf,
        "null_reasons": dict(sorted(gruende.items())),
    }


__all__ = [
    "REPORT_SCHEMA",
    "ROUTE_ORDER",
    "SCHEMA",
    "TransportPaths",
    "ai_transport_snapshot",
    "default_transport_paths",
    "local_http_status",
]
