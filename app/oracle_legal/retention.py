"""Löschfristen der Oracle-Beta (Datenschutz v0.5, Operator 01.10.2026).

Täglich ausgeführt von ``scripts/audit_rotate.py --apply`` (Timer ``kai-audit-rotate``,
04:40). Was die Datenschutzseite zusagt, muss hier stehen:

- **IP-Adressen** in den eigenen Zugriffsprotokollen der Anwendung und der daraus
  gebildete Schutzkennwert: knapp vor sechs Tagen entfernt. Bei täglichem Lauf ist damit
  keine IP älter als sieben Tage (das sagt die Seite zu, :data:`IP_PROMISE`).
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

Betrieb (erster echter Lauf 02.10.2026 per OOM-Kill gescheitert, MemoryMax=256M):
jede Datei wird zeilenweise in ein ``.tmp`` geschrieben und atomar ersetzt, der
Speicher hängt nicht von der Dateigröße ab. Ein Abbruch lässt das Original unberührt,
der nächste Lauf macht weiter. Die kleinen Fristen laufen vor den großen Archiven.
:func:`overdue` prüft danach, ob die Zusage tatsächlich eingehalten ist; der Timer
endet nur dann erfolgreich.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

from app.core.file_lock import append_lock
from app.oracle_legal import invites

logger = logging.getLogger(__name__)

#: Sechs Tage minus zwei Stunden: der tägliche Lauf (04:40, ~1 min) räumt eine Zeile, die
#: knapp unter der Grenze lag, erst 24 h später. 6 d + 24 h wäre exakt die Zusage ohne
#: Puffer für Laufzeit oder Verzug. Gleich ``keep_hours`` von ``api_request_audit``.
IP_RETENTION = timedelta(days=6) - timedelta(hours=2)
#: Öffentliche Zusage der Datenschutzseite („spätestens sieben Tage“); die sechs Tage
#: oben sind der Puffer für den täglichen Lauf.
IP_PROMISE = timedelta(days=7)
CASE_RETENTION_AFTER_CLOSE = timedelta(days=90)
# Archivnamen leiten sich vom bestehenden Strom ab (audit_rotate: <stem>.<ts>.jsonl).
_LIVE_AUDIT = "api_request_audit.jsonl"
_AUDIT_STEM, _AUDIT_SUFFIX = _LIVE_AUDIT.rsplit(".", 1)
_NOIP_MARK = ".noip"
_DEMAND_LEDGER = "ln_demand_ledger.jsonl"
_CASES = Path("oracle") / "oracle_cases.jsonl"
# Feld und Wert des echten Schreibers (``app.oracle_legal.mark_case``).
_CASE_STATUS_KEY = "status"
_CASE_CLOSED = "erledigt"
# Abgerissene Ledger-Zeile: den Kennwert auch ohne gültiges JSON finden.
_TORN_FP = re.compile(r'("requester_fp"\s*:\s*)"([^"\n]*)("?)')


class RetentionIncompleteError(RuntimeError):
    """Mindestens ein Archiv blieb unbereinigt; die übrigen sind erledigt."""


def _parse(ts: object) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _loads(line: str) -> dict[str, Any] | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def _lines(path: Path) -> Iterator[str]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        yield from fh


@contextmanager
def _atomic_writer(path: Path) -> Iterator[IO[str]]:
    """Zeilenweise nach ``<path>.tmp`` schreiben, danach atomar ersetzen.

    Quellen im selben ``with`` NACH diesem Writer öffnen: sie sind dann vor dem
    Ersetzen geschlossen (Windows ersetzt keine offene Datei). Bei einem Fehler
    bleibt das Ziel unverändert und das ``.tmp`` wird entfernt; nach einem harten
    Abbruch überschreibt es der nächste Lauf.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            yield fh
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def _unstripped_archives(archive_dir: Path) -> list[Path]:
    return [
        p
        for p in sorted(archive_dir.glob(f"{_AUDIT_STEM}.*.{_AUDIT_SUFFIX}"))
        if _NOIP_MARK not in p.name
    ]


def _strip_one_archive(src: Path) -> int:
    """Ein Archiv ohne ``client_ip`` neu schreiben; gibt die verworfenen Zeilen zurück.

    Eine abgerissene Zeile (der Audit-Schreiber nimmt keine Sperre) oder ein Wert, der
    kein Objekt ist, kann eine IP tragen und lässt sich nicht sicher bereinigen: sie
    wird verworfen, nicht übernommen.
    """
    dest = src.with_name(f"{src.stem}{_NOIP_MARK}{src.suffix}")
    dropped = 0
    with _atomic_writer(dest) as out, src.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = _loads(line)
            if row is None:
                dropped += 1
                continue
            row.pop("client_ip", None)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    src.unlink(missing_ok=True)
    return dropped


def strip_audit_archive_ips(archive_dir: Path) -> int:
    """``client_ip`` aus archivierten API-Zugriffsprotokollen entfernen.

    Archive sind ruhend (kein Schreiber), daher ohne Sperre. Eine bearbeitete Datei
    bekommt die Endung ``.noip.jsonl`` und wird danach nicht erneut gelesen. Jedes
    Archiv steht für sich: ein unlesbares hält die übrigen nicht auf, der Lauf meldet
    es am Ende als :class:`RetentionIncompleteError`.
    """
    done, dropped, failed = 0, 0, []
    for src in _unstripped_archives(archive_dir):
        try:
            dropped += _strip_one_archive(src)
        except OSError:
            logger.exception("[retention] Archiv %s nicht bereinigt", src.name)
            failed.append(src.name)
            continue
        done += 1
    if dropped:
        logger.warning("[retention] %d unlesbare Archivzeilen verworfen", dropped)
    if failed:
        raise RetentionIncompleteError(f"{len(failed)} Archiv(e) unbereinigt: {', '.join(failed)}")
    return done


