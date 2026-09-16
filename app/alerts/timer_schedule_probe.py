"""Health-Sonde: wiederkehrende Timer, die laufen und trotzdem keinen Termin haben.

Vorfall 2026-08-19: ``kai-tv-auto-promote.timer`` stand auf ``enabled`` +
``active`` mit ``NextElapseUSecMonotonic=infinity`` und hatte zuletzt am
2026-07-12 gefeuert -- fuenf Wochen tot. Er fiel durch BEIDE bestehenden Netze:
``systemctl --failed`` zeigt nichts (nichts ist gescheitert), und
``pi_timer_health_probe.sh`` sammelt ``NON_ACTIVE`` (er war aktiv).

Vorfall 2026-09-15 (V4, Daily Review 2026-09-16): dieselbe Sonde meldete
``kai-technical-screener.timer`` (17:30Z) und ``kai-funding-refresh.timer``
(23:16Z) als tot. Beide feuerten nachweislich weiter. Die Sonde fragt systemd
zweimal nacheinander -- Timer-Fakten, dann den Zustand der ausgeloesten Services
-- und vergleicht zwei Momentaufnahmen. Laeuft der Oneshot waehrend der ersten
Abfrage (der Timer meldet ``infinity``) und ist er bei der zweiten schon fertig,
sieht sie "kein Termin" UND "Service laeuft nicht". Ein TOCTOU im eigenen
Waechter. Deshalb muss ein Befund jetzt eine zweite, zeitlich getrennte Messung
ueberstehen; gemeldet wird die Schnittmenge. Derselbe Weg wie bei
``process_runtime_probe``.

Eigenes Modul, nicht ein weiterer Block in ``health_check.py``: die Datei stand
bei null Headroom gegen ihre God-File-Baseline. Die Deutung liegt weiter in den
reinen, getesteten Funktionen von ``app/services/timer_health``; hier steht nur
das Einsammeln und das Bestaetigen.

Gibt den Alarmtext zurueck, nicht die ``HealthIssue`` -- ``HealthIssue`` wohnt in
``health_check`` und ein Import von dort waere zirkulaer.

Fail-soft (Lehre #718): laesst sich systemd nicht befragen, gibt es KEINEN
Befund -- auch dann nicht, wenn nur die Bestaetigungsmessung scheitert. Ein
unbestaetigter Befund ist keiner.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

#: Pause zwischen erster Messung und Bestaetigung. systemd terminiert einen
#: ``OnUnitActiveSec``-Timer neu, sobald der ausgeloeste Oneshot deaktiviert ist;
#: zwei Sekunden reichen dafuer weit, und die Pause faellt nur an, wenn die erste
#: Messung ueberhaupt einen Kandidaten hat.
RESAMPLE_DELAY_SEC: Final[float] = 2.0

_TIMEOUT_SEC: Final[int] = 30

_sleep: Callable[[float], None] = time.sleep
_run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run


def _systemctl_show(units: list[str], properties: tuple[str, ...]) -> str | None:
    """``systemctl show`` fuer die Units, oder ``None``, wenn nicht befragbar."""
    args = ["systemctl", "show", *units]
    for prop in properties:
        args += ["-p", prop]
    try:
        proc = _run(  # noqa: S603 - feste Argumentliste, kein shell
            args,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SEC,
            check=False,
            # systemctl rendert Zeitstempel in der Zone des Aufrufers; ``CEST``
            # ist nicht zurueckparsbar und wuerde jeden LastTrigger als "nie
            # gelaufen" erscheinen lassen.
            env={**os.environ, "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout


def _sample(units: list[str]) -> list[str] | None:
    """EINE Messung: welche Timer sind jetzt terminlos? ``None`` = nicht befragbar."""
    from app.services.timer_health import (
        find_unscheduled_recurring_timers,
        parse_active_units,
        parse_systemctl_show,
    )

    timer_out = _systemctl_show(
        units,
        (
            "Id",
            "UnitFileState",
            "ActiveState",
            "NextElapseUSecRealtime",
            "NextElapseUSecMonotonic",
            "LastTriggerUSec",
            "Unit",
        ),
    )
    if timer_out is None:
        return None
    facts = parse_systemctl_show(timer_out)

    # Zweite Frage: laeuft der ausgeloeste Service gerade? Waehrend ein
    # ``Type=oneshot`` laeuft, hat ``OnUnitActiveSec`` nichts zum Ankern und
    # systemd meldet ``infinity`` -- ohne diese Runde wuerde jeder laufende
    # Timer als tot gemeldet (kai-shadow-resolver: 13-14 min von je 30).
    services = sorted({f.triggered_unit for f in facts if f.triggered_unit})
    if not services:
        return None
    service_out = _systemctl_show(services, ("Id", "ActiveState"))
    if service_out is None:
        return None
    running = parse_active_units(service_out)
    return find_unscheduled_recurring_timers([f.with_triggered_state(running) for f in facts])


def unscheduled_timer_finding(timer_dir: Path | None = None) -> str | None:
    """Der Befundtext, wenn wiederkehrende Timer bestaetigt keinen Termin haben."""
    if os.environ.get("KAI_TIMER_SCHEDULE_PROBE", "").strip().lower() == "off":
        return None

    directory = (
        timer_dir
        if timer_dir is not None
        else Path(__file__).resolve().parents[2] / "deploy" / "systemd"
    )
    units = sorted(f.name for f in directory.glob("kai-*.timer"))
    if not units:
        return None

    first = _sample(units)
    if not first:
        return None

    # Bestaetigung: nur was auch eine zweite, spaetere Messung als terminlos
    # sieht, ist tot. Ein Oneshot, der zwischen den Abfragen der ersten Messung
    # endete, ist bis hierhin laengst neu terminiert.
    _sleep(RESAMPLE_DELAY_SEC)
    second = _sample(units)
    if second is None:
        return None
    stuck = sorted(set(first) & set(second))
    if not stuck:
        return None

    return (
        f"{len(stuck)} wiederkehrende Timer laufen ohne naechsten Termin "
        f"(enabled+active, aber kein NextElapse): {', '.join(stuck)} "
        "— sie feuern nie wieder. Reparatur: Unit neu starten, nachdem der "
        "zugehoerige Service einmal gelaufen ist, und auf einen "
        "restart-sicheren Trigger umstellen (OnCalendar oder OnActiveSec)."
    )
