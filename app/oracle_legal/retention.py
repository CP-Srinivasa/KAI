"""Löschfristen der Oracle-Beta (Datenschutz v0.5, Operator 01.10.2026).

Täglich ausgeführt von ``scripts/audit_rotate.py --apply`` (Timer ``kai-audit-rotate``,
04:40). Was die Datenschutzseite zusagt, muss hier stehen:

- **IP-Adressen** in den eigenen Zugriffsprotokollen der Anwendung und der daraus
  gebildete Schutzkennwert: nach sechs Tagen entfernt. Bei täglichem Lauf ist damit
  keine IP älter als sieben Tage.
  - ``api_request_audit.jsonl``: das laufende File hält nur sechs Tage
    (``ROTATION_RULES``, auch nach Alter rotiert); Ältere Zeilen liegen im Archiv,
    dort entfernt :func:`strip_audit_archive_ips` das Feld ``client_ip``.
  - ``ln_demand_ledger.jsonl``: :func:`strip_demand_fingerprints` leert
    ``requester_fp`` nach sechs Tagen; die übrigen Felder sind Buchungs- und
    Nachfragebelege ohne Personenbezug.
- **Meldungen** (``oracle/oracle_cases.jsonl``): 90 Tage nach Abschluss entfernt.
- **Einladungen**: 30 Tage nach ihrem Ende (:func:`app.oracle_legal.invites.prune`).

Technische Server-Logs (``logs/*.log``) rotiert logrotate täglich und löscht sie nach
14 Tagen; das steht so auf der Datenschutzseite.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.file_lock import append_lock
from app.oracle_legal import invites

logger = logging.getLogger(__name__)

IP_RETENTION = timedelta(days=6)
CASE_RETENTION_AFTER_CLOSE = timedelta(days=90)
# Archivnamen leiten sich vom bestehenden Strom ab (audit_rotate: <stem>.<ts>.jsonl).
_AUDIT_STEM, _AUDIT_SUFFIX = "api_request_audit.jsonl".rsplit(".", 1)
_NOIP_MARK = ".noip"


def _parse(ts: object) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _rewrite(path: Path, lines: list[str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, path)


def strip_audit_archive_ips(archive_dir: Path) -> int:
    """``client_ip`` aus archivierten API-Zugriffsprotokollen entfernen.

    Archive sind ruhend (kein Schreiber), daher ohne Sperre. Eine bearbeitete Datei
    bekommt die Endung ``.noip.jsonl`` und wird danach nicht erneut gelesen.
    """
    done = 0
    for src in sorted(archive_dir.glob(f"{_AUDIT_STEM}.*.{_AUDIT_SUFFIX}")):
        if _NOIP_MARK in src.name:
            continue
        out: list[str] = []
        with src.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    out.append(line if line.endswith("\n") else line + "\n")
                    continue
                if isinstance(row, dict):
                    row.pop("client_ip", None)
                    out.append(json.dumps(row, ensure_ascii=False) + "\n")
        dest = src.with_name(f"{src.stem}{_NOIP_MARK}{src.suffix}")
        _rewrite(dest, out)
        src.unlink()
        done += 1
    return done


def strip_demand_fingerprints(path: Path, now: datetime) -> int:
    """``requester_fp`` in Zeilen älter als :data:`IP_RETENTION` leeren (unter Sperre)."""
    if not path.exists():
        return 0
    cutoff = now - IP_RETENTION
    changed = 0
    with append_lock(path):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        out: list[str] = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                out.append(line)
                continue
            stamp = _parse(row.get("ts")) if isinstance(row, dict) else None
            if stamp is not None and stamp < cutoff and row.get("requester_fp"):
                row["requester_fp"] = ""
                changed += 1
                out.append(json.dumps(row, ensure_ascii=False) + "\n")
            else:
                out.append(line)
        if changed:
            _rewrite(path, out)
    return changed


def prune_cases(path: Path, now: datetime) -> int:
    """Meldungen 90 Tage nach Abschluss (``erledigt``) vollständig entfernen."""
    if not path.exists():
        return 0
    cutoff = now - CASE_RETENTION_AFTER_CLOSE
    with append_lock(path):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        rows: list[tuple[str, dict[str, Any] | None]] = []
        closed: dict[str, datetime] = {}
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                rows.append((line, None))
                continue
            rows.append((line, row if isinstance(row, dict) else None))
            if isinstance(row, dict) and row.get("state") == "erledigt":
                at = _parse(row.get("at"))
                if at is not None:
                    closed[str(row.get("case_id"))] = at
        drop = {cid for cid, at in closed.items() if at < cutoff}
        if not drop:
            return 0
        keep = [line for line, row in rows if row is None or str(row.get("case_id")) not in drop]
        _rewrite(path, keep)
    return len(drop)


def apply(artifacts_dir: Path, *, now: datetime | None = None) -> dict[str, int]:
    """Alle Löschfristen einmal anwenden; jede für sich fail-soft."""
    now = now or datetime.now(UTC)
    steps: dict[str, Callable[[], int]] = {
        "audit_archives_stripped": lambda: strip_audit_archive_ips(artifacts_dir / "archive"),
        "demand_fingerprints_cleared": lambda: strip_demand_fingerprints(
            artifacts_dir / "ln_demand_ledger.jsonl", now
        ),
        "cases_removed": lambda: prune_cases(artifacts_dir / "oracle" / "oracle_cases.jsonl", now),
        "invites_removed": lambda: invites.prune(
            now=now, path=artifacts_dir / "oracle" / "invites.jsonl"
        ),
    }
    counts: dict[str, int] = {}
    for name, step in steps.items():
        try:
            counts[name] = step()
        except Exception:  # noqa: BLE001 — eine Frist darf die anderen nicht blockieren
            logger.exception("[retention] %s fehlgeschlagen", name)
            counts[name] = -1
    return counts


__all__ = [
    "CASE_RETENTION_AFTER_CLOSE",
    "IP_RETENTION",
    "apply",
    "prune_cases",
    "strip_audit_archive_ips",
    "strip_demand_fingerprints",
]
