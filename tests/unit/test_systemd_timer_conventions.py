"""systemd-Timer-Konventionen, dauerhaft gepinnt (Voll-Audit 2026-08-06, WP9).

`Requires=` auf einem Timer koppelt ihn hart an seine .service: ein
`systemctl stop`/Fehler der Unit propagiert und strandet den Timer bis zum
Reboot (Kaskaden-Lehre #414, kai_timer_requires_cascade_20260624 — im Repo
11× als Kommentar dokumentiert, trotzdem trugen 14 Timer das Anti-Pattern,
darunter kai-server-health-watchdog.timer, der auf der Watchdog-Exclude-Liste
steht und damit KEIN Sicherheitsnetz hatte). systemd triggert die .service
per Namenskonvention ohnehin ohne Requires=.
"""

from __future__ import annotations

from pathlib import Path

_TIMER_DIR = Path(__file__).resolve().parents[2] / "deploy" / "systemd"


def test_no_timer_unit_carries_requires() -> None:
    timers = sorted(_TIMER_DIR.glob("*.timer"))
    assert timers, "keine Timer-Units gefunden — Pfad kaputt?"
    offenders = [
        t.name
        for t in timers
        if any(
            line.strip().startswith("Requires=")
            for line in t.read_text(encoding="utf-8").splitlines()
        )
    ]
    assert offenders == [], (
        f"Timer mit Requires= (Kaskaden-Anti-Pattern #414): {offenders} — "
        "ein failed oneshot darf seinen Timer nicht bis zum Reboot stranden"
    )
