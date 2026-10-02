from datetime import UTC, datetime, timedelta

import pytest

from app.observability.ai_control.states import Signals, State, classify

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def s(**kw: object) -> Signals:
    return Signals(now=NOW, configured=True, **kw)  # type: ignore[arg-type]


def test_reihenfolge_konfiguration_vor_allem() -> None:
    v = classify(Signals(now=NOW, configured=False, disabled_reason="Route aus", proxy_down=True))
    assert v.state is State.DEAKTIVIERT and v.reason == "Route aus"


@pytest.mark.parametrize(
    ("kw", "erwartet"),
    [
        ({"proxy_down": True}, "Proxy nicht erreichbar"),
        ({"circuit_open": True}, "Circuit offen nach Fehlern"),
        ({"consecutive_failures": 5}, "5 Fehlversuche in Folge"),
        ({"calls_1h": 10, "failures_1h": 3}, "Fehlerquote 30 % in 1 h"),
        ({"balance_exhausted": True}, "Guthaben leer"),
    ],
)
def test_gestoert(kw: dict, erwartet: str) -> None:
    v = classify(s(**kw))
    assert v.state is State.GESTOERT and erwartet in v.reason


def test_wenige_aufrufe_sind_keine_quote() -> None:
    assert classify(s(calls_1h=9, failures_1h=9, last_ok=NOW)).state is State.AKTIV


def test_pausiert_mit_wiederanlauf() -> None:
    bis = NOW + timedelta(hours=12)
    v = classify(s(paused_reason="Tagesbudget leer", paused_until=bis))
    assert v.state is State.PAUSIERT and "00:00 UTC" in v.reason and v.since == bis


def test_aktiv_und_bereit() -> None:
    assert classify(s(last_ok=NOW - timedelta(minutes=14))).state is State.AKTIV
    v = classify(s(last_ok=NOW - timedelta(minutes=16)))
    assert v.state is State.BEREIT and "letzter Aufruf" in v.reason
    assert classify(s()).reason == "noch kein Aufruf"


def test_ausser_kraft_nur_mit_eingriff() -> None:
    v = classify(s(override_reason="standard -> off bis 03.10."))
    assert v.state is State.AUSSER_KRAFT
