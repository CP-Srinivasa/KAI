from datetime import UTC, datetime, timedelta
from typing import Any

from app.observability.ai_control.alerts import is_quiet, plan
from app.observability.ai_control.config import ControlThresholds

TH = ControlThresholds(_env_file=None)  # type: ignore[call-arg]
TAG = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)  # 12:00 MESZ
NACHT = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)  # 02:00 MESZ


def h(key: str, severity: str = "warn", min_age_min: int = 0) -> dict[str, Any]:
    return {
        "key": key,
        "severity": severity,
        "title": f"T {key}",
        "detail": "d",
        "min_age_min": min_age_min,
    }


def test_neu_einmal_dann_ruhe_dann_erinnerung_dann_behoben() -> None:
    text, st = plan([h("a")], {}, now=TAG, thresholds=TH)
    assert text and "T a" in text
    assert plan([h("a")], st, now=TAG + timedelta(hours=1), thresholds=TH)[0] is None
    text2, st2 = plan([h("a")], st, now=TAG + timedelta(hours=25), thresholds=TH)
    assert text2 and "weiterhin" in text2
    text3, st3 = plan([], st2, now=TAG + timedelta(hours=26), thresholds=TH)
    assert text3 and "behoben" in text3 and "a" not in st3["open"]


def test_mindestalter() -> None:
    text, st = plan([h("p", min_age_min=5)], {}, now=TAG, thresholds=TH)
    assert text is None
    text2, _ = plan([h("p", min_age_min=5)], st, now=TAG + timedelta(minutes=6), thresholds=TH)
    assert text2 and "T p" in text2


def test_kurzer_aussetzer_unter_mindestalter_meldet_auch_kein_behoben() -> None:
    _, st = plan([h("p", min_age_min=5)], {}, now=TAG, thresholds=TH)
    text, st2 = plan([], st, now=TAG + timedelta(minutes=2), thresholds=TH)
    assert text is None and st2["open"] == {}


def test_ruhezeit_sammelt_und_liefert_morgens() -> None:
    assert is_quiet(NACHT, TH) and not is_quiet(TAG, TH)
    text, st = plan([h("n")], {}, now=NACHT, thresholds=TH)
    assert text is None
    text, st = plan([], st, now=NACHT + timedelta(hours=2), thresholds=TH)
    assert text is None
    morgens = datetime(2026, 10, 2, 5, 5, tzinfo=UTC)  # 07:05 MESZ
    text, st = plan([], st, now=morgens, thresholds=TH)
    assert text and "Ruhezeit" in text and "T n" in text and "behoben" in text
    assert st["pending"] == []


def test_kritisch_bricht_die_ruhezeit() -> None:
    text, _ = plan([h("k", severity="crit")], {}, now=NACHT, thresholds=TH)
    assert text and "T k" in text


def test_tageshinweis_verfaellt_ohne_behoben() -> None:
    """Review M11: ``budget_frueh:<datum>`` endet mit dem Tag -- behoben ist dabei nichts."""
    tages = {**h("budget_frueh:2026-10-02"), "expires": True}
    text, st = plan([tages], {}, now=TAG, thresholds=TH)
    assert text and "T budget_frueh" in text
    text2, st2 = plan([], st, now=TAG + timedelta(hours=14), thresholds=TH)
    assert text2 is None and st2["open"] == {} and st2["pending"] == []
