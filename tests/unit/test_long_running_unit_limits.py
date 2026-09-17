"""Grenzen der zwei Dauer-Units (Audit 16.09. P1-10/P1-11, Sprint S-0917 C4).

``kai-entry-watch`` braucht eine Laufzeitgrenze, weil ein Haenger in
``run_tick()`` sonst unsichtbar bleibt; ``kai-server`` braucht eine
Speichergrenze, damit ein Wachstum als OOM-Kill + Neustart endet und nicht den
ganzen Pi einfriert. Beide Grenzen sind Zeilen in Unit-Dateien — ohne Test
verschwinden sie beim naechsten Umbau still.

Kommentare werden vor der Pruefung entfernt: die Begruendungen IN den Units
nennen die Direktiven woertlich.
"""

from __future__ import annotations

import re
from pathlib import Path

_UNIT_DIR = Path(__file__).resolve().parents[2] / "deploy" / "systemd"

#: Groesste gemessene Laufzeit je Watcher-Lauf (Pi, 7 Tage bis 17.09., n=8 281).
_MEASURED_MAX_RUN_S = 81.1


def _directives(name: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (_UNIT_DIR / name).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")) or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out[key] = value
    return out


def test_entry_watch_has_a_runtime_limit_above_every_measured_run() -> None:
    unit = _directives("kai-entry-watch.service")
    limit = int(unit["RuntimeMaxSec"])
    assert limit > _MEASURED_MAX_RUN_S, "Grenze wuerde gesunde Laeufe abbrechen"


def test_entry_watch_limit_leaves_room_for_the_configured_duration() -> None:
    """Wer ``--duration-seconds`` hochsetzt, muss die Grenze mitziehen.

    Gemessen: Laufzeit = Dauer + ~9 s Start (p50 63,7 s bei 55 s Dauer), Maximum
    +26 s. Verlangt wird Dauer + 60 s — sonst killt die Grenze den Normalbetrieb.
    """
    unit = _directives("kai-entry-watch.service")
    match = re.search(r"--duration-seconds\s+(\d+)", unit["ExecStart"])
    assert match, "ExecStart ohne --duration-seconds — Test und Unit neu abstimmen"
    assert int(unit["RuntimeMaxSec"]) >= int(match.group(1)) + 60


def test_kai_server_has_a_memory_limit_with_accounting() -> None:
    unit = _directives("kai-server.service")
    assert unit.get("MemoryAccounting") == "true"
    value = unit["MemoryMax"]
    assert value.endswith("G"), f"MemoryMax={value}: erwartet eine Angabe in G"
    # Obergrenze unter dem Pi-RAM (16 GB), Untergrenze klar ueber dem hoechsten
    # gemessenen cgroup-Peak (1,74 GB, 16.09.).
    assert 2 <= int(value[:-1]) <= 8
