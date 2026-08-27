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
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Invariante 2 braucht eine Kadenz-Erwartung. Die einzige ehrliche Quelle dafuer
# sind die Unit-Dateien selbst: ``OnUnitActiveSec=`` sagt, in welchem Abstand
# der Timer nachfeuern soll. ``OnCalendar=`` sagt das NICHT ohne
# Kalender-Expansion — solche Timer bleiben bewusst unbewertet.
# ---------------------------------------------------------------------------


def test_timespan_parses_the_forms_that_actually_occur() -> None:
    """Nur was in deploy/systemd/ vorkommt, muss verstanden werden."""
    from app.services.timer_health import parse_systemd_timespan

    assert parse_systemd_timespan("300s") == 300.0
    assert parse_systemd_timespan("5min") == 300.0
    assert parse_systemd_timespan("10min") == 600.0
    assert parse_systemd_timespan("1h") == 3600.0
    assert parse_systemd_timespan("60min") == 3600.0
    assert parse_systemd_timespan("6h") == 21600.0
    assert parse_systemd_timespan("24h") == 86400.0
    assert parse_systemd_timespan("1w") == 604800.0


def test_timespan_handles_compound_and_bare_numbers() -> None:
    """systemd erlaubt zusammengesetzte Spannen; eine blanke Zahl sind Sekunden."""
    from app.services.timer_health import parse_systemd_timespan

    assert parse_systemd_timespan("1h 30min") == 5400.0
    assert parse_systemd_timespan("2h15min") == 8100.0
    assert parse_systemd_timespan("90") == 90.0


def test_timespan_refuses_to_guess() -> None:
    """Unverstandenes ergibt ``None`` — eine geratene Kadenz waere schlimmer.

    Eine falsch geratene Erwartung erzeugt entweder Daueralarm oder deckt einen
    echten Stillstand zu; beides ist schlechter als ein ehrliches "weiss ich
    nicht", das den Timer schlicht unbewertet laesst.
    """
    from app.services.timer_health import parse_systemd_timespan

    for raw in ("", "   ", "Mon *-*-* 04:00:00", "infinity", "abc", "5 Fische", "-30min"):
        assert parse_systemd_timespan(raw) is None, raw


def test_expected_intervals_cover_only_onunitactivesec_timers() -> None:
    """Die Abdeckung wird nicht behauptet, sondern gemessen.

    ``OnCalendar``-Timer tauchen NICHT auf. Das ist kein Versehen: ihre Kadenz
    ergibt sich erst aus der Kalender-Expansion, und ein stillschweigend
    unterstellter Abstand waere geraten.
    """
    from pathlib import Path

    from app.services.timer_health import expected_intervals_from_unit_dir

    unit_dir = Path(__file__).resolve().parents[2] / "deploy" / "systemd"
    intervals = expected_intervals_from_unit_dir(unit_dir)

    assert intervals, "kein einziger Timer mit ableitbarer Kadenz — Regression"
    assert all(v > 0 for v in intervals.values())
    assert all(u.endswith(".timer") for u in intervals)

    # Gegenprobe an der Quelle: genau die Units mit OnUnitActiveSec sind drin.
    with_cadence = {
        f.name
        for f in unit_dir.glob("kai-*.timer")
        if "OnUnitActiveSec=" in f.read_text(encoding="utf-8")
    }
    assert set(intervals) == with_cadence


# ---------------------------------------------------------------------------
# Abnahme: die Abdeckung muss GENANNT werden, und die Population muss aufgehen.
# ---------------------------------------------------------------------------


