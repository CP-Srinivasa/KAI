"""Circuit-Zustand je Dienst als Datei (KI-Kontrollstation, 02.10.2026).

Der Circuit lebt prozesslokal (``app.ai.runtime._KREISE``): das Dashboard sah bisher
nur den des Servers. Jeder Dienst schreibt seinen Stand bei JEDER Aenderung atomar
nach ``artifacts/runtime/circuit_<dienst>.json``; ohne Aenderung kein Schreiben.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from app.ai.circuit import DEFAULT_COOLDOWN_S

EXPORT_DIR: Final = Path("artifacts/runtime")
SCHEMA: Final = "ai-circuit/v1"
_LETZTER: dict[str, str] = {}


@dataclass(frozen=True)
class ServiceCircuit:
    service: str
    written_at: datetime
    stale: bool
    keys: list[dict[str, Any]]


def export_circuit(
    states: list[dict[str, Any]], *, service: str, now: datetime, directory: Path = EXPORT_DIR
) -> bool:
    """Schreibt den Stand, wenn er sich seit dem letzten Schreiben geaendert hat."""
    fingerabdruck = json.dumps(states, sort_keys=True, default=str)
    if _LETZTER.get(service) == fingerabdruck:
        return False
    directory.mkdir(parents=True, exist_ok=True)
    daten = {
        "schema": SCHEMA,
        "service": service,
        "written_at": now.astimezone(UTC).isoformat(),
        "cooldown_s": DEFAULT_COOLDOWN_S,
        "keys": states,
    }
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".circuit-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, sort_keys=True, default=str)
    os.replace(tmp, directory / f"circuit_{service}.json")
    _LETZTER[service] = fingerabdruck
    return True


def read_circuits(
    *, now: datetime, directory: Path = EXPORT_DIR, max_age_h: float = 24.0
) -> list[ServiceCircuit]:
    """Alle Dienst-Staende. Ein offener Kreis altert nach der Abkuehlung zu ``half_open``.

    Der Export schreibt nur bei einer Aenderung: kam nach dem Oeffnen kein Aufruf mehr,
    bliebe ``open`` sonst ewig stehen, obwohl der Kreis laengst wieder pruefen darf.
    """
    ergebnis: list[ServiceCircuit] = []
    for datei in sorted(directory.glob("circuit_*.json")):
        try:
            daten = json.loads(datei.read_text(encoding="utf-8"))
            geschrieben = datetime.fromisoformat(daten["written_at"])
            keys = list(daten.get("keys") or [])
            abkuehlung = float(daten.get("cooldown_s") or DEFAULT_COOLDOWN_S)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        alter = now - geschrieben
        gealtert = [
            {**k, "state": "half_open"}
            if k.get("state") == "open" and alter > timedelta(seconds=abkuehlung)
            else k
            for k in keys
        ]
        ergebnis.append(
            ServiceCircuit(
                service=str(daten.get("service") or datei.stem.removeprefix("circuit_")),
                written_at=geschrieben,
                stale=alter > timedelta(hours=max_age_h),
                keys=gealtert,
            )
        )
    return ergebnis


__all__ = [
    "DEFAULT_COOLDOWN_S",
    "EXPORT_DIR",
    "SCHEMA",
    "ServiceCircuit",
    "export_circuit",
    "read_circuits",
]
