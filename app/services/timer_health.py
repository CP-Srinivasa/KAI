"""Timer Health Service — reads systemd-timer health audits (DALI-P-101)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _get_default_total() -> int:
    """Dynamically count kai-*.timer units in deploy/systemd as default timer count."""
    default_total = 10  # Standard fallback
    try:
        workspace_root = Path(__file__).resolve().parents[3]
        deploy_dir = workspace_root / "deploy" / "systemd"
        if deploy_dir.is_dir():
            default_total = len(list(deploy_dir.glob("kai-*.timer")))
    except Exception:
        pass
    return default_total


def read_latest_timer_audit(path: Path) -> dict[str, Any]:
    """Read the latest entry from the timer health audit JSONL file and return state.

    Fehlertolerant:
    - Datei fehlt oder leer -> state="no_data"
    - Letzte Zeile korrupt -> state="corrupt" mit Fallback auf vorletzte Zeile
    - checked_at älter als 2h -> state="stale" (auch wenn inactive=0)
    - inactive > 0 -> state="has_inactive"
    - sonst -> state="ok"
    """
    default_total = _get_default_total()

    default_response: dict[str, Any] = {
        "state": "no_data",
        "checked_at": None,
        "stale_minutes": None,
        "total": default_total,
        "active": default_total,
        "inactive": [],
    }

    if not path.exists():
        return default_response

    lines = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    lines.append(s)
    except Exception:
        return default_response

    if not lines:
        return default_response

    parsed_data = None
    is_corrupt = False

    # Versuche den letzten Eintrag
    try:
        parsed_data = json.loads(lines[-1])
    except Exception:
        is_corrupt = True
        # Fallback auf vorletzten Eintrag
        if len(lines) > 1:
            try:
                parsed_data = json.loads(lines[-2])
            except Exception:
                parsed_data = None
        else:
            parsed_data = None

    if parsed_data is None:
        return {
            "state": "corrupt",
            "checked_at": None,
            "stale_minutes": None,
            "total": default_total,
            "active": default_total,
            "inactive": [],
        }

    # Bestimme checked_at
    checked_at_str = parsed_data.get("timestamp_utc")
    checked_at = None
    stale_minutes = None
    state = "ok"

    if checked_at_str:
        try:
            checked_at = datetime.fromisoformat(checked_at_str.replace("Z", "+00:00"))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=UTC)
            checked_at = checked_at.astimezone(UTC)

            now = datetime.now(UTC)
            diff = now - checked_at
            stale_minutes = int(diff.total_seconds() // 60)
            if diff.total_seconds() > 7200:  # 2 Stunden = 7200 Sekunden
                state = "stale"
        except Exception:
            pass

    # Parse inaktive Timer aus findings
    raw_findings = parsed_data.get("findings", [])
    inactive_timers = []

    for f in raw_findings:
        if not isinstance(f, str) or not f.strip():
            continue
        unit_name = f
        unit_state = "inactive"
        if " (" in f and f.endswith(")"):
            parts = f.rsplit(" (", 1)
            unit_name = parts[0]
            unit_state = parts[1][:-1]

        inactive_timers.append(
            {
                "unit": unit_name,
                "state": unit_state,
                "last_trigger": None,
            }
        )

    # Resolve total und active
    total_from_audit = parsed_data.get("total_timers")
    active_from_audit = parsed_data.get("active_timers")

    total = default_total
    if total_from_audit is not None:
        try:
            total = int(total_from_audit)
        except Exception:
            pass
    elif active_from_audit is not None:
        try:
            total = int(active_from_audit) + len(inactive_timers)
        except Exception:
            pass

    if len(inactive_timers) > total:
        total = len(inactive_timers)
    active = total - len(inactive_timers)

    # Status-Priorisierung
    if is_corrupt:
        state = "corrupt"
    elif state != "stale":
        if len(inactive_timers) > 0:
            state = "has_inactive"
        else:
            state = "ok"

    return {
        "state": state,
        "checked_at": checked_at_str,
        "stale_minutes": stale_minutes,
        "total": total,
        "active": active,
        "inactive": inactive_timers,
    }
