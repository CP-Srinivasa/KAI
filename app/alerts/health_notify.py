"""Versand des Health-Befunds an den Operator-Kanal, mit Cooldown-Gate.

Aus ``app/cli/main.py`` herausgezogen (God-File-Ratchet, repo_hygiene_policy §5)
— und inhaltlich ohnehin fällig: der Versand lag dort im linearen Ablauf HINTER
dem Stale-Exit, wodurch der Health-Check genau im Ausfall schwieg, für den er
gebaut ist. Als eigene Funktion kann er vor dem Abbruch aufgerufen werden, ohne
den Ablauf zu duplizieren, und ist ohne CLI-Rahmen testbar.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Protocol

__all__ = ["HEALTH_NOTIFY_STATE_FILE", "build_health_alert_text", "dispatch_health_notification"]

HEALTH_NOTIFY_STATE_FILE = Path("artifacts") / ".health_check_last_notification"


class _Console(Protocol):
    def print(self, *args: Any, **kwargs: Any) -> None: ...


def build_health_alert_text(report: Any, *, lookback_hours: int) -> str:
    """Alarmtext aus dem Report. Rein, damit der Inhalt prüfbar ist."""
    lines = ["KAI Health Alert"]
    if report.data_sources_stale:
        lines.append("[NOTE] data sources stale — Pi-sync may be lagging")
    lines.append(
        f"Window: {lookback_hours}h · alerts={report.recent_alerts} "
        f"(actionable={report.recent_actionable_alerts}) · cycles={report.recent_cycles}"
    )
    for issue in report.issues:
        tag = "CRITICAL" if issue.severity == "critical" else "WARNING"
        lines.append(f"[{tag}] {issue.component}: {issue.message}")
    return "\n".join(lines)


def _cooldown_blocks(state_file: Path, *, now_ts: float, cooldown_minutes: float) -> float | None:
    """Verstrichene Minuten, wenn der Cooldown noch greift — sonst ``None``."""
    if not state_file.exists():
        return None
    try:
        last_ts = float(state_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        # Unlesbarer Zustand darf nicht stummschalten: lieber einmal zu viel
        # melden als einen echten Befund wegen eines kaputten Zeitstempels
        # verschlucken.
        return None
    elapsed_min = (now_ts - last_ts) / 60.0
    return elapsed_min if elapsed_min < cooldown_minutes else None


def dispatch_health_notification(
    report: Any,
    *,
    lookback_hours: int,
    notify_cooldown_minutes: float,
    console: _Console,
    state_file: Path | None = None,
) -> bool:
    """Health-Befund senden. Gibt zurück, ob tatsächlich gesendet wurde."""
    from app.alerts.notify import send_operator_notification

    path = state_file or HEALTH_NOTIFY_STATE_FILE
    now_ts = time.time()

    elapsed = _cooldown_blocks(path, now_ts=now_ts, cooldown_minutes=notify_cooldown_minutes)
    if elapsed is not None:
        console.print(
            f"[dim]Notification suppressed (cooldown: "
            f"{elapsed:.1f}/{notify_cooldown_minutes}min).[/dim]"
        )
        return False

    text = build_health_alert_text(report, lookback_hours=lookback_hours)
    ok = asyncio.run(send_operator_notification(text))
    if ok:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(now_ts), encoding="utf-8")
        except OSError:
            pass
        console.print("[green]Telegram notification sent.[/green]")
    else:
        console.print("[yellow]Telegram not configured or send failed.[/yellow]")
    return bool(ok)
