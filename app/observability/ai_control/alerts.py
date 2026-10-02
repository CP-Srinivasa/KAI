"""Telegram-Hinweise der KI-Kontrollstation (Spec §6.1).

Jeder Hinweis kommt einmal; nach ``remind_after_hours`` eine Erinnerung; verschwindet er,
einmal „behoben“. In der Ruhezeit wird gesammelt und morgens in EINER Nachricht
nachgeliefert -- ausser ``crit`` (Stoerung auf einer ``primary``-Route).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.observability.ai_control.config import ControlThresholds


def is_quiet(now: datetime, thresholds: ControlThresholds) -> bool:
    stunde = now.astimezone(ZoneInfo(thresholds.quiet_tz)).hour
    a, b = thresholds.quiet_start_hour, thresholds.quiet_end_hour
    if a == b:
        return False
    return (stunde >= a or stunde < b) if a > b else (a <= stunde < b)


def plan(
    attention: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    now: datetime,
    thresholds: ControlThresholds,
) -> tuple[str | None, dict[str, Any]]:
    """Eine Nachricht (oder ``None``) und der neue Zustand. Rein, ohne Versand."""
    offen: dict[str, Any] = dict(state.get("open") or {})
    pending: list[str] = list(state.get("pending") or [])
    ruhe = is_quiet(now, thresholds)
    zeilen: list[str] = []
    stempel = now.isoformat()

    def ausgeben(text: str, kritisch: bool) -> None:
        if ruhe and not kritisch:
            pending.append(text)
        else:
            zeilen.append(text)

    aktuelle = {a["key"]: a for a in attention}
    for key, a in aktuelle.items():
        eintrag = offen.setdefault(
            key, {"first_seen": stempel, "last_sent": None, "title": a["title"]}
        )
        eintrag["expires"] = bool(a.get("expires"))
        alter = now - datetime.fromisoformat(eintrag["first_seen"])
        if alter < timedelta(minutes=int(a.get("min_age_min") or 0)):
            continue
        kritisch = a.get("severity") == "crit"
        symbol = "⛔" if kritisch else "⚠"
        if eintrag["last_sent"] is None:
            ausgeben(f"{symbol} {a['title']}: {a['detail']}", kritisch)
            eintrag["last_sent"] = stempel
        elif now - datetime.fromisoformat(eintrag["last_sent"]) >= timedelta(
            hours=thresholds.remind_after_hours
        ):
            ausgeben(f"↻ weiterhin: {a['title']}: {a['detail']}", kritisch)
            eintrag["last_sent"] = stempel
    for key in [k for k in offen if k not in aktuelle]:
        eintrag = offen.pop(key)
        # Ein Tages- oder Monatshinweis endet mit seinem Zeitraum -- behoben ist dabei nichts.
        if eintrag.get("last_sent") and not eintrag.get("expires"):
            ausgeben(f"✓ behoben: {eintrag['title']}", False)
    if not ruhe and pending:
        zeilen = ["Nachtrag aus der Ruhezeit:", *pending, *zeilen]
        pending = []
    text = ("KI-Kontrolle\n" + "\n".join(zeilen)) if zeilen else None
    return text, {"open": offen, "pending": pending}


def load_state(path: Path) -> dict[str, Any]:
    try:
        daten = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return daten if isinstance(daten, dict) else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ai-alert-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, sort_keys=True)
    os.replace(tmp, path)


__all__ = ["is_quiet", "load_state", "plan", "save_state"]
