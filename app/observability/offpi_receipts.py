"""Quittungen der Offsite-Kopie (MindBlow 2.0, E3) — der Schreiber auf der Pi.

Der Laptop (``kai_vault.ps1``) sichert Pi, Lightning-Material und Laptop auf die
Platte "KAI Backup", prueft jede Generation (sha256, Entschluesseln + Listen,
gzip-CRC, ``integrity_check`` der dev.db, ``verify_chain`` des Zahlungsjournals)
und reicht danach per ssh genau eine Quittung herein::

    ssh ubuntu@pi 'cd ~/ai_analyst_trading_bot && \\
        .venv/bin/python -m app.observability.offpi_receipts append' < quittung.json

Dieses Modul prueft das Schema und haengt gesperrt an ``artifacts/backup/
offpi_receipts.jsonl`` an (``append_jsonl_locked``: mehrere Laeufe duerfen sich
nicht ueberschreiben). Leser ist ``app/alerts/health_check_host.offpi_backup_finding``;
Vertrag: ``config/stream_contracts.json``.

Die Quittung ist EVIDENZ, kein Zustand: die Wahrheit liegt auf der Platte
(``VERIFIED.json`` je Generation). Eine abgewiesene Quittung wird nicht
"repariert", sondern laut abgelehnt — der Laptop meldet das und die Pi-Sonde
bleibt beim letzten gueltigen Beleg.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from app.storage.jsonl_io import append_jsonl_locked

OFFPI_RECEIPTS_FILENAME: Final = "offpi_receipts.jsonl"
OFFPI_RECEIPTS_RELPATH: Final = Path("backup") / OFFPI_RECEIPTS_FILENAME
SCHEMA: Final = "offpi_receipt/v1"
_REQUIRED: Final = ("schema", "ts_utc", "vault_id", "generation", "generation_ts_utc", "probe")
_PROBES: Final = frozenset({"PASS", "FAIL"})
#: Eine Quittung ist ~700 Byte. Die Grenze haelt einen fehlgeleiteten Strom
#: (z. B. ein Archiv statt JSON auf stdin) aus dem Journal heraus.
MAX_RECEIPT_BYTES: Final = 4096


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate(record: object) -> list[str]:
    """Probleme der Quittung; leer heisst gueltig."""
    if not isinstance(record, dict):
        return ["keine JSON-Objekt-Quittung"]
    problems = [f"Feld fehlt: {key}" for key in _REQUIRED if not record.get(key)]
    if record.get("schema") and record["schema"] != SCHEMA:
        problems.append(f"fremdes Schema: {record['schema']!r} (erwartet {SCHEMA})")
    if record.get("probe") and record["probe"] not in _PROBES:
        problems.append(f"probe muss PASS oder FAIL sein, nicht {record['probe']!r}")
    for key in ("ts_utc", "generation_ts_utc"):
        if record.get(key) and not _is_utc_timestamp(record[key]):
            problems.append(f"{key} ist kein UTC-Zeitstempel (…Z): {record[key]!r}")
    return problems


def append_receipt(artifacts_dir: Path, record: dict[str, Any]) -> Path:
    """Gueltige Quittung gesperrt anhaengen; ungueltige mit ``ValueError`` ablehnen."""
    problems = validate(record)
    if problems:
        raise ValueError("; ".join(problems))
    path = artifacts_dir / OFFPI_RECEIPTS_RELPATH
    append_jsonl_locked(path, record)
    return path


def utc_epoch(value: object) -> float | None:
    """Epoch-Sekunden eines ISO-Zeitstempels; ``None`` wenn keiner."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def newest_verified(artifacts_dir: Path) -> tuple[float, dict[str, Any]] | None:
    """(Generationszeit, Quittung) der neuesten Generation mit ``probe == "PASS"``.

    Zaehlt nur Quittungen im eigenen Schema; kaputte Zeilen werden uebersprungen.
    Fehlt die Datei, ist das ``None`` ("kein Beleg"); ist sie unlesbar, wird der
    ``OSError`` weitergereicht — unlesbar ist kein Beleg fuer "fehlt". Leser:
    ``health_check_host.offpi_backup_finding`` und der Operator-Digest.
    """
    path = artifacts_dir / OFFPI_RECEIPTS_RELPATH
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return None
    newest: tuple[float, dict[str, Any]] | None = None
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
            continue
        ts = utc_epoch(rec.get("generation_ts_utc"))
        if rec.get("probe") != "PASS" or ts is None:
            continue
        if newest is None or ts > newest[0]:
            newest = (ts, rec)
    return newest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.observability.offpi_receipts")
    parser.add_argument("command", choices=["append"])
    parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args(argv)
    raw = sys.stdin.read(MAX_RECEIPT_BYTES + 1)
    if len(raw.encode("utf-8")) > MAX_RECEIPT_BYTES:
        print(f"OFFPI_RECEIPT_REJECTED groesser als {MAX_RECEIPT_BYTES} Byte", file=sys.stderr)
        return 2
    try:
        record = json.loads(raw)
    except ValueError as exc:
        print(f"OFFPI_RECEIPT_REJECTED kein JSON: {exc}", file=sys.stderr)
        return 2
    try:
        path = append_receipt(args.artifacts_dir, record)
    except ValueError as exc:
        print(f"OFFPI_RECEIPT_REJECTED {exc}", file=sys.stderr)
        return 2
    print(f"OFFPI_RECEIPT_OK {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "MAX_RECEIPT_BYTES",
    "OFFPI_RECEIPTS_FILENAME",
    "OFFPI_RECEIPTS_RELPATH",
    "SCHEMA",
    "append_receipt",
    "main",
    "newest_verified",
    "utc_epoch",
    "validate",
]
