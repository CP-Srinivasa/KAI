"""Student-t-Verteilung und regularisierte unvollstaendige Betafunktion.

Deterministisch, ohne SciPy, ohne Zufall. Herausgeloest aus
``app/risk/portfolio_risk.py``, wo diese Numerik seit jeher als privater Block
lag — der cluster-robuste Primaertest (``app/research/pooled_inference.py``)
braucht dieselbe Verteilung, und zwei Implementierungen derselben Mathematik
sind genau der Truth-Drift, den KAI andernorts bereits teuer bezahlt hat
("Research sagt A, Production rechnet A'").

Verhalten unveraendert: der Code ist wortgleich uebernommen, ``portfolio_risk``
importiert ihn jetzt nur noch. Die bestehenden Risiko-Tests decken das ab.

**Warum ueberhaupt t und nicht die Normalapproximation:** bei cluster-robuster
Inferenz zaehlt nicht die Zahl der Beobachtungen, sondern die Zahl der Cluster.
Bei moderatem G ist die Normalapproximation spuerbar zu grosszuegig — sie
behauptet Signifikanz, die die Daten nicht tragen. ``df = G - 1`` ist die
uebliche, konservative Wahl.
"""

from __future__ import annotations

import math


def beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Kettenbruch-Darstellung der unvollstaendigen Betafunktion.

    Numerical-Recipes-Stil (Lentz); ~30 Terme genuegen fuer doppelte Genauigkeit,
    die Schleife bricht frueh ab.
    """
    eps = 3e-12
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """Regularisierte unvollstaendige Betafunktion ``I_x(a, b)``."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * beta_continued_fraction(a, b, x) / a
    return 1.0 - bt * beta_continued_fraction(b, a, 1.0 - x) / b


def student_t_cdf(t: float, df: float) -> float:
    """CDF der *unstandardisierten* Student-t (Varianz ``df/(df-2)``)."""
    x = df / (df + t * t)
    half = 0.5 * regularized_incomplete_beta(df / 2.0, 0.5, x)
    return 1.0 - half if t > 0 else half


def student_t_sf(t: float, df: float) -> float:
    """Rechter Tail ``P(T > t)`` — der einseitige p-Wert fuer ``H0: mean <= 0``.

    Ueber die CDF ausgedrueckt statt ueber ``1 - cdf``, damit im relevanten
    rechten Tail keine Ausloeschung entsteht: dort ist ``cdf`` nahe 1, und
    ``1 - 0.9999999`` verliert genau die Stellen, auf die es ankommt.
    """
    if df <= 0.0 or not math.isfinite(t):
        return 1.0 if t <= 0.0 or not math.isfinite(t) else 0.0
    x = df / (df + t * t)
    half = 0.5 * regularized_incomplete_beta(df / 2.0, 0.5, x)
    return half if t > 0 else 1.0 - half