def test_timer_population_categories_sum_to_the_total() -> None:
    """Jede Unit landet in genau einer Kategorie — sonst ist die Abdeckung erfunden.

    Bezugsmenge hier: die im Repo definierten Units (``deploy/systemd/kai-*.timer``).
    Das ist NICHT dieselbe Menge wie "live installiert" oder "enabled"; der
    Health-Check bezieht seine Abdeckung deshalb auf das, was systemd zur
    Laufzeit tatsaechlich kannte.
    """
    import re
    from pathlib import Path as _Path

    unit_dir = _Path(__file__).resolve().parents[2] / "deploy" / "systemd"
    files = sorted(unit_dir.glob("kai-*.timer"))
    assert files, "keine Timer-Units gefunden — Regression"

    on_active, on_calendar, other, unclassified = [], [], [], []
    for f in files:
        text = f.read_text(encoding="utf-8")
        if re.search(r"^\s*OnUnitActiveSec=", text, re.M):
            on_active.append(f.name)
        elif re.search(r"^\s*OnCalendar=", text, re.M):
            on_calendar.append(f.name)
        elif any(
            re.search(rf"^\s*{k}=", text, re.M)
            for k in ("OnBootSec", "OnActiveSec", "OnStartupSec", "OnUnitInactiveSec")
        ):
            other.append(f.name)
        else:
            unclassified.append(f.name)

    buckets = on_active + on_calendar + other + unclassified
    assert len(buckets) == len(files), "Summenprobe verletzt"
    assert len(set(buckets)) == len(buckets), "Kategorien ueberschneiden sich"
    assert not unclassified, f"unklassifizierte Timer: {unclassified}"

    # Genau die OnUnitActiveSec-Units sind kadenzbewertbar.
    from app.services.timer_health import expected_intervals_from_unit_dir

    assert set(expected_intervals_from_unit_dir(unit_dir)) == set(on_active)


def test_the_coverage_line_never_claims_full_coverage() -> None:
    """23 von 56 muss als 23 von 56 erscheinen — nie als "alles gesund"."""
    from app.services.timer_health import format_cadence_coverage

    line = format_cadence_coverage({"total": 56, "evaluated": 23, "unevaluated": 33, "overdue": 0})
    assert line is not None
    assert "23/56" in line
    assert "0 ueberfaellig" in line
    assert "33 nicht kadenzbewertet" in line
    # Der gefaehrliche Satz darf nicht entstehen.
    assert "56/56" not in line

    # Ohne Messung gibt es keine Zeile — kein "0/0 bewertet".
    assert format_cadence_coverage({}) is None
    assert format_cadence_coverage(None) is None


def test_the_alert_text_carries_the_coverage_without_a_finding() -> None:
    """Der Operator liest Telegram, nicht die Konsole.

    Stuende die Abdeckung nur im Befundfall im Text, waere ein stiller
    Health-Alert ununterscheidbar von "alle Timer geprueft und in Ordnung".
    """
    from app.alerts.health_check import HealthReport
    from app.alerts.health_notify import build_health_alert_text

    report = HealthReport(
        timer_cadence={"total": 56, "evaluated": 23, "unevaluated": 33, "overdue": 0}
    )
    text = build_health_alert_text(report, lookback_hours=24)
    assert "23/56 bewertet" in text
    assert "33 nicht kadenzbewertet" in text
    assert not report.issues, "der Fall ist ausdruecklich der ohne Befund"


def test_coverage_is_reported_even_when_nothing_is_overdue() -> None:
    """ "0 ueberfaellig" ohne Nenner liest sich wie "alle Timer geprueft".

    Genau diese stille Vollstaendigkeits-Behauptung ist der Fehler, den die
    Invariante verhindern soll — sie darf ihn nicht selbst begehen.
    """
    from app.alerts.health_check import HealthReport

    report = HealthReport()
    assert hasattr(report, "timer_cadence")

    # Der Vertrag: alle vier Schluessel, und evaluated + unevaluated == total.
    coverage = {"total": 56, "evaluated": 23, "unevaluated": 33, "overdue": 0}
    assert coverage["evaluated"] + coverage["unevaluated"] == coverage["total"]


def test_a_cadence_warning_does_not_turn_health_red() -> None:
    """Eine unkalibrierte Heuristik darf keine False-Red-Kaskade ausloesen.

    Der einzige harte Ausstieg in ``alerts health-check`` haengt am
    Stale-Pfad (``--exit-on-stale``), nicht an der Zahl oder Schwere der
    Befunde. Dieser Test haelt das fest, damit niemand spaeter beilaeufig
    einen Exit an ``severity`` knuepft und damit einen ungeeichten
    Timer-Verdacht zum Deploy-Blocker macht.
    """
    main_py = (Path(__file__).resolve().parents[2] / "app" / "cli" / "main.py").read_text(
        encoding="utf-8"
    )
    start = main_py.index("def alerts_health_check(")
    end = main_py.index("def alerts_ops_status(")
    body = main_py[start:end]

    exits = [
        ln.strip()
        for ln in body.splitlines()
        if "typer.Exit(" in ln and not ln.strip().startswith("#")
    ]
    assert exits == ["raise typer.Exit(code=2)"], exits
    # ... und dieser eine haengt am Stale-Pfad, nicht an der Severity.
    assert "_stale_exit_now" in body
    # Kein Ausstieg darf an Schwere oder Anzahl der Befunde haengen.
    for line_no, ln in enumerate(body.splitlines()):
        if "typer.Exit(" not in ln:
            continue
        window = " ".join(body.splitlines()[max(0, line_no - 4) : line_no])
        assert "severity" not in window, window
        assert "critical" not in window, window


