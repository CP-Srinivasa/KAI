r"""Der Health-Alarm wiederholte denselben unveraenderten Befund im 30-Minuten-Takt.

Gemessen am 2026-08-18 auf dem Pi: **30 gesendete** Health-Alarme an einem Tag,
47 weitere nur durch den Cooldown unterdrueckt -- und jeder einzelne trug
denselben, dem Operator seit 16 Tagen bekannten Befund:

    [WARNING] tradingview_ingress_freshness: ... mtime is 22521min old

Das Gate war rein zeitbasiert (``.health_check_last_notification`` = ein
Zeitstempel). Nach Ablauf des Cooldowns ging derselbe Text wieder raus, endlos.
Das ist dieselbe Krankheit wie der 5-Minuten-Watchdog-Spam und der rote
Phantom-Ausfall mit Erfolgswerten: ein Kanal, der oft Bekanntes wiederholt,
wird ignoriert -- und dann faellt das Neue mit durch.

Der Fix aendert NICHT, WAS erkannt wird, sondern nur, WANN gesprochen wird:

    Befundmenge geaendert   -> sofort melden (das ist Neuigkeit)
    Befundmenge unveraendert-> erst nach ``reassert_minutes`` wieder (Default 24h)
    Befundmenge leer geworden-> einmal Entwarnung (gab es vorher NICHT)

Entscheidendes Detail: der Fingerprint laeuft ueber ``severity:component``,
NICHT ueber den Meldetext. Der Text enthaelt das Alter in Minuten
("mtime is 22521min old") und aendert sich bei JEDEM Lauf -- ein Fingerprint
darueber wuerde nie greifen und die Wiederholung nicht bremsen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.alerts.health_notify import (
    dispatch_health_notification,
    issues_fingerprint,
)


@dataclass
class _Issue:
    severity: str
    component: str
    message: str


@dataclass
class _Report:
    issues: list[_Issue] = field(default_factory=list)
    recent_alerts: int = 5
    recent_actionable_alerts: int = 1
    recent_cycles: int = 1200
    data_sources_stale: bool = False


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        self.lines.append(" ".join(str(a) for a in args))


def _tv_stale(age_min: int) -> _Issue:
    """Der Realbefund -- der Text traegt das Alter, also bei jedem Lauf anders."""
    return _Issue(
        "warning",
        "tradingview_ingress_freshness",
        f"tradingview_webhook_audit.jsonl mtime is {age_min}min old (threshold: 720min)",
    )


def test_fingerprint_ignores_the_changing_age_in_the_message() -> None:
    """Kern des Fixes: derselbe Befund mit anderem Alter ist derselbe Befund.

    Ohne diese Eigenschaft waere jede Wiederholung ein "neuer" Befund und der
    Alarm wuerde weiter im Takt feuern.
    """
    assert issues_fingerprint([_tv_stale(22521)]) == issues_fingerprint([_tv_stale(29999)])


def test_fingerprint_distinguishes_component_and_severity() -> None:
    base = [_tv_stale(1000)]
    plus = [_tv_stale(1000), _Issue("warning", "alerts_freshness", "x")]
    escalated = [_Issue("critical", "tradingview_ingress_freshness", "y")]
    assert issues_fingerprint(base) != issues_fingerprint(plus)
    assert issues_fingerprint(base) != issues_fingerprint(escalated)


def test_fingerprint_is_order_independent() -> None:
    a = _Issue("warning", "alerts_freshness", "a")
    b = _Issue("warning", "tradingview_ingress_freshness", "b")
    assert issues_fingerprint([a, b]) == issues_fingerprint([b, a])


def _dispatch(report: _Report, state: Path, sent: list[str], **kw) -> bool:
    console = _Console()
    ok = dispatch_health_notification(
        report,
        lookback_hours=24,
        notify_cooldown_minutes=kw.pop("cooldown", 30),
        console=console,
        state_file=state,
        sender=lambda text: (sent.append(text), True)[1],
        **kw,
    )
    return ok


def test_first_finding_is_sent(tmp_path: Path) -> None:
    sent: list[str] = []
    assert _dispatch(_Report(issues=[_tv_stale(1000)]), tmp_path / "s.json", sent, now_ts=0.0)
    assert len(sent) == 1


def test_unchanged_finding_is_not_repeated_before_reassert(tmp_path: Path) -> None:
    """Der 30-Alarme-Tag: unveraendert heisst schweigen.

    Vier Laeufe ueber 3 Stunden mit demselben Befund und stetig steigendem
    Alter -- genau die Realsequenz vom 18.08.
    """
    state = tmp_path / "s.json"
    sent: list[str] = []
    _dispatch(_Report(issues=[_tv_stale(1000)]), state, sent, now_ts=0.0)
    for minutes, age in ((45, 1045), (90, 1090), (180, 1180)):
        _dispatch(_Report(issues=[_tv_stale(age)]), state, sent, now_ts=minutes * 60.0)
    assert len(sent) == 1, f"erwartet 1 Meldung, gesendet {len(sent)}"


def test_unchanged_finding_is_reasserted_after_a_day(tmp_path: Path) -> None:
    """Ein Daueralarm darf nicht still verschwinden -- taeglich einmal erinnern."""
    state = tmp_path / "s.json"
    sent: list[str] = []
    _dispatch(_Report(issues=[_tv_stale(1000)]), state, sent, now_ts=0.0)
    _dispatch(_Report(issues=[_tv_stale(2440)]), state, sent, now_ts=1441 * 60.0)
    assert len(sent) == 2


def test_new_issue_breaks_through_immediately(tmp_path: Path) -> None:
    """Eine ANDERE Befundmenge ist Neuigkeit und wartet auf keinen Cooldown.

    Das ist die Gegenprobe: der Fix darf den Waechter nicht langsamer machen,
    nur leiser bei Bekanntem.
    """
    state = tmp_path / "s.json"
    sent: list[str] = []
    _dispatch(_Report(issues=[_tv_stale(1000)]), state, sent, now_ts=0.0)
    _dispatch(
        _Report(issues=[_tv_stale(1001), _Issue("critical", "prereg_ledger_presence", "weg")]),
        state,
        sent,
        now_ts=60.0,  # eine Minute spaeter, weit im Cooldown
    )
    assert len(sent) == 2
    assert "prereg_ledger_presence" in sent[-1]


def test_escalation_to_critical_breaks_through(tmp_path: Path) -> None:
    state = tmp_path / "s.json"
    sent: list[str] = []
    _dispatch(_Report(issues=[_tv_stale(1000)]), state, sent, now_ts=0.0)
    _dispatch(
        _Report(issues=[_Issue("critical", "tradingview_ingress_freshness", "jetzt kritisch")]),
        state,
        sent,
        now_ts=120.0,
    )
    assert len(sent) == 2


def test_recovery_is_announced_once(tmp_path: Path) -> None:
    """Entwarnung gab es vorher NICHT -- der Operator erfuhr nie, dass es klar ist."""
    state = tmp_path / "s.json"
    sent: list[str] = []
    _dispatch(_Report(issues=[_tv_stale(1000)]), state, sent, now_ts=0.0)
    _dispatch(_Report(issues=[]), state, sent, now_ts=600.0)
    assert len(sent) == 2
    assert "behoben" in sent[-1].lower() or "aufgeloest" in sent[-1].lower()
    # Und danach Ruhe: ein gesundes System schweigt.
    _dispatch(_Report(issues=[]), state, sent, now_ts=1200.0)
    assert len(sent) == 2


def test_clean_report_never_notifies_from_scratch(tmp_path: Path) -> None:
    sent: list[str] = []
    assert not _dispatch(_Report(issues=[]), tmp_path / "s.json", sent, now_ts=0.0)
    assert sent == []


def test_unreadable_state_never_silences(tmp_path: Path) -> None:
    """fail-open bleibt: kaputter Zustand darf keinen Befund verschlucken."""
    state = tmp_path / "s.json"
    state.write_text("{kaputt", encoding="utf-8")
    sent: list[str] = []
    assert _dispatch(_Report(issues=[_tv_stale(1000)]), state, sent, now_ts=0.0)
    assert len(sent) == 1


def test_legacy_timestamp_state_is_accepted(tmp_path: Path) -> None:
    """Altzustand war ein nackter Zeitstempel -- der Umstieg darf nicht knallen."""
    state = tmp_path / "s.json"
    state.write_text("12345.0", encoding="utf-8")
    sent: list[str] = []
    assert _dispatch(_Report(issues=[_tv_stale(1000)]), state, sent, now_ts=99999.0)
    assert len(sent) == 1


# ---------------------------------------------------------------------------
# G6 Task 1: die Klasse entscheidet, wie lange Bekanntes schweigen darf
# ---------------------------------------------------------------------------


def _p0() -> _Issue:
    return _Issue("critical", "privilege_broker", "Broker fehlt — kein passwortfreier Pfad")


def _p2() -> _Issue:
    return _Issue("warning", "annotations", "12 unannotierte Alarme")


def test_p0_reasserts_hourly_not_daily(tmp_path: Path) -> None:
    """Ein Kapital-/Truth-Befund darf nicht 24 h aus dem Kanal verschwinden."""
    state = tmp_path / "s.json"
    sent: list[str] = []
    _dispatch(_Report(issues=[_p0()]), state, sent, now_ts=0.0)
    _dispatch(_Report(issues=[_p0()]), state, sent, now_ts=59 * 60.0)
    assert len(sent) == 1, "vor 60 min darf nichts wiederholt werden"
    _dispatch(_Report(issues=[_p0()]), state, sent, now_ts=61 * 60.0)
    assert len(sent) == 2


def test_p1_reasserts_after_six_hours(tmp_path: Path) -> None:
    """Stilles Versagen: 4x taeglich statt 1x — das ist A4-024/026."""
    state = tmp_path / "s.json"
    sent: list[str] = []
    _dispatch(_Report(issues=[_tv_stale(1000)]), state, sent, now_ts=0.0)
    _dispatch(_Report(issues=[_tv_stale(1355)]), state, sent, now_ts=355 * 60.0)
    assert len(sent) == 1
    _dispatch(_Report(issues=[_tv_stale(1365)]), state, sent, now_ts=365 * 60.0)
    assert len(sent) == 2


def test_digest_only_report_keeps_the_daily_window(tmp_path: Path) -> None:
    """Negativkontrolle: P2 bleibt beim 24-h-Fenster — der Fatigue-Schutz haelt.

    Ohne diesen Test waere die Aenderung nur eine Absenkung ALLER Schwellen und
    haette den 30-Alarme-Tag vom 18.08. zurueckgeholt.
    """
    state = tmp_path / "s.json"
    sent: list[str] = []
    _dispatch(_Report(issues=[_p2()]), state, sent, now_ts=0.0)
    for hours in (1, 6, 12, 23):
        _dispatch(_Report(issues=[_p2()]), state, sent, now_ts=hours * 3600.0)
    assert len(sent) == 1
    _dispatch(_Report(issues=[_p2()]), state, sent, now_ts=1441 * 60.0)
    assert len(sent) == 2


def test_the_most_urgent_class_sets_the_window(tmp_path: Path) -> None:
    """Eine P0 neben zehn P2 macht die Meldung stuendlich, nicht taeglich."""
    state = tmp_path / "s.json"
    sent: list[str] = []
    issues = [_p2(), _p0(), _p2()]
    _dispatch(_Report(issues=issues), state, sent, now_ts=0.0)
    _dispatch(_Report(issues=issues), state, sent, now_ts=61 * 60.0)
    assert len(sent) == 2


def test_alert_text_is_grouped_by_class(tmp_path: Path) -> None:
    """35 Komponenten reisten bisher ohne Rangordnung in EINER Nachricht."""
    from app.alerts.health_notify import build_health_alert_text

    text = build_health_alert_text(_Report(issues=[_p2(), _p0()]), lookback_hours=24)
    assert "== P0 (1) ==" in text
    assert "== P2 (1) ==" in text
    assert text.index("== P0 (1) ==") < text.index("== P2 (1) ==")
    assert "privilege_broker" in text and "annotations" in text


# ---------------------------------------------------------------------------
# P0 (2026-09-01): ein Testlauf darf die versiegelte Population nicht fuellen
# ---------------------------------------------------------------------------


def test_emission_stream_follows_the_injected_state_path(tmp_path: Path) -> None:
    """Der Emissions-Satz landet dort, wohin der Zustand gelenkt wurde.

    Vorher stand im Schreiber ein hartkodiertes ``Path("artifacts")`` — relativ
    zum ``cwd``. Jeder Testlauf in der Repo-Wurzel schrieb damit echte
    ``alert_emitted``-Saetze in den PRODUKTIONS-Strom; auf dem Pi erzeugte der
    Post-Deploy-Testlauf so fuenf Emissionen in neun Millisekunden.
    """
    state = tmp_path / "state.json"
    sent: list[str] = []
    _dispatch(_Report(issues=[_tv_stale(1000)]), state, sent, now_ts=0.0)

    stream = tmp_path / "operator_commands.jsonl"
    assert stream.exists(), "Emission muss neben dem injizierten Zustand liegen"
    records = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()]
    assert [r["record_type"] for r in records] == ["alert_emitted"]


def test_no_write_outside_the_injected_path(tmp_path: Path, monkeypatch) -> None:
    """Negativkontrolle: auch mit cwd=Repo-Wurzel darf nichts nach ./artifacts gehen.

    Das ist die eigentliche Zusage. Ohne sie ist eine versiegelte Messung durch
    einen beliebigen Testlauf faelschbar.
    """
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(repo_root)
    production = repo_root / "artifacts" / "operator_commands.jsonl"
    before = production.read_bytes() if production.exists() else None

    state = tmp_path / "state.json"
    sent: list[str] = []
    _dispatch(_Report(issues=[_tv_stale(1000)]), state, sent, now_ts=0.0)

    after = production.read_bytes() if production.exists() else None
    assert after == before, "Der Produktionsstrom wurde durch einen Testlauf veraendert"
    assert (tmp_path / "operator_commands.jsonl").exists()
