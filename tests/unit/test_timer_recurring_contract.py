r"""RECURRING-Timer muessen nach jedem Neustart wieder einen Termin haben.

Vorfall 2026-08-19: ``kai-tv-auto-promote.timer`` meldete ``enabled`` UND
``active``, besass aber ``NextElapseUSecMonotonic=infinity`` und hatte zuletzt am
**2026-07-12 12:39:45** gefeuert — fuenf Wochen tot. ``systemctl --failed`` zeigt
so etwas nicht, und die Timer-Probe sammelt ``NON_ACTIVE`` — ein aktiver Timer
ohne Termin faellt durch beide Netze.

Die Fehlbedingung, live nachgemessen:

* ``OnBootSec=`` feuert EINMAL nach dem Boot. Wird der Timer spaeter neu
  gestartet, ist dieser Trigger vorbei.
* ``OnUnitActiveSec=`` verankert sich an der letzten Aktivierung des
  ausgeloesten SERVICE. Lief der Service lange nicht, existiert kein Anker.

Ein Timer mit ausschliesslich diesen beiden Angaben, der lange nach dem Boot neu
gestartet wird, bekommt daher NIE wieder einen Termin. Genau das ist passiert.

Gegenprobe aus demselben Vorfall: ``kai-oracle-earnings-booking`` und
``kai-premium-healthcheck`` haben dieselbe Bauform, wurden am selben Tag ebenfalls
neu gestartet — und blieben gesund, weil ihr Service einen frischen Anker hatte.
Die Bauform allein toetet nicht; sie macht verwundbar.

``OnActiveSec=`` verankert sich am TIMER selbst und ueberlebt jeden Neustart. Der
Contract verlangt deshalb NICHT ``OnCalendar``, sondern nur, dass ein
restart-sicherer Initial-Trigger existiert — ``OnCalendar`` ODER ``OnActiveSec``.

Zweiter Befund derselben Messung: ``Persistent=`` wirkt laut systemd
ausschliesslich bei ``OnCalendar=``. An einem rein monotonen Timer ist es
wirkungslos — eine Zusicherung, die nichts zusichert. Das stand 15x im Repo.
"""

from __future__ import annotations

import re
from pathlib import Path

_TIMER_DIR = Path(__file__).resolve().parents[2] / "deploy" / "systemd"


def _field(text: str, key: str) -> str | None:
    match = re.search(rf"^\s*{key}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _timers() -> list[tuple[str, str]]:
    files = sorted(_TIMER_DIR.glob("*.timer"))
    assert files, "keine Timer-Units gefunden — Pfad kaputt?"
    return [(f.name, f.read_text(encoding="utf-8")) for f in files]


def test_every_recurring_timer_has_a_restart_safe_initial_trigger() -> None:
    """Der Kern: ohne OnCalendar ODER OnActiveSec ueberlebt kein Timer einen Neustart.

    ``OnBootSec`` allein genuegt NICHT — es ist genau die Konstellation, die
    ``kai-tv-auto-promote`` fuenf Wochen lang tot liegen liess.
    """
    offenders = []
    for name, text in _timers():
        if _field(text, "OnCalendar"):
            continue  # kalenderbasiert = restart-sicher
        if _field(text, "OnActiveSec"):
            continue  # timer-eigener Anker = restart-sicher
        offenders.append(f"{name} (OnBootSec={_field(text, 'OnBootSec')})")

    assert not offenders, (
        "Diese Timer haben keinen restart-sicheren Initial-Trigger und koennen "
        "nach einem Neustart dauerhaft ohne Termin bleiben "
        "(NextElapse=infinity):\n  " + "\n  ".join(offenders) + "\n"
        "Reparatur: OnBootSec=X durch OnActiveSec=X ersetzen (am Boot identisch, "
        "ueberlebt zusaetzlich jeden Restart) — oder OnCalendar= setzen."
    )


def test_every_recurring_timer_has_a_recurrence_trigger() -> None:
    """Ein Initial-Trigger allein macht noch keinen wiederkehrenden Timer."""
    offenders = []
    for name, text in _timers():
        if _field(text, "OnCalendar"):
            continue  # OnCalendar ist selbst wiederkehrend
        if _field(text, "OnUnitActiveSec"):
            continue
        offenders.append(name)

    assert not offenders, "Diese Timer haben keinen Wiederholungstrigger:\n  " + "\n  ".join(
        offenders
    )


def test_persistent_only_where_it_actually_works() -> None:
    """``Persistent=`` ohne ``OnCalendar`` ist eine Zusicherung, die nichts zusichert.

    systemd wertet ``Persistent=`` ausschliesslich bei kalenderbasierten Timern
    aus. Stand 2026-08-19 trugen **15** rein monotone Timer die Angabe — und in
    #729 wurde eine Kadenz-Senkung sogar damit BEGRUENDET, dass Persistent einen
    verpassten Lauf nachhole. Falsche Begruendungen im Repo sind schlimmer als
    fehlende, weil sie beim naechsten Mal geglaubt werden.
    """
    offenders = []
    for name, text in _timers():
        if _field(text, "Persistent") is None:
            continue
        if _field(text, "OnCalendar"):
            continue
        offenders.append(name)

    assert not offenders, (
        "Diese Timer tragen Persistent= ohne OnCalendar — dort ist es wirkungslos:\n  "
        + "\n  ".join(offenders)
    )


def test_the_incident_unit_is_calendar_based() -> None:
    """``kai-tv-auto-promote`` war der Ausloeser und wird ausdruecklich gepinnt."""
    text = (_TIMER_DIR / "kai-tv-auto-promote.timer").read_text(encoding="utf-8")
    assert _field(text, "OnCalendar") == "*:0/5"
    assert _field(text, "Persistent") == "true", (
        "bei OnCalendar wirkt Persistent — und wird hier gebraucht"
    )


def test_oracle_timer_stays_monotonic_with_its_own_anchor() -> None:
    """Produktionsverhalten wird NICHT geaendert, nur restart-sicher gemacht.

    Der Timer bleibt bei ~60 min zwischen Aktivierungen; er bekommt lediglich
    einen timer-eigenen Initialanker. Ihn auf ``OnCalendar=hourly`` umzubauen,
    nur damit ein frueherer Kommentar wieder wahr wird, waere die falsche
    Richtung gewesen.
    """
    text = (_TIMER_DIR / "kai-oracle-earnings-booking.timer").read_text(encoding="utf-8")
    assert _field(text, "OnCalendar") is None
    assert _field(text, "OnActiveSec") == "60min"
    assert _field(text, "OnUnitActiveSec") == "60min"
    assert _field(text, "Persistent") is None, "wirkungslos ohne OnCalendar — muss weg"


def test_no_timer_lost_its_schedule_entirely() -> None:
    """Gegenprobe zur Sanierung: kein Timer darf durch die Umstellung leer werden."""
    for name, text in _timers():
        has_schedule = any(
            _field(text, key)
            for key in ("OnCalendar", "OnActiveSec", "OnBootSec", "OnUnitActiveSec")
        )
        assert has_schedule, f"{name} hat gar keinen Trigger mehr"