def test_health_endpoint_does_not_map_issues_to_503() -> None:
    """Der HTTP-Health-Endpunkt liest den Timer-Audit, nicht diesen Check.

    Wuerde er Befunde in einen 5xx uebersetzen, koennte ein ``warning`` aus
    einer hergeleiteten Schwelle den Service als tot erscheinen lassen.
    """
    router = (
        Path(__file__).resolve().parents[2] / "app" / "api" / "routers" / "health.py"
    ).read_text(encoding="utf-8")
    assert "run_health_check" not in router
    assert "503" not in router


def test_the_counts_exist_below_the_presentation_not_inside_it() -> None:
    """Telegram ist die Darstellung, nicht die Existenz der Information.

    Entstuenden die Abdeckungszahlen erst beim String-Bau, gaebe es sie nur
    dort: kein JSON-Konsument, kein Test und keine spaetere Auswertung kaeme
    an sie heran, und eine zweite Oberflaeche muesste sie neu herleiten —
    womit sie driften koennten.

    Der Vertrag ist deshalb: ``_check_timer_scheduleability`` RECHNET, der
    Report TRAEGT, der Notifier FORMATIERT nur.
    """
    from dataclasses import fields

    from app.alerts.health_check import HealthReport

    # 1. Der Report traegt die Zahlen als eigenes, maschinenlesbares Feld.
    names = {f.name for f in fields(HealthReport)}
    assert "timer_cadence" in names

    # 2. Der Notifier leitet nichts her — er liest nur.
    notify = (
        Path(__file__).resolve().parents[2] / "app" / "alerts" / "health_notify.py"
    ).read_text(encoding="utf-8")
    for derivation in (
        "expected_intervals_from_unit_dir",
        "find_stalled_recurring_timers",
        "parse_systemd_timespan",
        "systemctl",
    ):
        assert derivation not in notify, f"health_notify leitet {derivation} selbst ab"

    # 3. Die Formatierung ist rein: gleiche Eingabe, gleiche Ausgabe, keine Quelle.
    from app.services.timer_health import format_cadence_coverage

    payload = {"total": 56, "evaluated": 23, "unevaluated": 33, "overdue": 0}
    assert format_cadence_coverage(payload) == format_cadence_coverage(dict(payload))


def test_oncalendar_timers_are_never_counted_as_cadence_healthy() -> None:
    """Nicht bewertet ist nicht dasselbe wie in Ordnung.

    Die 33 OnCalendar-Units duerfen weder in ``evaluated`` noch stillschweigend
    in einer Gesundmeldung landen — sie stehen ausdruecklich in ``unevaluated``.
    """
    from app.services.timer_health import (
        expected_intervals_from_unit_dir,
        format_cadence_coverage,
    )

    unit_dir = Path(__file__).resolve().parents[2] / "deploy" / "systemd"
    evaluable = set(expected_intervals_from_unit_dir(unit_dir))
    all_units = {f.name for f in unit_dir.glob("kai-*.timer")}
    unevaluable = all_units - evaluable
    assert unevaluable, "Regression: alles waere bewertbar, das stimmt nicht"

    line = format_cadence_coverage(
        {
            "total": len(all_units),
            "evaluated": len(evaluable),
            "unevaluated": len(unevaluable),
            "overdue": 0,
        }
    )
    assert line is not None
    assert f"{len(evaluable)}/{len(all_units)}" in line
    assert f"{len(unevaluable)} nicht kadenzbewertet" in line
    assert f"{len(all_units)}/{len(all_units)}" not in line
