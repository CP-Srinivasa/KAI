"""Die Alert-Entscheidung darf nicht an einer Rundungsgrenze haengen.

``priority = round(raw * 9) + 1`` legt die Schwelle fuer min_priority=7 auf
raw = 5.5/9 = 0.61111. Gemessen an 460 echten Entscheidungen (230 Dokumente,
zwei Modelle) liegen die vorkommenden raw-Werte in Schritten von 0,005 — das LLM
liefert seine Teil-Scores in 0,1-Schritten. Direkt an der Kante:

    0.61000   0.00111 UNTER der Kante  -> kein Alert
    0.61500   0.00389 UEBER der Kante  -> Alert

2,0 % aller Entscheidungen kippen bei einer Stoerung von +-0,005. Eine Groesse,
die in 0,1-Schritten geschaetzt wird, entscheidet ueber Abstaende von 0,005.
"""

from __future__ import annotations

import pytest

from app.alerts.threshold import ThresholdEngine
from app.analysis.scoring import (
    ALERT_GATE_RAW,
    compute_priority,
    is_alert_worthy,
    min_priority_as_raw_gate,
)
from app.core.domain.document import AnalysisResult, SentimentLabel


def _result(*, relevance: float, impact: float, novelty: float = 0.5) -> AnalysisResult:
    return AnalysisResult(
        document_id="doc-1",
        sentiment_label=SentimentLabel.NEUTRAL,
        sentiment_score=0.0,
        relevance_score=relevance,
        impact_score=impact,
        confidence_score=0.8,
        novelty_score=novelty,
        explanation_short="",
        explanation_long="",
        actionable=False,
        spam_probability=0.0,
    )


# --- die Umkehrung der Rundung ---------------------------------------------


@pytest.mark.parametrize(
    ("min_priority", "erwartet"),
    [(7, 5.5 / 9), (8, 6.5 / 9), (10, 8.5 / 9), (1, -0.5 / 9)],
)
def test_min_priority_as_raw_gate_kehrt_die_rundung_um(min_priority: int, erwartet: float) -> None:
    assert min_priority_as_raw_gate(min_priority) == pytest.approx(erwartet)


def test_die_umrechnung_stimmt_mit_compute_priority_ueberein() -> None:
    """Gegenprobe: an der berechneten Kante muss die Ganzzahl tatsaechlich kippen."""
    gate = min_priority_as_raw_gate(7)
    for relevance in [i / 1000 for i in range(400, 1001)]:
        res = _result(relevance=relevance, impact=relevance)
        ps = compute_priority(res)
        assert (ps.priority >= 7) == (ps.raw_score >= gate - 1e-12), (
            f"Umrechnung weicht ab bei raw={ps.raw_score}: priority={ps.priority}"
        )


# --- der kalibrierte Wert ---------------------------------------------------


def test_alert_gate_liegt_zwischen_den_beiden_realen_nachbarwerten() -> None:
    """0,615 trennt 0,61000 (heute kein Alert) von 0,61500 (heute Alert).

    Beide Werte kommen im Messsatz tatsaechlich vor. Ein Gate MUSS zwischen ihnen
    liegen, sonst aendert es das Verhalten.
    """
    assert 0.61000 < ALERT_GATE_RAW <= 0.61500
    # und es liegt weiter von der Rundungskante entfernt als diese von 0.61
    assert abs(ALERT_GATE_RAW - 0.61500) <= abs(min_priority_as_raw_gate(7) - 0.61000)


def test_gate_aendert_die_entscheidung_an_den_realen_nachbarn_nicht() -> None:
    """Dieselbe Dokumentmenge wie heute — das war die Bedingung der Kalibrierung."""
    for raw_ziel, erwartet_alert in ((0.61000, False), (0.61500, True)):
        # Ein Ergebnis konstruieren, dessen raw_score exakt den Zielwert trifft:
        # raw = 0.30*rel + 0.30*imp + 0.20*nov + 0.15*0 + 0.05*1
        # mit rel = imp = nov = x  ->  raw = 0.8x + 0.05
        x = (raw_ziel - 0.05) / 0.8
        res = _result(relevance=x, impact=x, novelty=x)
        ps = compute_priority(res)
        assert ps.raw_score == pytest.approx(raw_ziel, abs=1e-9)
        assert is_alert_worthy(res, gate_raw=ALERT_GATE_RAW) is erwartet_alert
        # und das alte, gerundete Verhalten stimmt an diesen Punkten ueberein
        assert (ps.priority >= 7) is erwartet_alert


