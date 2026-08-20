"""Wie viele *unabhaengige* Beobachtungen stecken in n Signalen?

Der Anlass ist eine Zahl, die groesser aussieht als sie ist. Ueber ein Universum
von 34 Assets feuert eine Regel oft nicht 34-mal unabhaengig, sondern einmal auf
einen gemeinsamen Marktimpuls::

    10:00   BTC feuert
    10:00   ETH feuert
    10:00   SOL feuert
    10:00   ADA feuert
    10:00   AVAX feuert

Formal fuenf Signale, oekonomisch naeherungsweise eines. Dazu kommt bei
``horizon = 4`` die serielle Ueberlappung: Signale um 10:00, 11:00 und 12:00
teilen sich Haltekerzen und damit einen grossen Teil ihrer Rendite.

``n = 100`` sind deshalb nicht automatisch 100 unabhaengige Beobachtungen — und
``se = std / sqrt(n)`` (was ``app/research/stats.py`` rechnet) unterschaetzt den
Standardfehler genau um diesen Faktor. Ein p-Wert daraus ist zu klein, und zwar
systematisch in die Richtung, in die man sich gern irrt.

**Cluster-Definition, vor T0 festgelegt.** Ein Signal auf der Kerze mit
Oeffnungszeit ``T`` belegt das Haltefenster::

    Einstieg = open(T + 1 Kerze)
    Ausstieg = close(T + h Kerzen)
    Fenster  = [ T + dt , T + (h+1)*dt )

Zwei Signale sind verbunden, wenn ihre Fenster ueberlappen — **symboluebergreifend**,
weil genau die Gleichzeitigkeit ueber Assets die staerkste Abhaengigkeit ist. Da
alle Fenster gleich lang sind, ist das aequivalent zu::

    |T_a - T_b| < h * dt

Cluster sind die Zusammenhangskomponenten dieser Relation, also Single-Linkage
auf der Zeitachse. Das ist die **konservative** Lesart: eine Kette von Signalen
im Stundenabstand bildet einen langen Cluster. Ob diese Verkettung den Bestand
zu einem einzigen Riesencluster zusammenzieht, ist keine Geschmacksfrage, sondern
messbar — deshalb meldet ``ClusterStats`` ausdruecklich ``max_cluster_size`` und
``max_cluster_span_bars``.

Dieses Modul sieht **keine Renditen**. Es kennt nur ``symbol``, ``timestamp`` und
das Haltefenster. Nur deshalb darf die Abhaengigkeitsanalyse vor T0 laufen.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    """Ein Feuern der Regel. Bewusst ohne Rendite, ohne Preis, ohne Outcome."""

    symbol: str
    bar_open_ms: int
    side: int = 0  # +1/-1, nur Diagnose


@dataclass(frozen=True)
class LeaveOneOut:
    """Was uebrig bleibt, wenn das staerkste Symbol wegfaellt."""

    symbol: str | None
    n_signals: int
    n_clusters: int


@dataclass(frozen=True)
class ClusterStats:
    """Was aus n Feuerungen an unabhaengiger Information uebrig bleibt.

    Traegt die Zerlegung mit, nicht nur das Aggregat (Direktive 2026-08-08):
    ``per_symbol_signals`` ist die Gruppentabelle, ``leave_one_out_top_symbol``
    die Antwort auf "traegt ein einziges Asset das Ergebnis?". Beides ist hier
    keine Formalie — eine Reifeschranke, die auf einer Rate beruht, die zu 30 %
    von einem Symbol kommt, ist eine andere Zahl als eine breit getragene.
    """

    n_signals: int
    n_unique_bars: int
    n_symbols: int
    n_clusters: int
    median_cluster_size: float
    max_cluster_size: int
    max_cluster_span_bars: int
    mean_symbols_per_cluster: float
    top_symbol_share: float
    top_cluster_share: float
    per_symbol_signals: dict[str, int]
    leave_one_out_top_symbol: LeaveOneOut

    @property
    def effective_sample_ratio(self) -> float:
        """Cluster je Signal. 1,0 = vollstaendig unabhaengig, 0,1 = zehnfach geteilt."""
        return self.n_clusters / self.n_signals if self.n_signals else 0.0


def assign_clusters(
    signals: Sequence[Signal],
    *,
    timeframe_ms: int,
    horizon: int,
) -> list[int]:
    """Cluster-ID je Signal, in der Reihenfolge der Eingabe.

    IDs sind aufsteigend nach Zeit vergeben (Cluster 0 ist der frueheste), damit
    das Ergebnis reproduzierbar ist und nicht von der Eingabereihenfolge abhaengt.

    Args:
        signals: die Feuerungen; Reihenfolge beliebig.
        timeframe_ms: Kerzenlaenge in Millisekunden (1h = 3_600_000).
        horizon: Haltedauer in Kerzen. Muss >= 1 sein.

    Returns:
        Liste gleicher Laenge wie ``signals`` mit der Cluster-ID je Signal.

    Raises:
        ValueError: horizon < 1 oder timeframe_ms <= 0.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if timeframe_ms <= 0:
        raise ValueError("timeframe_ms must be > 0")
    if not signals:
        return []

    gap = horizon * timeframe_ms
    order = sorted(range(len(signals)), key=lambda i: (signals[i].bar_open_ms, signals[i].symbol))

    out = [0] * len(signals)
    cluster = 0
    previous_ms = signals[order[0]].bar_open_ms
    for position, index in enumerate(order):
        current_ms = signals[index].bar_open_ms
        # Streng kleiner: zwei Fenster, die sich exakt beruehren, ueberlappen nicht.
        if position > 0 and current_ms - previous_ms >= gap:
            cluster += 1
        out[index] = cluster
        previous_ms = current_ms
    return out


