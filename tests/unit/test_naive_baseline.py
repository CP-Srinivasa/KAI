"""Naive Basisrate fuer die Signal-Praezision (S2, Daily Review 2026-09-16).

Messung S1 am 16.09. (30 Tage, reife Episoden): die Signal-Praezision lag exakt auf
der naiven Basisrate "immer bullish zu jeder vollen Stunde" -- TradingView 55,6 %
gegen 56,0 %, Technical 60,8 % gegen 60,5 %. Ohne die Basisrate daneben ist jede
Praezisionszahl als Qualitaet unlesbar: alle Episoden im Fenster waren bullish,
und die Treffer-Regel ist grosszuegig (irgendein Fenster von 1 h bis 168 h).

Die Basisrate MUSS den Lebenszyklus des Annotators emulieren. Eine erste Rechnung,
die allen Stichproben alle fuenf Fenster auf einmal gab, ergab 67,6 % bzw. 81,6 %
und wurde verworfen: der Annotator bewertet ab ~4 h Alter, hit/miss sind dann
final, nur inconclusive wird spaeter neu bewertet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.alerts import auto_annotator as ann
from app.research.naive_baseline import (
    BaselineCounts,
    compare_to_baseline,
    hourly_baseline,
    lifecycle_bullish_outcome,
    nearest_price,
    weighted_rate,
)

T0 = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)


def _series(points: dict[float, float], *, start: datetime = T0) -> list[tuple[datetime, float]]:
    """Stundenwerte relativ zu ``start`` (Schluessel in Stunden)."""
    return sorted((start + timedelta(hours=h), p) for h, p in points.items())


def _flat_btc(price: float = 100.0, hours: int = 400) -> list[tuple[datetime, float]]:
    """BTC ohne 24-h-Veraenderung -> Volatilitaetsfaktor 0,6 wie im Annotator."""
    return _series({float(h): price for h in range(-48, hours)})


# ---------------------------------------------------------------------------
# Eine Quelle der Wahrheit: die Regel kommt aus dem Annotator
# ---------------------------------------------------------------------------


def test_schwelle_und_fenster_stammen_aus_dem_annotator() -> None:
    from app.research import naive_baseline as nb

    assert nb.WINDOWS is ann._MULTI_WINDOW_HOURS
    assert nb.scaled_threshold is ann._scaled_threshold
    assert nb.BASE_MOVE_THRESHOLD == ann._DEFAULT_MOVE_THRESHOLD
    assert nb.STAGE_AGES_HOURS == (
        ann._DEFAULT_MIN_AGE_HOURS,
        ann._REEVAL_MIN_AGE_HOURS,
        ann._STALE_REEVAL_WINDOW_HOURS,
    )


# ---------------------------------------------------------------------------
# Preis
# ---------------------------------------------------------------------------


def test_naechster_preis_innerhalb_von_drei_stunden() -> None:
    s = _series({0.0: 10.0, 5.0: 20.0})
    assert nearest_price(s, T0 + timedelta(hours=1)) == 10.0
    assert nearest_price(s, T0 + timedelta(hours=4)) == 20.0


def test_zu_grosse_luecke_liefert_nichts() -> None:
    s = _series({0.0: 10.0})
    assert nearest_price(s, T0 + timedelta(hours=4)) is None


# ---------------------------------------------------------------------------
# Lebenszyklus
# ---------------------------------------------------------------------------


def test_treffer_im_ersten_bewertungsschritt() -> None:
    # 4-h-Schwelle bei flachem BTC: 1.0 * 0.7 * 0.6 = 0.42 %
    s = _series({0.0: 100.0, 1.0: 100.0, 4.0: 100.5})
    assert lifecycle_bullish_outcome(s, _flat_btc(), T0) == "hit"


def test_fehlschlag_im_ersten_schritt_ist_final() -> None:
    """Genau die Asymmetrie, die die erste S1-Rechnung verfehlte: ein spaeterer
    Anstieg darf einen frueh entschiedenen Fehlschlag nicht mehr drehen."""
    s = _series({0.0: 100.0, 1.0: 99.0, 4.0: 99.0, 24.0: 110.0, 72.0: 120.0, 168.0: 130.0})
    assert lifecycle_bullish_outcome(s, _flat_btc(), T0) == "miss"


def test_unentschieden_wird_spaeter_neu_bewertet() -> None:
    s = _series({0.0: 100.0, 1.0: 100.1, 4.0: 100.1, 24.0: 102.0})
    assert lifecycle_bullish_outcome(s, _flat_btc(), T0) == "hit"


def test_bleibt_unentschieden_wenn_nichts_die_schwelle_kreuzt() -> None:
    s = _series({0.0: 100.0, 1.0: 100.1, 4.0: 100.1, 24.0: 100.2, 72.0: 100.2, 168.0: 100.3})
    assert lifecycle_bullish_outcome(s, _flat_btc(), T0) == "inconclusive"


def test_ohne_startpreis_kein_ergebnis() -> None:
    s = _series({10.0: 100.0})
    assert lifecycle_bullish_outcome(s, _flat_btc(), T0) is None


# ---------------------------------------------------------------------------
# Stundenweise Basisrate
# ---------------------------------------------------------------------------


def test_stundenweise_basis_zaehlt_jede_volle_stunde() -> None:
    rising = _series({float(h): 100.0 * (1.001**h) for h in range(-2, 400)})
    counts = hourly_baseline(rising, _flat_btc(), start=T0, end=T0 + timedelta(hours=9))
    assert counts.hit + counts.miss + counts.inconclusive == 10
    assert counts.hit == 10
    assert counts.rate == 1.0


def test_basis_ohne_entschiedene_stunden_hat_keine_quote() -> None:
    assert BaselineCounts(hit=0, miss=0, inconclusive=5).rate is None


# ---------------------------------------------------------------------------
# Gewichtung und Vergleich
# ---------------------------------------------------------------------------


def test_gewichtet_nach_den_basiswerten_des_pfads() -> None:
    rate = weighted_rate({"BTC/USDT": 30, "ETH/USDT": 10}, {"BTC/USDT": 0.50, "ETH/USDT": 0.70})
    assert rate == pytest.approx(0.55)


def test_basiswerte_ohne_basis_fallen_aus_der_gewichtung() -> None:
    rate = weighted_rate({"BTC/USDT": 10, "XYZ/USDT": 90}, {"BTC/USDT": 0.60})
    assert rate == pytest.approx(0.60)


def test_ohne_ueberlappung_keine_gewichtete_quote() -> None:
    assert weighted_rate({"XYZ/USDT": 5}, {}) is None


def test_vergleich_verlangt_untergrenze_ueber_der_basis() -> None:
    """Vorab festgelegtes S1-Kriterium: Wilson-95-Untergrenze > Basisrate."""
    strong = compare_to_baseline(hits=90, n=100, baseline_rate=0.60)
    assert strong["timing_value"] is True

    s1_technical = compare_to_baseline(hits=59, n=97, baseline_rate=0.605)
    assert s1_technical["timing_value"] is False
    assert s1_technical["wilson_low"] == pytest.approx(0.509, abs=0.001)


def test_vergleich_ohne_basis_urteilt_nicht() -> None:
    result = compare_to_baseline(hits=5, n=10, baseline_rate=None)
    assert result["timing_value"] is None
