r"""``systemctl show`` uebersetzen — die ungetestete Naht unter Invariante 1.

Die Deutungs-Funktionen (``has_future_trigger``,
``find_unscheduled_recurring_timers``) waren ab Tag 1 getestet. Der Parser, der
sie fuettert, war es nicht — und genau dort sass der Fehler.

Vorfall 2026-08-21: der Waechter meldete elf wiederkehrende Timer als terminlos.
Live nachgemessen war **keiner** davon terminlos, waehrend
``kai-tv-auto-promote.timer`` — der Vorfall, fuer den der Waechter gebaut wurde
— unentdeckt weiterlief. Ursache: ``systemctl show`` gibt ``Id=`` NICHT als
erste Property aus (real steht es an vierter Stelle, hinter den
NextElapse-Feldern). Ein Parser, der den Block an ``Id=`` schneidet, verwirft
die Werte der ersten Unit und haengt jeder folgenden Unit die Werte ihres
NACHFOLGERS an. 55 von 55 Fakten trugen fremde Termine.

Die Fixtures hier sind woertlich von ``kai-pi5`` kopiert — inklusive
Property-Reihenfolge, Leerzeile zwischen den Bloecken und leerem
Realtime-Feld bei monotonen Timern.
"""

from __future__ import annotations

from datetime import UTC

from app.services.timer_health import (
    find_unscheduled_recurring_timers,
    parse_active_units,
    parse_systemctl_show,
    parse_systemd_timestamp,
)

# Woertliche Ausgabe von `systemctl show <3 timer> -p Id -p UnitFileState
# -p ActiveState -p NextElapseUSecRealtime -p NextElapseUSecMonotonic
# -p LastTriggerUSec -p Unit` auf kai-pi5, 2026-08-21.
_SHOW_TIMERS = """\
Unit=kai-audit-rotate.service
NextElapseUSecRealtime=Sat 2026-08-22 04:40:00 CEST
NextElapseUSecMonotonic=0
LastTriggerUSec=Fri 2026-08-21 04:40:00 CEST
Id=kai-audit-rotate.timer
ActiveState=active
UnitFileState=enabled

Unit=kai-auto-annotate.service
NextElapseUSecRealtime=
NextElapseUSecMonotonic=2month 3w 4d 14h 47min 51.998367s
LastTriggerUSec=Fri 2026-08-21 03:10:46 CEST
Id=kai-auto-annotate.timer
ActiveState=active
UnitFileState=enabled

Unit=kai-tv-auto-promote.service
NextElapseUSecRealtime=
NextElapseUSecMonotonic=infinity
LastTriggerUSec=Sun 2026-07-12 12:39:45 CEST
Id=kai-tv-auto-promote.timer
ActiveState=active
UnitFileState=enabled
"""

_RECURRING = {"category_of": lambda _u: "recurring_required"}


def _by_unit(output: str) -> dict[str, object]:
    return {f.unit: f for f in parse_systemctl_show(output)}


def test_id_is_not_the_first_property_and_values_stay_with_their_unit() -> None:
    """Der Kern des Fehlers: kein Feld darf zur Nachbar-Unit wandern."""
    facts = _by_unit(_SHOW_TIMERS)

    assert facts["kai-audit-rotate.timer"].next_elapse_realtime == "Sat 2026-08-22 04:40:00 CEST"
    assert facts["kai-audit-rotate.timer"].next_elapse_monotonic == "0"
    assert facts["kai-auto-annotate.timer"].next_elapse_realtime == ""
    assert facts["kai-auto-annotate.timer"].next_elapse_monotonic.startswith("2month")
    assert facts["kai-tv-auto-promote.timer"].next_elapse_monotonic == "infinity"


def test_first_unit_is_not_dropped() -> None:
    """Der Block vor dem ersten ``Id=`` wurde komplett verworfen."""
    facts = parse_systemctl_show(_SHOW_TIMERS)

    assert [f.unit for f in facts] == [
        "kai-audit-rotate.timer",
        "kai-auto-annotate.timer",
        "kai-tv-auto-promote.timer",
    ]


def test_last_unit_keeps_its_own_schedule() -> None:
    """Die letzte Unit erbte vorher gar nichts und galt damit immer als tot."""
    last = _by_unit(_SHOW_TIMERS)["kai-tv-auto-promote.timer"]

    assert last.last_trigger_utc is not None
    assert last.last_trigger_utc.year == 2026
    assert last.last_trigger_utc.month == 7