def summarize_clusters(
    signals: Sequence[Signal],
    *,
    timeframe_ms: int,
    horizon: int,
) -> ClusterStats:
    """Konzentration und Verkettung in einem Rutsch — kein Aggregat ohne Zerlegung."""
    if not signals:
        return ClusterStats(
            n_signals=0,
            n_unique_bars=0,
            n_symbols=0,
            n_clusters=0,
            median_cluster_size=0.0,
            max_cluster_size=0,
            max_cluster_span_bars=0,
            mean_symbols_per_cluster=0.0,
            top_symbol_share=0.0,
            top_cluster_share=0.0,
            per_symbol_signals={},
            leave_one_out_top_symbol=LeaveOneOut(symbol=None, n_signals=0, n_clusters=0),
        )

    ids = assign_clusters(signals, timeframe_ms=timeframe_ms, horizon=horizon)

    members: dict[int, list[Signal]] = {}
    for cluster_id, signal in zip(ids, signals, strict=True):
        members.setdefault(cluster_id, []).append(signal)

    sizes = [len(group) for group in members.values()]
    spans = [
        (max(s.bar_open_ms for s in group) - min(s.bar_open_ms for s in group)) // timeframe_ms
        for group in members.values()
    ]
    per_symbol: dict[str, int] = {}
    for signal in signals:
        per_symbol[signal.symbol] = per_symbol.get(signal.symbol, 0) + 1

    # Zerlegung: traegt ein einziges Asset die Rate? Ohne diese Antwort ist eine
    # daraus abgeleitete Reifeschranke nicht beurteilbar.
    top_symbol = max(per_symbol, key=lambda key: per_symbol[key])
    rest = [s for s in signals if s.symbol != top_symbol]
    rest_clusters = (
        len(set(assign_clusters(rest, timeframe_ms=timeframe_ms, horizon=horizon))) if rest else 0
    )

    return ClusterStats(
        n_signals=len(signals),
        n_unique_bars=len({s.bar_open_ms for s in signals}),
        n_symbols=len(per_symbol),
        n_clusters=len(members),
        median_cluster_size=float(statistics.median(sizes)),
        max_cluster_size=max(sizes),
        max_cluster_span_bars=max(spans),
        mean_symbols_per_cluster=statistics.fmean(
            len({s.symbol for s in group}) for group in members.values()
        ),
        top_symbol_share=max(per_symbol.values()) / len(signals),
        top_cluster_share=max(sizes) / len(signals),
        per_symbol_signals=dict(per_symbol),
        leave_one_out_top_symbol=LeaveOneOut(
            symbol=top_symbol,
            n_signals=len(rest),
            n_clusters=rest_clusters,
        ),
    )
