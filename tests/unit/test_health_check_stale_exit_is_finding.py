"""Ein Wächter darf nicht abbrechen, weil das Beobachtete fehlt.

Belegter Vorfall (Pi-Journal, 16.08. 11:00–12:00 und erneut 17.08. 12:45):

    toter TV-Ingest -> keine Alerts -> alert_audit.jsonl veraltet (815 min
    vs. Schwelle 480) -> "Exit-on-stale: aborting with code 2" ->
    kai-health-check.service: status=2/INVALIDARGUMENT

Fünfmal am 16.08., und ``systemctl --failed`` zeigte trotzdem 0 — es kennt nur
den AKTUELLEN Zustand. Der Wächter quittierte den Dienst genau in dem Zustand,
für dessen Meldung er existiert.

``--exit-on-stale`` wurde für einen ANDEREN Zweck gebaut: Workstation-Läufe
lesen gespiegelte, womöglich unvollständige Artefakte und sollen den
Telegram-Kanal nicht mit Fehlalarmen fluten (Vorfall 2026-05-23). Dieser Zweck
hängt vollständig an ``runs_on_pi`` — die Nicht-Autorität der Quelle. Auf dem
autoritativen Host ist Staleness dagegen ein BEFUND: dort schreibt der Erzeuger
lokal, und wenn die Datei altert, ist der Erzeuger tot.

Der Fix trennt beides: nicht-autoritativer Host -> Abbruch (Code 2, Schutz
bleibt); autoritativer Host -> melden und mit 0 enden.
"""

from __future__ import annotations

import typer

from app.alerts import health_check as hc_mod
from app.alerts import health_notify as hn_mod
from app.cli import main as cli_main


def _report(*, stale: bool, on_pi: bool) -> hc_mod.HealthReport:
    report = hc_mod.HealthReport()
    report.data_sources_stale = stale
    report.runs_on_pi = on_pi
    report.hostname = "kai-pi5" if on_pi else "workstation"
    if stale:
        report.issues.append(
            hc_mod.HealthIssue(
                severity="warning",
                component="alerts",
                message="alert_audit.jsonl stale (815 min)",
            )
        )
    return report


def _run(monkeypatch, *, stale: bool, on_pi: bool) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(
        hc_mod, "run_health_check_report", lambda **_kw: _report(stale=stale, on_pi=on_pi)
    )
    monkeypatch.setattr(
        hn_mod,
        "dispatch_health_notification",
        lambda report, **_kw: sent.append("dispatched"),
    )
    cli_main.alerts_health_check(
        lookback_hours=24,
        notify=False,
        telegram_on_issue=True,
        notify_cooldown_minutes=30,
        min_expected_actionable=0,
        exit_on_stale=True,
        allow_stale=False,
    )
    return sent


def test_stale_auf_dem_autoritativen_host_ist_ein_befund_kein_abbruch(monkeypatch) -> None:
    """Der Fall aus dem Journal: Pi + veraltete Daten -> melden, Exit 0."""
    sent = _run(monkeypatch, stale=True, on_pi=True)
    assert sent == ["dispatched"], "Der Befund muss die Maschine verlassen."


def test_off_pi_bricht_weiter_ab(monkeypatch) -> None:
    """Die Schutzsemantik gegen Workstation-Fehlalarme bleibt unverändert."""
    try:
        _run(monkeypatch, stale=False, on_pi=False)
    except typer.Exit as exc:
        assert exc.exit_code == 2
    else:  # pragma: no cover - Fehlerpfad
        raise AssertionError("Off-Pi-Lauf muss weiterhin mit Code 2 aussteigen.")


def test_off_pi_meldet_trotz_abbruch(monkeypatch) -> None:
    """#698-Reihenfolge bleibt: erst senden, dann aussteigen."""
    sent: list[str] = []
    monkeypatch.setattr(
        hc_mod, "run_health_check_report", lambda **_kw: _report(stale=True, on_pi=False)
    )
    monkeypatch.setattr(
        hn_mod, "dispatch_health_notification", lambda report, **_kw: sent.append("dispatched")
    )
    try:
        cli_main.alerts_health_check(
            lookback_hours=24,
            notify=False,
            telegram_on_issue=True,
            notify_cooldown_minutes=30,
            min_expected_actionable=0,
            exit_on_stale=True,
            allow_stale=False,
        )
    except typer.Exit:
        pass
    assert sent == ["dispatched"]
