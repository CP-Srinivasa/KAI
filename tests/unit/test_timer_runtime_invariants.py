r"""Zwei Laufzeit-Invarianten fuer wiederkehrende Timer.

Der Vorfall vom 2026-08-19 fiel durch **beide** bestehenden Netze:

* ``systemctl --failed`` zeigte nichts — es war ja nichts gescheitert.
* ``scripts/pi_timer_health_probe.sh`` sammelt ``NON_ACTIVE`` — der Timer WAR
  aktiv.

``kai-tv-auto-promote.timer`` stand auf ``enabled`` + ``active`` mit
``NextElapseUSecMonotonic=infinity`` und hatte zuletzt am 2026-07-12 gefeuert.
Fuenf Wochen lang meldete niemand etwas.

Die erste Invariante schliesst genau diese Luecke. Die zweite faengt den Fall,
den die erste NICHT sieht: ein Termin existiert, aber es laeuft trotzdem nichts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.timer_health import (
    TimerRuntimeFacts,
    find_stalled_recurring_timers,
    find_unscheduled_recurring_timers,
    has_future_trigger,
)

_NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _facts(unit: str, **kw) -> TimerRuntimeFacts:
    base = {
        "enabled": True,
        "active": True,
        "next_elapse_realtime": "",
        "next_elapse_monotonic": "2month 3w 2d 18h",
    }
    base.update(kw)
    return TimerRuntimeFacts(unit=unit, **base)  # type: ignore[arg-type]


# ── has_future_trigger ──────────────────────────────────────────────────────


def test_monotonic_infinity_means_no_trigger() -> None:
    """Der Realwert aus dem Vorfall."""
    assert not has_future_trigger(_facts("kai-tv-auto-promote", next_elapse_monotonic="infinity"))


def test_calendar_timer_with_empty_monotonic_is_healthy() -> None:
    """Ein Kalender-Timer traegt NUR das Realtime-Feld.

    Wer nur ``NextElapseUSecMonotonic`` liest, haelt jeden Kalender-Timer fuer
    tot — und wer nur Realtime liest, jeden monotonen. Beide Felder zaehlen.
    """
    assert has_future_trigger(
        _facts(
            "kai-truth-anchor",
            next_elapse_realtime="Wed 2026-08-20 04:35:00 UTC",
            next_elapse_monotonic="",
        )
    )


def test_monotonic_timer_with_empty_realtime_is_healthy() -> None:
    assert has_future_trigger(
        _facts("kai-funding-refresh", next_elapse_realtime="", next_elapse_monotonic="1h 2min")
    )


def test_both_empty_means_no_trigger() -> None:
    assert not has_future_trigger(
        _facts("kai-x", next_elapse_realtime="", next_elapse_monotonic="")
    )


# ── Invariante 1: Scheduleability ───────────────────────────────────────────


def test_the_real_incident_is_detected() -> None:
    """Der Zustand, der fuenf Wochen unbemerkt blieb."""
    incident = _facts("kai-tv-auto-promote", next_elapse_monotonic="infinity")
    healthy = _facts("kai-funding-refresh")

    found = find_unscheduled_recurring_timers(
        [incident, healthy], category_of=lambda _u: "recurring_required"
    )

    assert found == ["kai-tv-auto-promote"]


def test_one_shot_without_trigger_is_not_reported() -> None:
    """Ein abgelaufener One-Shot hat legitim keinen naechsten Termin.

    Ihn zu melden waere ein Daueralarm — und ein Kanal, der Bekanntes
    wiederholt, wird ignoriert.
    """
    one_shot = _facts("kai-risk-gate-audit-review", next_elapse_monotonic="infinity")

    found = find_unscheduled_recurring_timers(
        [one_shot], category_of=lambda _u: "one_shot_expected_inactive"
    )

    assert found == []


def test_inactive_or_disabled_timer_is_not_reported_here() -> None:
    """Das ist der Job der bestehenden Probe, nicht dieser Invariante.

    Sie beantwortet ausschliesslich: laeuft dieser Timer und hat trotzdem keinen
    Termin? Ein gestoppter Timer ist ein anderer Befund und wird nicht doppelt
    gemeldet.
    """
    stopped = _facts("kai-x", active=False, next_elapse_monotonic="infinity")
    disabled = _facts("kai-y", enabled=False, next_elapse_monotonic="infinity")

    found = find_unscheduled_recurring_timers(
        [stopped, disabled], category_of=lambda _u: "recurring_required"
    )

    assert found == []


# ── Invariante 2: Cadence ───────────────────────────────────────────────────


def test_stalled_timer_is_detected_despite_having_a_trigger() -> None:
    """Termin vorhanden, trotzdem laeuft nichts — was Invariante 1 nicht sieht."""
    stalled = _facts("kai-funding-refresh", last_trigger_utc=_NOW - timedelta(hours=6))

    found = find_stalled_recurring_timers(
        [stalled], now=_NOW, expected_interval_s={"kai-funding-refresh": 300.0}
    )

    assert found == ["kai-funding-refresh"]


def test_grace_factor_tolerates_normal_jitter() -> None:
    """Erst das Dreifache der Kadenz meldet — ein flatternder Waechter wird ignoriert."""
    recent = _facts("kai-funding-refresh", last_trigger_utc=_NOW - timedelta(minutes=12))

    found = find_stalled_recurring_timers(
        [recent], now=_NOW, expected_interval_s={"kai-funding-refresh": 300.0}
    )

    assert found == []


def test_unknown_expectation_is_silence_not_a_guess() -> None:
    """Ohne bekannte Kadenz ist die Frage unbeantwortbar — Raten waere schlimmer."""
    unknown = _facts("kai-mystery", last_trigger_utc=_NOW - timedelta(days=30))

    assert find_stalled_recurring_timers([unknown], now=_NOW, expected_interval_s={}) == []


def test_never_run_timer_is_not_reported_as_stalled() -> None:
    """Nie gelaufen ist kein Kadenz-Befund — dafuer ist Invariante 1 zustaendig."""
    never = _facts("kai-fresh", last_trigger_utc=None)

    found = find_stalled_recurring_timers(
        [never], now=_NOW, expected_interval_s={"kai-fresh": 300.0}
    )

    assert found == []