def test_only_the_real_incident_is_reported() -> None:
    """Live-Gegenprobe vom 2026-08-21 in einem Test.

    Vorher: zwei gesunde Timer gemeldet, der kranke uebersehen. Nachher genau
    umgekehrt — und das ist der einzige Zustand, der den Kanal wert haelt.
    """
    found = find_unscheduled_recurring_timers(parse_systemctl_show(_SHOW_TIMERS), **_RECURRING)

    assert found == ["kai-tv-auto-promote.timer"]


def test_triggered_unit_property_is_read() -> None:
    facts = _by_unit(_SHOW_TIMERS)

    assert facts["kai-audit-rotate.timer"].triggered_unit == "kai-audit-rotate.service"


def test_single_unit_output_without_separator_still_parses() -> None:
    """Bei EINER Unit gibt ``systemctl show`` keine Leerzeile aus."""
    single = "\n".join(_SHOW_TIMERS.splitlines()[:7]) + "\n"

    facts = parse_systemctl_show(single)

    assert len(facts) == 1
    assert facts[0].next_elapse_realtime == "Sat 2026-08-22 04:40:00 CEST"


def test_block_without_id_is_skipped_not_merged() -> None:
    """Eine unvollstaendige Antwort darf nicht in den Nachbarblock lecken."""
    facts = parse_systemctl_show("ActiveState=active\nUnitFileState=enabled\n\n" + _SHOW_TIMERS)

    assert [f.unit for f in facts] == [
        "kai-audit-rotate.timer",
        "kai-auto-annotate.timer",
        "kai-tv-auto-promote.timer",
    ]


# ── Laufender Service: legitim terminlos ────────────────────────────────────


_SHOW_SERVICES = """\
Id=kai-shadow-resolver.service
ActiveState=activating

Id=kai-audit-rotate.service
ActiveState=inactive
"""


def test_parse_active_units_reads_activating_as_running() -> None:
    """Ein laufender ``Type=oneshot`` steht in ``activating``, nicht ``active``."""
    running = parse_active_units(_SHOW_SERVICES)

    assert running == {"kai-shadow-resolver.service"}


def test_timer_whose_service_is_running_is_not_reported() -> None:
    """``OnUnitActiveSec`` hat waehrend des Laufs nichts zum Ankern.

    ``kai-shadow-resolver`` laeuft 13-14 min von je 30 — der Timer ist damit
    fast die Haelfte der Zeit ohne Termin, voellig regulaer. Ohne diese
    Unterdrueckung tauscht der Fix eine Fehlalarm-Klasse gegen die naechste.
    """
    facts = parse_systemctl_show(_SHOW_TIMERS)
    running = {"kai-tv-auto-promote.service"}
    facts = [f.with_triggered_state(running) for f in facts]

    assert find_unscheduled_recurring_timers(facts, **_RECURRING) == []


# ── Zeitstempel ─────────────────────────────────────────────────────────────


def test_cest_timestamp_is_converted_not_dropped() -> None:
    """``%Z`` parst ``CEST`` nicht — vorher war JEDER LastTrigger ``None``.

    Und ``None`` heisst in ``find_stalled_recurring_timers`` "nie gelaufen" und
    wird uebersprungen: die Kadenz-Invariante waere flottenweit blind gewesen.
    """
    ts = parse_systemd_timestamp("Fri 2026-08-21 04:40:00 CEST")

    assert ts is not None
    assert (ts.hour, ts.minute, ts.tzinfo) == (2, 40, UTC)


def test_utc_timestamp_roundtrips() -> None:
    ts = parse_systemd_timestamp("Fri 2026-08-21 02:40:00 UTC")

    assert ts is not None
    assert (ts.hour, ts.tzinfo) == (2, UTC)


def test_unknown_zone_is_silence_not_a_guess() -> None:
    """Eine um Stunden verschobene Kadenz-Aussage waere unsichtbar falsch."""
    assert parse_systemd_timestamp("Fri 2026-08-21 04:40:00 NZDT") is None


def test_missing_timestamp_is_none() -> None:
    assert parse_systemd_timestamp("n/a") is None
    assert parse_systemd_timestamp("") is None
