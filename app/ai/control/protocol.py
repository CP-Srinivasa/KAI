"""Protokoll der KI-Schalter: Fingerabdruck der NICHT geheimen Werte, Aenderungen als JSON.

Bewusst eine begrenzte JSON-Datei (max. :data:`MAX_ENTRIES`) statt eines JSONL-Stroms: ein
neuer ``*.jsonl``-Strom braeuchte einen Strom-Vertrag mit Frische-Pruefung in
``app/alerts/health_check.py`` -- einer Datei ohne Spielraum im God-File-Ratchet.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from app.ai.control.config import ControlPaths

PREFIXES: Final = (
    "KAI_INFERENCE_",
    "KAI_LITELLM_",
    "SOURCE_LLM_SPARFENSTER_",
    "APP_AI_BUDGET_",
    "KAI_AI_CONTROL_",
)
GEHEIM: Final = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS")
MAX_ENTRIES: Final = 200


def safe_switches(env_file: Path) -> dict[str, str]:
    """Die KI-Schalter aus der ``.env`` -- ohne alles, was nach Geheimnis aussieht."""
    werte: dict[str, str] = {}
    try:
        zeilen = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return werte
    for roh in zeilen:
        zeile = roh.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, _, wert = zeile.partition("=")
        name = name.strip()
        if name.startswith(PREFIXES) and not any(g in name for g in GEHEIM):
            werte[name] = wert.strip().strip('"').strip("'")
    return werte


def diff(old: dict[str, str], new: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"key": k, "old": old.get(k), "new": new.get(k)}
        for k in sorted(set(old) | set(new))
        if old.get(k) != new.get(k)
    ]


def _atomar(path: Path, daten: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ai-protocol-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, sort_keys=True)
    os.replace(tmp, path)


def read(path: Path) -> list[dict[str, Any]]:
    try:
        daten = json.loads(path.read_text(encoding="utf-8"))
        return list(daten.get("entries") or [])
    except (OSError, ValueError, AttributeError):
        return []


def record(
    paths: ControlPaths, *, now: datetime, extra: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Vergleicht mit dem letzten Stand; Aenderungen landen neueste-zuerst im Protokoll.

    Der erste Lauf legt nur die Basis an -- er kennt kein Vorher und meldet nichts.
    ``extra`` traegt Werte von ausserhalb der ``.env``, z. B. ``{"RELEASE": "7446c486"}``.
    """
    basis = paths.runtime_dir / "ai_control_switches.json"
    jetzt = {**safe_switches(paths.env_file), **(extra or {})}
    try:
        vorher = json.loads(basis.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _atomar(basis, jetzt)
        return []
    aenderungen = diff(vorher if isinstance(vorher, dict) else {}, jetzt)
    if aenderungen:
        stempel = now.astimezone(UTC).isoformat()
        neu = [
            {"ts": stempel, "actor": "Konfiguration", "kind": "schalter", **a} for a in aenderungen
        ]
        _atomar(
            paths.protocol,
            {
                "schema": "ai-control-protocol/v1",
                "entries": (neu + read(paths.protocol))[:MAX_ENTRIES],
            },
        )
        _atomar(basis, jetzt)
    return aenderungen


__all__ = ["MAX_ENTRIES", "diff", "read", "record", "safe_switches"]