# --- Boundary direkt um das Gate -------------------------------------------


# ``compute_priority`` gibt ``raw_score=round(raw, 4)`` zurueck. Die
# kontinuierliche Groesse hat damit selbst eine Aufloesung von 1e-4 — feiner
# kann kein Gate unterscheiden, egal wie es gewaehlt ist. Das ist unkritisch
# (die realen Werte liegen in Schritten von 0,005, also 50-mal groeber), gehoert
# aber festgehalten: wer hier eine Grenze auf 1e-6 erwartet, irrt.
RAW_AUFLOESUNG = 1e-4


@pytest.mark.parametrize("abstand", [0.0002, 0.001, 0.004])
def test_knapp_unter_dem_gate_kein_alert(abstand: float) -> None:
    assert abstand > RAW_AUFLOESUNG, "unterhalb der Aufloesung von raw_score"
    x = (ALERT_GATE_RAW - abstand - 0.05) / 0.8
    res = _result(relevance=x, impact=x, novelty=x)
    assert is_alert_worthy(res, gate_raw=ALERT_GATE_RAW) is False


@pytest.mark.parametrize("abstand", [0.0, 0.0002, 0.001, 0.004])
def test_am_gate_und_darueber_alert(abstand: float) -> None:
    x = (ALERT_GATE_RAW + abstand - 0.05) / 0.8
    res = _result(relevance=x, impact=x, novelty=x)
    assert is_alert_worthy(res, gate_raw=ALERT_GATE_RAW) is True


def test_raw_score_hat_eine_aufloesung_von_1e4() -> None:
    """Festgehalten, damit niemand eine feinere Trennschaerfe annimmt.

    Unkritisch, weil die real vorkommenden raw-Werte in Schritten von 0,005
    liegen — 50-mal grober als diese Aufloesung.
    """
    x = (ALERT_GATE_RAW - 0.05) / 0.8
    fein = compute_priority(_result(relevance=x, impact=x, novelty=x)).raw_score
    winzig = (ALERT_GATE_RAW - 1e-9 - 0.05) / 0.8
    assert (
        compute_priority(_result(relevance=winzig, impact=winzig, novelty=winzig)).raw_score == fein
    )


# --- Rueckwaertskompatibilitaet und Spam -----------------------------------


def test_ohne_gate_raw_bleibt_das_alte_verhalten() -> None:
    """Aufrufer, die weiter ganzzahlig denken, duerfen nichts merken."""
    res = _result(relevance=0.75, impact=0.75)
    ps = compute_priority(res)
    assert is_alert_worthy(res, min_priority=7) is (ps.priority >= 7)
    assert is_alert_worthy(res, min_priority=9) is (ps.priority >= 9)


def test_spam_schlaegt_das_gate_in_jedem_fall() -> None:
    res = _result(relevance=1.0, impact=1.0, novelty=1.0)
    assert is_alert_worthy(res, gate_raw=0.0, spam_probability=0.95) is False


# --- die Engine -------------------------------------------------------------


def test_engine_nutzt_bei_der_standardschwelle_den_kalibrierten_wert() -> None:
    assert ThresholdEngine().gate_raw == ALERT_GATE_RAW
    assert ThresholdEngine(min_priority=7).gate_raw == ALERT_GATE_RAW


def test_eine_abweichende_schwelle_wirkt_weiterhin() -> None:
    """Sonst waere eine Konfigurationsaenderung stillschweigend wirkungslos."""
    for p in (5, 6, 8, 9, 10):
        engine = ThresholdEngine(min_priority=p)
        assert engine.gate_raw == pytest.approx(min_priority_as_raw_gate(p))
        assert engine.gate_raw != ALERT_GATE_RAW


def test_engine_entscheidet_wie_is_alert_worthy() -> None:
    engine = ThresholdEngine()
    for x in [i / 100 for i in range(50, 101)]:
        res = _result(relevance=x, impact=x, novelty=x)
        assert engine.should_alert(res) is is_alert_worthy(res, gate_raw=ALERT_GATE_RAW)
