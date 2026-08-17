#!/usr/bin/env python3
"""Meldet eine fehlgeschlagene systemd-Unit an den Operator-Kanal.

Wird über ``OnFailure=kai-unit-failure-notify@%n.service`` aus der scheiternden
Unit heraus gestartet. Vorher gab es diesen Weg NICHT: keine der 59 Units trug
ein ``OnFailure=``, und 17 maskierten ihren Exit-Code zusätzlich mit
``ExecStart=-``. Ein kaputter Job war damit von einem gesunden nicht zu
unterscheiden — dieselbe Familie wie der 6 Tage unbemerkte TV-Ingest-Tod.

Bewusst hart gegen Selbstschaden:
* **Rate-Limit je Unit** (6 h). Eine dauerhaft scheiternde Unit meldet sich
  einmal, nicht bei jedem Timer-Lauf — sonst trainiert der Kanal den Operator
  darauf, ihn zu ignorieren.
* **fail-open**: ist der Zustand unlesbar, wird GESENDET. Ein kaputter
  Zähler darf niemals einen echten Ausfall verschlucken.
* **kein eigenes OnFailure**: scheitert der Notifier, bleibt es bei einem
  Journal-Eintrag. Ein Alarm, der sich selbst alarmiert, kaskadiert.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ein Ausfall ist ein Zustand, kein Ereignis: 6 h zwischen zwei Meldungen
# derselben Unit reichen, um ihn präsent zu halten, ohne den Kanal zu fluten.
COOLDOWN = timedelta(hours=6)
DEFAULT_STATE_PATH = Path("artifacts/unit_failure_notify_state.json")
# Telegram kappt lange Nachrichten; eine abgeschnittene Meldung ist keine.
_MAX_MESSAGE = 3900
_MAX_JOURNAL = 2500


def build_message(unit: str, *, exit_code: str, result: str, journal_tail: str) -> str:
    """Operator-Text: WAS ist gescheitert, WIE, und was stand zuletzt im Log."""
    head = f"🔴 systemd-Unit fehlgeschlagen: {unit}"
    details = []
    if result:
        details.append(f"result={result}")
    if exit_code:
        details.append(f"exit={exit_code}")
    if details:
        head += f"\n({', '.join(details)})"

    tail = journal_tail.strip()
    if len(tail) > _MAX_JOURNAL:
        # Das ENDE des Logs trägt die Ursache, nicht der Anfang.
        tail = "…(gekürzt)…\n" + tail[-_MAX_JOURNAL:]
    if tail:
        head += f"\n\n{tail}"

    if len(head) > _MAX_MESSAGE:
        head = head[:_MAX_MESSAGE]
    return head


def _load_state(state_path: Path) -> dict[str, str]:
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # fail-open: lieber melden als schweigen
    return raw if isinstance(raw, dict) else {}


def should_send(unit: str, *, state_path: Path, now: datetime) -> bool:
    """True, wenn diese Unit seit ``COOLDOWN`` nicht gemeldet wurde.

    Der Zustand wird bei True sofort fortgeschrieben, damit zwei fast
    gleichzeitige Fehlschläge nicht doppelt melden.
    """
    state = _load_state(state_path)
    previous = state.get(unit)
    if previous:
        try:
            last = datetime.fromisoformat(previous)
        except ValueError:
            last = None
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if now - last < COOLDOWN:
                return False

    state[unit] = now.isoformat()
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # Nicht schreiben zu koennen darf das Melden nicht verhindern.
    return True


def _systemctl_show(unit: str, prop: str) -> str:
    try:
        out = subprocess.run(  # noqa: S603
            ["systemctl", "show", "-p", prop, "--value", unit],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _journal_tail(unit: str, lines: int = 20) -> str:
    try:
        out = subprocess.run(  # noqa: S603
            ["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "cat"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: notify_unit_failure.py <unit-name>", file=sys.stderr)
        return 2
    unit = argv[1]

    if not should_send(unit, state_path=DEFAULT_STATE_PATH, now=datetime.now(UTC)):
        print(f"suppressed (cooldown): {unit}")
        return 0

    message = build_message(
        unit,
        exit_code=_systemctl_show(unit, "ExecMainStatus"),
        result=_systemctl_show(unit, "Result"),
        journal_tail=_journal_tail(unit),
    )

    import asyncio

    from app.alerts.notify import send_operator_notification

    sent = asyncio.run(send_operator_notification(message))
    print(f"{'sent' if sent else 'NOT sent (channel disabled/failed)'}: {unit}")
    # Exit 0 auch bei nicht erreichtem Kanal: der Notifier soll nicht selbst
    # rot werden und eine zweite Fehlerquelle vortäuschen. Das Journal traegt
    # den Befund.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
