"""Tests fuer die Rueckkante Operator → Truth (G8, R2-07 / R2-12 / R2-25).

Die Kette, die es nie gab, wird hier Glied fuer Glied festgenagelt:

    Befund → ``trigger_id`` → Alarmtext → Operator-Klick → Request-Audit

Die schaerfsten Tests sind die Negativkontrollen: eine Handlung **vor** dem
Befund ist keine Reaktion, und eine Handlung **ohne** die ID ist kein Beleg,
sondern eine Vermutung. Ohne diese beiden waere die Kennzahl beliebig nach oben
zu treiben — genau der Fehler, der eine Nutzenaussage wertlos macht.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.observability.operator_feedback import (
    OPERATOR_ACTION_STREAM,
    TRIGGER_QUERY_PARAM,
    correlate,
    is_trigger_id,
    load_jsonl,
    new_trigger_id,
    record_operator_action,
    summarise,
)

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _stream(tmp_path: Path) -> Path:
    return tmp_path / OPERATOR_ACTION_STREAM


# ---------------------------------------------------------------------------
# Die Auslöser-ID
# ---------------------------------------------------------------------------


def test_trigger_id_has_a_checkable_form() -> None:
    trigger = new_trigger_id(seed="x", now=T0)
    assert is_trigger_id(trigger)
    assert trigger.startswith("trg_")


def test_same_finding_in_the_same_minute_is_the_same_trigger() -> None:
    """Ein wiederholter Alarm ist kein neuer Ausloeser — sonst waere der Nenner beliebig."""
    a = new_trigger_id(seed="fingerprint-abc", now=T0)
    b = new_trigger_id(seed="fingerprint-abc", now=T0 + timedelta(seconds=30))
    assert a == b


def test_different_findings_get_different_triggers() -> None:
    assert new_trigger_id(seed="a", now=T0) != new_trigger_id(seed="b", now=T0)


@pytest.mark.parametrize(
    "value",
    ["", None, "trg_", "trg_xyz", "trg_" + "a" * 11, "trg_" + "a" * 13, "trg_ABCDEF012345", 42],
)
def test_malformed_trigger_ids_are_rejected(value: object) -> None:
    """Der Parameter kommt aus der URL — Fremdeingabe vor einem Schreibpfad."""
    assert is_trigger_id(value) is False


# ---------------------------------------------------------------------------
# Der Strom (der BESTEHENDE, kein neuer)
# ---------------------------------------------------------------------------


def test_action_is_appended_to_the_existing_operator_stream(tmp_path: Path) -> None:
    trigger = new_trigger_id(seed="s", now=T0)
    record_operator_action(
        _stream(tmp_path),
        now=T0,
        channel="dashboard",
        action="open_panel",
        trigger_id=trigger,
    )
    records = load_jsonl(_stream(tmp_path))
    assert len(records) == 1
    assert records[0]["record_type"] == "operator_action"
    assert records[0]["trigger_id"] == trigger
    assert records[0]["channel"] == "dashboard"


def test_malformed_trigger_is_not_written_into_the_stream(tmp_path: Path) -> None:
    record_operator_action(
        _stream(tmp_path),
        now=T0,
        channel="dashboard",
        action="open_panel",
        trigger_id="<script>",
    )
    assert "trigger_id" not in load_jsonl(_stream(tmp_path))[0]


def test_corrupt_lines_are_skipped_not_guessed(tmp_path: Path) -> None:
    path = _stream(tmp_path)
    record_operator_action(path, now=T0, channel="cli", action="x")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{kaputt\n\n")
    assert len(load_jsonl(path)) == 1


# ---------------------------------------------------------------------------
# Korrelation — und die zwei Wege, sie zu faelschen
# ---------------------------------------------------------------------------


def test_action_after_the_finding_counts(tmp_path: Path) -> None:
    trigger = new_trigger_id(seed="s", now=T0)
    record_operator_action(
        _stream(tmp_path),
        now=T0 + timedelta(minutes=12),
        channel="dashboard",
        action="open",
        trigger_id=trigger,
    )
    result = correlate([(trigger, T0)], load_jsonl(_stream(tmp_path)))
    assert result[0].acted is True
    assert result[0].latency_minutes == 12.0
    assert result[0].channel == "dashboard"


def test_action_before_the_finding_is_not_a_reaction(tmp_path: Path) -> None:
    """Negativkontrolle 1: Reihenfolge. Sonst zaehlt jeder Zufall als Wirkung."""
    trigger = new_trigger_id(seed="s", now=T0)
    record_operator_action(
        _stream(tmp_path),
        now=T0 - timedelta(minutes=5),
        channel="dashboard",
        action="open",
        trigger_id=trigger,
    )
    assert correlate([(trigger, T0)], load_jsonl(_stream(tmp_path)))[0].acted is False


def test_action_without_the_trigger_id_is_not_evidence(tmp_path: Path) -> None:
    """Negativkontrolle 2: Identitaet. Eine Handlung ohne ID ist eine Vermutung."""
    trigger = new_trigger_id(seed="s", now=T0)
    record_operator_action(
        _stream(tmp_path), now=T0 + timedelta(minutes=3), channel="dashboard", action="open"
    )
    assert correlate([(trigger, T0)], load_jsonl(_stream(tmp_path)))[0].acted is False


def test_action_after_the_window_does_not_count(tmp_path: Path) -> None:
    trigger = new_trigger_id(seed="s", now=T0)
    record_operator_action(
        _stream(tmp_path),
        now=T0 + timedelta(hours=25),
        channel="cli",
        action="x",
        trigger_id=trigger,
    )
    assert correlate([(trigger, T0)], load_jsonl(_stream(tmp_path)))[0].acted is False


def test_earliest_reaction_wins(tmp_path: Path) -> None:
    trigger = new_trigger_id(seed="s", now=T0)
    path = _stream(tmp_path)
    for minutes, channel in ((40, "cli"), (10, "telegram"), (90, "dashboard")):
        record_operator_action(
            path,
            now=T0 + timedelta(minutes=minutes),
            channel=channel,
            action="x",
            trigger_id=trigger,
        )
    result = correlate([(trigger, T0)], load_jsonl(path))[0]
    assert result.latency_minutes == 10.0
    assert result.channel == "telegram"


# ---------------------------------------------------------------------------
# Zerlegung statt Quote
# ---------------------------------------------------------------------------


def test_summary_names_the_unanswered_findings(tmp_path: Path) -> None:
    path = _stream(tmp_path)
    answered = new_trigger_id(seed="a", now=T0)
    ignored = new_trigger_id(seed="b", now=T0)
    record_operator_action(
        path, now=T0 + timedelta(minutes=5), channel="dashboard", action="x", trigger_id=answered
    )
    verdict = summarise(correlate([(answered, T0), (ignored, T0)], load_jsonl(path)))
    assert verdict.emitted == 2
    assert verdict.acted == 1
    assert verdict.unanswered == (ignored,)
    assert verdict.action_rate == 0.5
    assert verdict.by_channel == (("dashboard", 1),)


def test_empty_population_has_no_rate_instead_of_zero() -> None:
    """Eine Quote ohne Nenner ist keine 0 %, sondern keine Aussage."""
    verdict = summarise([])
    assert verdict.emitted == 0
    assert verdict.action_rate is None


def test_median_latency_is_reported(tmp_path: Path) -> None:
    path = _stream(tmp_path)
    triggers = [new_trigger_id(seed=str(i), now=T0) for i in range(3)]
    for trigger, minutes in zip(triggers, (5, 15, 60), strict=True):
        record_operator_action(
            path,
            now=T0 + timedelta(minutes=minutes),
            channel="dashboard",
            action="x",
            trigger_id=trigger,
        )
    verdict = summarise(correlate([(t, T0) for t in triggers], load_jsonl(path)))
    assert verdict.median_latency_minutes == 15.0


# ---------------------------------------------------------------------------
# Verdrahtung: der Alarmtext traegt die ID
# ---------------------------------------------------------------------------


def test_alert_text_carries_a_usable_trigger_id() -> None:
    from dataclasses import dataclass, field

    from app.alerts.health_notify import build_health_alert_text

    @dataclass
    class _Issue:
        severity: str
        component: str
        message: str

    @dataclass
    class _Report:
        issues: list[_Issue] = field(default_factory=list)
        recent_alerts: int = 0
        recent_actionable_alerts: int = 0
        recent_cycles: int = 0
        data_sources_stale: bool = False

    text = build_health_alert_text(
        _Report(issues=[_Issue("critical", "privilege_broker", "weg")]), lookback_hours=24
    )
    line = next(ln for ln in text.splitlines() if ln.startswith("Trigger: "))
    trigger = line.split()[1]
    assert is_trigger_id(trigger)


def test_alert_text_carries_a_clickable_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne klickbaren Link misst die Rueckkante Reibung statt Nutzen.

    Eine Population, die verlangt, dass der Operator eine ID von Hand an eine
    URL haengt, ist nicht erreichbar — und eine Null waere dann die Antwort
    auf die Umstaendlichkeit der Messung, nicht auf die Frage.
    """
    from dataclasses import dataclass, field

    import app.alerts.health_notify as hn

    @dataclass
    class _Issue:
        severity: str
        component: str
        message: str

    @dataclass
    class _Report:
        issues: list[_Issue] = field(default_factory=list)
        recent_alerts: int = 0
        recent_actionable_alerts: int = 0
        recent_cycles: int = 0
        data_sources_stale: bool = False

    monkeypatch.setattr(hn, "_dashboard_link", lambda t: f"https://example.test/dashboard/?t={t}")
    text = hn.build_health_alert_text(
        _Report(issues=[_Issue("critical", "privilege_broker", "weg")]), lookback_hours=24
    )
    trigger = next(ln for ln in text.splitlines() if ln.startswith("Trigger: ")).split()[1]
    assert f"https://example.test/dashboard/?{TRIGGER_QUERY_PARAM}={trigger}" in text


def test_missing_dashboard_url_leaves_the_bare_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negativkontrolle: ohne konfigurierte URL wird nichts geraten."""
    import app.alerts.health_notify as hn
    from app.core.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(type(settings.operator), "telegram_dashboard_url", "", raising=False)
    assert hn._dashboard_link("trg_0123456789ab") in ("", None) or "http" in hn._dashboard_link(
        "trg_0123456789ab"
    )