def _cleared_fp(line: str, cutoff: datetime) -> str | None:
    """Die Zeile ohne Kennwert, wenn er zu leeren ist; sonst ``None``.

    Gültige Zeilen nach Alter. Eine abgerissene Zeile hat kein verlässliches Alter und
    taugt nicht als Beleg: ihr Kennwert wird sofort geleert, der Rest bleibt stehen.
    """
    row = _loads(line)
    if row is None:
        match = _TORN_FP.search(line)
        if match is None or (match.group(2) == "" and match.group(3)):
            return None
        return _TORN_FP.sub(r'\1""', line)
    stamp = _parse(row.get("ts"))
    if stamp is None or stamp >= cutoff or not row.get("requester_fp"):
        return None
    row["requester_fp"] = ""
    return json.dumps(row, ensure_ascii=False) + "\n"


def strip_demand_fingerprints(path: Path, now: datetime) -> int:
    """``requester_fp`` in Zeilen älter als :data:`IP_RETENTION` leeren (unter Sperre)."""
    if not path.exists():
        return 0
    cutoff = now - IP_RETENTION
    with append_lock(path):
        if not any(_cleared_fp(line, cutoff) is not None for line in _lines(path)):
            return 0
        changed = 0
        with _atomic_writer(path) as out, path.open("r", encoding="utf-8") as fh:
            for line in fh:
                cleared = _cleared_fp(line, cutoff)
                if cleared is not None:
                    changed += 1
                out.write(cleared if cleared is not None else line)
    return changed


def _closed_before(path: Path, cutoff: datetime) -> set[str]:
    """Fälle, deren letzter Abschluss (``status`` = ``erledigt``) vor ``cutoff`` liegt."""
    closed: dict[str, datetime] = {}
    for line in _lines(path):
        row = _loads(line)
        if row is not None and row.get(_CASE_STATUS_KEY) == _CASE_CLOSED:
            at = _parse(row.get("at"))
            if at is not None:
                closed[str(row.get("case_id"))] = at
    return {cid for cid, at in closed.items() if at < cutoff}


def prune_cases(path: Path, now: datetime) -> int:
    """Meldungen 90 Tage nach Abschluss (``erledigt``) vollständig entfernen."""
    if not path.exists():
        return 0
    with append_lock(path):
        drop = _closed_before(path, now - CASE_RETENTION_AFTER_CLOSE)
        if not drop:
            return 0
        with _atomic_writer(path) as out, path.open("r", encoding="utf-8") as fh:
            for line in fh:
                row = _loads(line)
                if row is None or str(row.get("case_id")) not in drop:
                    out.write(line)
    return len(drop)


def _live_audit_older_than(path: Path, cutoff: datetime) -> bool:
    for line in _lines(path):
        row = _loads(line)
        stamp = _parse(row.get("timestamp_utc")) if row is not None else None
        if stamp is not None:
            return stamp < cutoff
    return False


def overdue(artifacts_dir: Path, now: datetime | None = None) -> dict[str, int]:
    """Was nach dem Lauf noch gegen die Datenschutzseite verstößt; überall 0 = eingehalten."""
    now = now or datetime.now(UTC)
    promise = now - IP_PROMISE
    return {
        "audit_archives_with_ip": len(_unstripped_archives(artifacts_dir / "archive")),
        "demand_fingerprints_overdue": sum(
            1
            for line in _lines(artifacts_dir / _DEMAND_LEDGER)
            if _cleared_fp(line, promise) is not None
        ),
        "live_audit_older_than_7d": int(
            _live_audit_older_than(artifacts_dir / _LIVE_AUDIT, promise)
        ),
        "cases_overdue": len(
            _closed_before(artifacts_dir / _CASES, now - CASE_RETENTION_AFTER_CLOSE)
        ),
    }


def apply(artifacts_dir: Path, *, now: datetime | None = None) -> dict[str, int]:
    """Alle Löschfristen einmal anwenden; jede für sich fail-soft, die großen Archive zuletzt."""
    now = now or datetime.now(UTC)
    steps: dict[str, Callable[[], int]] = {
        "demand_fingerprints_cleared": lambda: strip_demand_fingerprints(
            artifacts_dir / _DEMAND_LEDGER, now
        ),
        "cases_removed": lambda: prune_cases(artifacts_dir / _CASES, now),
        "invites_removed": lambda: invites.prune(
            now=now, path=artifacts_dir / "oracle" / "invites.jsonl"
        ),
        "audit_archives_stripped": lambda: strip_audit_archive_ips(artifacts_dir / "archive"),
    }
    counts: dict[str, int] = {}
    # Ein zweiter Lauf (Timer + Handlauf) wartet hier, statt dieselben .tmp zu schreiben.
    with append_lock(artifacts_dir / "archive" / ".retention"):
        for name, step in steps.items():
            try:
                counts[name] = step()
            except Exception:  # noqa: BLE001 — eine Frist darf die anderen nicht blockieren
                logger.exception("[retention] %s fehlgeschlagen", name)
                counts[name] = -1
    return counts


__all__ = [
    "CASE_RETENTION_AFTER_CLOSE",
    "IP_PROMISE",
    "IP_RETENTION",
    "RetentionIncompleteError",
    "apply",
    "overdue",
    "prune_cases",
    "strip_audit_archive_ips",
    "strip_demand_fingerprints",
]
