"""Welcher systemd-Dienst schreibt -- einmal je Prozess aus ``/proc/self/cgroup``.

Telemetrie v10 (KI-Kontrollstation, 02.10.2026): ohne den Dienstnamen sieht das
Dashboard, DASS analysiert wurde, aber nicht von wem. Keine Unit-Aenderung noetig.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Final

UNKNOWN: Final = "unbekannt"
_UNIT: Final = re.compile(r"/([A-Za-z0-9@_.:-]+)\.service(?:/|$)")


def parse_cgroup(text: str) -> str:
    for line in text.splitlines():
        treffer = _UNIT.search(line.strip())
        if treffer:
            return treffer.group(1)
    return UNKNOWN


@lru_cache(maxsize=1)
def service_name(path: Path = Path("/proc/self/cgroup")) -> str:
    try:
        return parse_cgroup(path.read_text(encoding="utf-8"))
    except OSError:
        return UNKNOWN
