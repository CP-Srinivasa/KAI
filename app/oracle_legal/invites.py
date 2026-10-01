"""Einladungen für die begleitete Oracle-Beta (Operator 01.10.2026, Beta v0.5).

Eine neue Zahlungsanforderung (HTTP 402 mit Rechnung) bekommt nur, wer einen gültigen
Einladungscode mitschickt. So bleibt die Beta auf eingeladene Teilnehmer beschränkt,
auch wenn die Oracle-Adressen öffentlich erreichbar sind. Bereits bezahlte Zugänge
und die kostenfreie Abholung eines Zeitstempels brauchen keinen Code.

Gespeichert wird nur, was die Prüfung braucht: SHA-256 des Codes, eine frei gewählte
Bezeichnung (keine E-Mail-Adresse, die Kontaktdaten liegen im Postfach des
Betreibers), Ausgabe, Ablauf und gegebenenfalls Sperre. Den Code selbst sieht der
Betreiber genau einmal beim Anlegen (``scripts/oracle_invite.py``).

Löschung: Einladungen werden 30 Tage nach ihrem Ende (Ablauf oder Sperre) entfernt
(:func:`prune`, täglich über ``scripts/audit_rotate.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.file_lock import append_lock

INVITES_PATH = Path("artifacts/oracle/invites.jsonl")
DEFAULT_DAYS = 30
RETAIN_AFTER_END = timedelta(days=30)
HEADER = "X-KAI-Invite"
QUERY = "invite"
_MAX_CODE_LEN = 64


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def _parse(ts: object) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            rows.append(row)
    return rows


def _current(path: Path) -> dict[str, dict[str, Any]]:
    """Letzter Stand je Einladung (die Datei ist append-only: Anlage, dann ggf. Sperre)."""
    state: dict[str, dict[str, Any]] = {}
    for row in _read(path):
        state[row["id"]] = {**state.get(row["id"], {}), **row}
    return state


def create(
    label: str, *, days: int = DEFAULT_DAYS, now: datetime | None = None, path: Path | None = None
) -> tuple[str, dict[str, Any]]:
    """Neue Einladung; gibt den Klartext-Code (nur jetzt sichtbar) und den Eintrag zurück."""
    path = path or INVITES_PATH
    label = " ".join(label.split())[:80]
    if not label:
        raise ValueError("Bitte eine Bezeichnung angeben (z. B. Vorname oder Partnerkürzel).")
    if not 1 <= days <= 365:
        raise ValueError("Gültigkeit 1 bis 365 Tage.")
    now = now or datetime.now(UTC)
    code = secrets.token_urlsafe(16)
    entry = {
        "id": "inv_" + secrets.token_hex(4),
        "code_sha256": _hash(code),
        "label": label,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with append_lock(path), path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)
    return code, entry


def revoke(invite_id: str, *, now: datetime | None = None, path: Path | None = None) -> None:
    path = path or INVITES_PATH
    if invite_id not in _current(path):
        raise KeyError(invite_id)
    now = now or datetime.now(UTC)
    with append_lock(path), path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": invite_id, "revoked_at": now.isoformat()}) + "\n")


def listing(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or INVITES_PATH
    return sorted(_current(path).values(), key=lambda e: str(e.get("created_at", "")))


def _end(entry: dict[str, Any]) -> datetime | None:
    ends = [d for d in (_parse(entry.get("revoked_at")), _parse(entry.get("expires_at"))) if d]
    return min(ends) if ends else None


def is_valid(code: str | None, *, now: datetime | None = None, path: Path | None = None) -> bool:
    """True, wenn der Code zu einer nicht gesperrten, nicht abgelaufenen Einladung gehört."""
    path = path or INVITES_PATH
    if not code or len(code) > _MAX_CODE_LEN:
        return False
    now = now or datetime.now(UTC)
    wanted = _hash(code)
    for entry in _current(path).values():
        if not hmac.compare_digest(str(entry.get("code_sha256", "")), wanted):
            continue
        end = _end(entry)
        return end is not None and now < end
    return False


def code_from_request(headers: Any, query: Any) -> str | None:
    """Code aus Header ``X-KAI-Invite`` oder Query-Parameter ``invite``."""
    value = headers.get(HEADER) or query.get(QUERY)
    return str(value).strip() if value else None


def prune(*, now: datetime | None = None, path: Path | None = None) -> int:
    """Einladungen 30 Tage nach ihrem Ende entfernen; gibt die Zahl entfernter zurück."""
    path = path or INVITES_PATH
    if not path.exists():
        return 0
    now = now or datetime.now(UTC)
    with append_lock(path):
        state = _current(path)
        drop = {
            i
            for i, e in state.items()
            if (end := _end(e)) is not None and now - end > RETAIN_AFTER_END
        }
        if not drop:
            return 0
        keep = [row for row in _read(path) if row["id"] not in drop]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), "utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    return len(drop)


__all__ = [
    "DEFAULT_DAYS",
    "HEADER",
    "INVITES_PATH",
    "QUERY",
    "code_from_request",
    "create",
    "is_valid",
    "listing",
    "prune",
    "revoke",
]
