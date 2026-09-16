"""Naive Basisrate fuer Richtungs-Praezision — rein, ohne I/O (S2, 2026-09-16).

**Warum es das braucht.** Messung S1 am 16.09. (30 Tage, reife Episoden): die
Episoden-Praezision lag exakt auf der naiven Basisrate "immer bullish zu jeder
vollen Stunde" — TradingView 55,6 % gegen 56,0 %, Technical 60,8 % gegen 60,5 %.
Alle Episoden im Fenster waren bullish, und die Treffer-Regel des Annotators ist
grosszuegig: ein bullischer Alert trifft, wenn der Kurs in IRGENDEINEM Fenster von
1 h bis 168 h die skalierte Schwelle uebersteigt. Eine Praezisionszahl ohne diese
Basisrate daneben misst also vor allem, wie oft der Markt gestiegen ist.

**Eine Quelle der Wahrheit.** Fenster, Schwellen-Skalierung, Basisschwelle und die
Bewertungsalter kommen direkt aus ``app/alerts/auto_annotator`` — keine Kopie, die
still auseinanderlaufen koennte (Lehre: EIN Katalog).

**Lebenszyklus, nicht Momentaufnahme.** Der Annotator bewertet einen Alert ab
``_DEFAULT_MIN_AGE_HOURS`` Alter; hit und miss sind dann final, nur inconclusive
wird ab ``_REEVAL_MIN_AGE_HOURS`` und bis ``_STALE_REEVAL_WINDOW_HOURS`` neu
bewertet. Eine erste S1-Rechnung gab jeder Stichprobe alle fuenf Fenster auf
einmal und ergab 67,6 % bzw. 81,6 % — eine ueberhoehte Basis, die die Signale
faelschlich schlechter als Zufall aussehen liess. Verworfen.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.alerts import auto_annotator as _ann

PriceSeries = Sequence[tuple[datetime, float]]

WINDOWS: tuple[float, ...] = _ann._MULTI_WINDOW_HOURS
scaled_threshold = _ann._scaled_threshold
BASE_MOVE_THRESHOLD: float = _ann._DEFAULT_MOVE_THRESHOLD
STAGE_AGES_HOURS: tuple[float, float, float] = (
    _ann._DEFAULT_MIN_AGE_HOURS,
    _ann._REEVAL_MIN_AGE_HOURS,
    _ann._STALE_REEVAL_WINDOW_HOURS,
)

# Wie BinanceAdapter.get_price_change_between: naechste Kerze, sonst nichts.
MAX_POINT_GAP = timedelta(hours=3)


def _times(series: PriceSeries) -> list[datetime]:
    return [t for t, _ in series]


def _nearest(times: Sequence[datetime], series: PriceSeries, moment: datetime) -> float | None:
    i = bisect_left(times, moment)
    best: tuple[timedelta, float] | None = None
    for j in (i - 1, i):
        if 0 <= j < len(series):
            gap = abs(series[j][0] - moment)
            if gap <= MAX_POINT_GAP and (best is None or gap < best[0]):
                best = (gap, series[j][1])
    return None if best is None else best[1]


def nearest_price(series: PriceSeries, moment: datetime) -> float | None:
    """Schlusskurs der zeitlich naechsten Kerze, oder ``None`` bei zu grosser Luecke."""
    if not series:
        return None
    return _nearest(_times(series), series, moment)


def _lifecycle(
    s_times: Sequence[datetime],
    series: PriceSeries,
    b_times: Sequence[datetime],
    btc: PriceSeries,
    moment: datetime,
) -> str | None:
    start_price = _nearest(s_times, series, moment)
    if not start_price:
        return None
    last: str | None = None
    for age in STAGE_AGES_HOURS:
        at = moment + timedelta(hours=age)
        now_p = _nearest(b_times, btc, at) if btc else None
        before = _nearest(b_times, btc, at - timedelta(hours=24)) if btc else None
        vol = (now_p - before) / before * 100.0 if now_p and before else None
        opposite = False
        seen = False
        outcome: str | None = None
        for window_h in WINDOWS:
            if window_h > age:
                continue
            end_price = _nearest(s_times, series, moment + timedelta(hours=window_h))
            if not end_price:
                continue
            seen = True
            pct = (end_price - start_price) / start_price * 100.0
            threshold = scaled_threshold(window_h, BASE_MOVE_THRESHOLD, vol)
            if pct >= threshold:
                outcome = "hit"
                break
            if pct <= -threshold:
                opposite = True
        if outcome is None and seen:
            outcome = "miss" if opposite else "inconclusive"
        if outcome is None:
            continue
        last = outcome
        if outcome in ("hit", "miss"):
            return outcome
    return last


def lifecycle_bullish_outcome(
    series: PriceSeries, btc: PriceSeries, moment: datetime
) -> str | None:
    """Was der Annotator fuer einen bullischen Alert zu ``moment`` entschieden haette.

    Bewertet in den Altern ``STAGE_AGES_HOURS`` jeweils mit den bis dahin
    abgelaufenen Fenstern; die Volatilitaet ist die BTC-24-h-Veraenderung zum
    jeweiligen Bewertungszeitpunkt. hit/miss beenden den Lebenszyklus.
    """
    if not series:
        return None
    return _lifecycle(_times(series), series, _times(btc), btc, moment)


@dataclass(frozen=True)
class BaselineCounts:
    hit: int
    miss: int
    inconclusive: int
    # Grundgesamtheit: alle betrachteten Stunden, auch solche ohne Preis. Die
    # Differenz zu hit+miss+inconclusive zeigt, wie viel Luecke die Quote verdeckt.
    population: int = 0

    @property
    def rate(self) -> float | None:
        decided = self.hit + self.miss
        return None if decided == 0 else self.hit / decided


def hourly_baseline(
    series: PriceSeries, btc: PriceSeries, *, start: datetime, end: datetime
) -> BaselineCounts:
    """ "Immer bullish" zu jeder vollen Stunde in ``[start, end]``, Lebenszyklus-treu."""
    hit = miss = inconclusive = population = 0
    s_times, b_times = _times(series), _times(btc)
    moment = start.replace(minute=0, second=0, microsecond=0)
    while moment <= end:
        population += 1
        outcome = _lifecycle(s_times, series, b_times, btc, moment)
        if outcome == "hit":
            hit += 1
        elif outcome == "miss":
            miss += 1
        elif outcome == "inconclusive":
            inconclusive += 1
        moment += timedelta(hours=1)
    return BaselineCounts(hit=hit, miss=miss, inconclusive=inconclusive, population=population)


def weighted_rate(weights: Mapping[str, int], rates: Mapping[str, float]) -> float | None:
    """Basisrate gewichtet nach der Basiswert-Verteilung eines Pfads.

    Basiswerte ohne eigene Basisrate fallen aus Zaehler UND Nenner — eine fehlende
    Basis darf die gewichtete Zahl weder druecken noch heben.
    """
    total = sum(n for asset, n in weights.items() if asset in rates)
    if total == 0:
        return None
    return sum(n * rates[asset] for asset, n in weights.items() if asset in rates) / total


def compare_to_baseline(*, hits: int, n: int, baseline_rate: float | None) -> dict[str, object]:
    """Vorab festgelegtes S1-Kriterium: Timing-Wert nur, wenn Wilson-95-Untergrenze > Basis."""
    from app.alerts.provenance_metrics import wilson_ci

    ci = wilson_ci(hits, n)
    low, high = ci if ci is not None else (None, None)
    timing: bool | None
    if baseline_rate is None or low is None:
        timing = None
    else:
        timing = low > baseline_rate
    return {
        "numerator": hits,
        "denominator": n,
        "hits": hits,
        "n": n,
        "precision": (hits / n) if n else None,
        "wilson_low": low,
        "wilson_high": high,
        "baseline_rate": baseline_rate,
        "timing_value": timing,
    }


__all__ = [
    "BASE_MOVE_THRESHOLD",
    "STAGE_AGES_HOURS",
    "WINDOWS",
    "BaselineCounts",
    "compare_to_baseline",
    "hourly_baseline",
    "lifecycle_bullish_outcome",
    "nearest_price",
    "scaled_threshold",
    "weighted_rate",
]
