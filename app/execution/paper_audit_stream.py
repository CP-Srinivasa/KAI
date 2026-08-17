"""Der Port vor dem Paper-Execution-Audit-Stream.

``artifacts/paper_execution_audit.jsonl`` ist die Replay-SSOT der Paper-
Ausführung. Bis 2026-08-17 war er ein **Bus ohne Port**: 90 Dateien
referenzieren ihn, 50 lesen ihn mit eigenem ``open()``/``json.loads``. Zwei
davon — ``observability/churn_report.py`` und ``observability/edge_report.py``
— trugen dieselbe Funktion unter demselben Namen ``load_audit_events``, Zeile
für Zeile identisch bis auf das Log-Präfix.

Die Folge ist nicht Redundanz, sondern **Uneinheitlichkeit**: jede Lesestelle
darf eigene Annahmen über Kodierung, Leerzeilen und defekte Datensätze treffen,
und keine davon lässt sich zentral korrigieren.

Dieses Modul ist die eine Leseregel. Es ist bewusst schmal — es interpretiert
nichts, es liest. Die Semantik der Events (Seiten, PnL, Gebühren) bleibt in
``close_pnl.py`` / ``open_fee_match.py``, die dafür die kanonische Quelle sind.

**Schaden wird gezählt, nicht verschluckt.** Die Altfassungen verwarfen kaputte
Zeilen still (churn_report ganz ohne Log) oder mit einer Warnung *pro Zeile*
(edge_report — bei einer beschädigten Datei ein Log-Sturm). Beides ist falsch:
ein stiller Verlust in einem Evidenz-Stream ist genau die Lücke, gegen die die
Truth-Schicht gebaut ist, und eine Wache, die tausendfach schreit, wird
ignoriert. Hier: eine Meldung mit Anzahl, und die Anzahl ist am Ergebnis
ablesbar.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Die Replay-SSOT. Als Konstante hier, damit Konsumenten nicht jeder für sich
# einen String-Pfad hartkodieren.
DEFAULT_AUDIT_PATH = Path("artifacts/paper_execution_audit.jsonl")


@dataclass(frozen=True)
class AuditStreamRead:
    """Ergebnis eines Lesevorgangs — inklusive des Schadens, der dabei auffiel."""

    events: list[dict[str, Any]] = field(default_factory=list)
    #: Zeilen, die weder leer noch ein JSON-Objekt waren.
    skipped: int = 0
    #: Nicht-leere Zeilen insgesamt (``len(events) + skipped``).
    total_lines: int = 0
    #: ``False``, wenn die Datei gar nicht existierte.
    file_present: bool = True


def _iter_raw_lines(path: Path) -> Iterator[str]:
    # ``errors="replace"``: ein einzelnes kaputtes Byte darf nicht den ganzen
    # Stream verlieren. Die betroffene Zeile scheitert dann am JSON-Parser und
    # wird als Schaden GEZAEHLT — statt die Datei unlesbar zu machen.
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        yield from fh


def read_audit_stream(
    path: str | Path = DEFAULT_AUDIT_PATH,
    *,
    source: str = "paper_audit_stream",
) -> AuditStreamRead:
    """Den Stream lesen und dabei ausweisen, was nicht gelesen werden konnte."""
    p = Path(path)
    if not p.exists():
        logger.warning("[%s] audit file not found: %s", source, p)
        return AuditStreamRead(file_present=False)

    events: list[dict[str, Any]] = []
    skipped = 0
    for raw in _iter_raw_lines(p):
        line = raw.strip()
        if not line:
            continue  # Leerzeilen sind Formatierung, kein Schaden.
        try:
            parsed = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(parsed, dict):
            # Gueltiges JSON, aber kein Event: ein nacktes Array oder ein
            # Skalar wuerde weiter unten als ``.get()``-Aufruf knallen.
            skipped += 1
            continue
        events.append(parsed)

    if skipped:
        # EINE Meldung mit Anzahl statt einer pro Zeile.
        logger.warning(
            "[%s] %d unlesbare Zeile(n) in %s uebersprungen (%d gelesen)",
            source,
            skipped,
            p,
            len(events),
        )
    return AuditStreamRead(
        events=events,
        skipped=skipped,
        total_lines=len(events) + skipped,
    )


def load_audit_events(
    path: str | Path = DEFAULT_AUDIT_PATH,
    *,
    source: str = "paper_audit_stream",
) -> list[dict[str, Any]]:
    """Nur die Events — signaturgleicher Ersatz für die beiden Altfassungen."""
    return read_audit_stream(path, source=source).events


def iter_audit_events(
    path: str | Path = DEFAULT_AUDIT_PATH,
) -> Iterator[dict[str, Any]]:
    """Events einzeln, ohne die Datei zu materialisieren.

    Der Stream wächst (2026-08-17: 4,5 MB / 6585 Zeilen). Wer nur filtert oder
    zählt, sollte ihn nicht komplett in den Speicher ziehen.
    """
    p = Path(path)
    if not p.exists():
        return
    for raw in _iter_raw_lines(p):
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            yield parsed


__all__ = [
    "DEFAULT_AUDIT_PATH",
    "AuditStreamRead",
    "iter_audit_events",
    "load_audit_events",
    "read_audit_stream",
]
