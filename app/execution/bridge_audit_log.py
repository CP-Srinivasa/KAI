"""Append-Pfad des Bridge-Audits (``bridge_pending_orders.jsonl``) unter Lock.

Vier Prozesse schreiben diese Datei — kai-server (Premium-Router und
Telegram-Bot), kai-entry-watch, der Paper-Cron-Tick und der
Premium-Healthcheck — bisher ohne Serialisierung (System-Audit 16.09.,
NEO-A-014). Eine Bridge-Zeile ist im Schnitt ~2,6 kB und kann den 8-kB-Puffer
von ``TextIOWrapper`` ueberschreiten; dann zerfaellt ein ``write()`` in mehrere
Syscalls, die sich mit einem zweiten Writer verzahnen koennen. Eine verzahnte
Zeile ist kein JSON mehr und faellt beim toleranten Lesen still heraus.

Best-effort-Lock wie im uebrigen Trade-Pfad: ein Lock-Fehler blockt keinen
Fill, wird aber geloggt (``app.core.file_lock``). Die Rotation
(``scripts/audit_rotate.py``) nimmt denselben Lock, damit Umbenennen und
Neuanlegen nie zwischen Oeffnen und Schreiben eines Writers fallen.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.file_lock import append_lock

logger = logging.getLogger(__name__)


def append_bridge_audit(path: Path, record: dict[str, object]) -> bool:
    """Eine Zeile anhaengen; ``False`` bei IO-Fehler (geloggt, nie geworfen)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        with append_lock(path), path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
    except OSError as exc:
        logger.error("[bridge] audit write failed: %s", exc)
        return False
    return True
