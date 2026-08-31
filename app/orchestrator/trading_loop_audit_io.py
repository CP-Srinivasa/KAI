"""Lesen des Zyklus-Audit-Stroms — herausgeloest aus ``trading_loop.py``.

Extraktion statt angehobener Baseline (God-File-Ratchet, repo_hygiene_policy §5):
``trading_loop.py`` traegt die Schleifenlogik, nicht das Dateiformat ihres
Protokolls. Der Anlass war das Fenster unten — und ein God-File waechst nicht
fuer einen Lesepfad.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

_AUDIT_LOG = Path("artifacts") / "trading_loop_audit.jsonl"

__all__ = ["load_trading_loop_cycles"]


def load_trading_loop_cycles(
    audit_path: str | Path = _AUDIT_LOG,
    *,
    tail: int | None = None,
) -> list[dict[str, object]]:
    """Load loop cycle audit rows from JSONL, skipping malformed lines.

    ``tail`` begrenzt das Ergebnis auf die n JUENGSTEN Saetze — und zwar
    waehrend des Lesens, nicht danach. Gemessen am 31.08. auf dem Pi:
    128.501 Saetze aus einer 79-MB-Datei kosten **+320 MB**; das war der
    groesste Einzelposten im ``kai-health-check``, der ab 20:00 vom
    OOM-Killer erschlagen wurde (540 MB gegen MemoryMax=512M).

    Bewusst **opt-in**: ``build_recent_cycles_summary`` zaehlt trotz seines
    Namens ``status_counts`` ueber die GESAMTE Historie, und
    ``build_loop_status_summary`` will den letzten Satz. Ein Default-Fenster
    wuerde deren Zahlen still veraendern — genau die Art Aenderung, die
    niemand bemerkt, bis eine Kennzahl nicht mehr stimmt.
    """
    path = Path(audit_path)
    if not path.exists():
        return []

    records: deque[dict[str, object]] | list[dict[str, object]] = (
        deque(maxlen=tail) if tail is not None and tail > 0 else []
    )
    if tail is not None and tail <= 0:
        return []
    # KAI-01: stream the (~27 MB) trading-loop audit line-by-line instead of
    # ``read_text().splitlines()`` to avoid the full-file RAM peak on the Pi.
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
    return list(records)
