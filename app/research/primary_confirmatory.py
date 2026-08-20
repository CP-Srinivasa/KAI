"""Der Primaertest: EIN Estimand, EIN p-Wert, EIN Verdikt.

Der Vertrag aus C3b, hier als Code statt als Absichtserklaerung. Drei Regeln,
die er durchsetzt:

**1. Gepoolt, nicht pro Symbol.** ``run_symbol_search`` erzeugt je Symbol einen
eigenen p-Wert. Ueber 34 Assets waeren das 34 Tests derselben Hypothese und
``m = 1`` waere gebrochen. Hier entsteht genau ein Sample ueber das gesamte
versiegelte Universum und genau ein Verdikt. Per-Symbol-Zahlen sind
``DIAGNOSTIC_NON_GATING`` — sie stehen im Bericht, sie gaten nichts.

**2. Abhaengigkeit im Standardfehler.** Gleichzeitige Signale ueber korrelierte
Assets und ueberlappende Haltefenster sind keine unabhaengigen Beobachtungen.
Der cluster-robuste Sandwich zaehlt Freiheitsgrade in Clustern.

**3. Unreif ist NICHT widerlegt.** Die teuerste Lektion aus ND-v2: ein Fenster,
das ohne ``n >= n_min`` endet, liefert ``INCONCLUSIVE_NOT_MATURE`` und
ausdruecklich kein ``NOT_MET``. Reife hat hier zwei Bedingungen — genug Signale
UND genug unabhaengige Cluster. Die zweite ist neu und noetig: 100 Signale in
drei Clustern sind keine 100 Beobachtungen, und ohne diese Schranke koennte ein
einziger Marktimpuls formale Reife vortaeuschen.

Das Modul rechnet **keinen** Backfill und trifft **keine** Netzentscheidung; es
bekommt fertige Zeilen, Labels und Zeitstempel. Der eine konfirmatorische Lauf
liegt hinter T0 — dieses Modul existiert vorher, damit die Regeln vorher
feststehen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.analysis.features.feature_matrix import FeatureRow
from app.research.pooled_inference import ClusterRobustSummary, cluster_robust_mean
from app.research.samples import Decider, decisions_to_trades
from app.research.signal_clusters import ClusterStats, Signal, assign_clusters, summarize_clusters

VERDICT_PASS = "PASS"
VERDICT_NOT_MET = "NOT_MET"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE_NOT_MATURE"

DIAGNOSTIC_STATUS = "DIAGNOSTIC_NON_GATING"


@dataclass(frozen=True)
class SymbolPanel:
    """Die Eingabe je Symbol. Zeitstempel getrennt, weil Cluster Zeit brauchen."""

    symbol: str
    rows: list[FeatureRow]
    labels: list[float | None]


@dataclass(frozen=True)
class SymbolDiagnostic:
    """Per-Symbol-Zahlen. Nie gatend — sie beantworten eine andere Frage."""

    symbol: str
    n_signals: int
    n_long: int
    n_short: int
    mean_bps: float | None
    status: str = DIAGNOSTIC_STATUS


@dataclass(frozen=True)
class PrimaryConfirmatoryResult:
    """Das eine Ergebnis des Primaertests."""

    hypothesis: str
    universe_sha256: str
    n_symbols: int
    verdict: str
    summary: ClusterRobustSummary
    clusters: ClusterStats
    per_symbol: tuple[SymbolDiagnostic, ...]
    n_min: int
    cluster_min: int
    alpha: float
    economic_floor_bps: float
    reasons: tuple[str, ...] = ()


def _to_ms(timestamp_utc: str) -> int:
    parsed = datetime.fromisoformat(timestamp_utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def evaluate_primary(
    panels: Sequence[SymbolPanel],
    decide: Decider,
    *,
    hypothesis: str,
    universe_sha256: str,
    round_trip_cost_bps: float,
    timeframe_ms: int,
    horizon: int,
    n_min: int,
    cluster_min: int,
    alpha: float = 0.05,
    economic_floor_bps: float = 0.0,
) -> PrimaryConfirmatoryResult:
    """Pool alle Symbole zu EINEM Test und faelle EIN Verdikt.

    Args:
        panels: je Symbol die kausalen Feature-Zeilen und die dazu ausgerichteten
            Forward-Labels (Next-Open-Konvention fuer diesen Test).
        decide: die eine praeregistrierte Regel.
        universe_sha256: Hash des versiegelten Universums — wandert unveraendert
            in das Ergebnis, damit ein Verdikt nie ohne seine Population zitiert
            werden kann.
        round_trip_cost_bps: Kosten je Trade; wird von ``decisions_to_trades``
            angewendet (dieselbe Arithmetik wie ueberall sonst, keine Kopie).
        timeframe_ms / horizon: definieren das Haltefenster und damit die Cluster.
        n_min / cluster_min: die beiden Reifeschranken.
        alpha: Signifikanzniveau. Bei m=1 ist BH-FDR rechnerisch ``p <= alpha``.
        economic_floor_bps: oekonomische Mindesthuerde auf den gepoolten
            Mittelwert. Statistische Signifikanz allein ist keine Produktionsreife.

    Returns:
        PrimaryConfirmatoryResult mit genau einem ``verdict``.
    """
    pooled_net: list[float] = []
    signals: list[Signal] = []
    diagnostics: list[SymbolDiagnostic] = []

    for panel in panels:
        trades = decisions_to_trades(panel.rows, panel.labels, decide, round_trip_cost_bps)
        for trade in trades:
            pooled_net.append(trade.net_bps)
            signals.append(
                Signal(
                    symbol=panel.symbol,
                    bar_open_ms=_to_ms(trade.timestamp_utc),
                    side=trade.side,
                )
            )
        longs = sum(1 for t in trades if t.side == 1)
        diagnostics.append(
            SymbolDiagnostic(
                symbol=panel.symbol,
                n_signals=len(trades),
                n_long=longs,
                n_short=len(trades) - longs,
                mean_bps=(sum(t.net_bps for t in trades) / len(trades) if trades else None),
            )
        )

    clusters = summarize_clusters(signals, timeframe_ms=timeframe_ms, horizon=horizon)
    cluster_ids = assign_clusters(signals, timeframe_ms=timeframe_ms, horizon=horizon)
    summary = cluster_robust_mean(pooled_net, cluster_ids)

    reasons: list[str] = []
    if summary.n < n_min:
        reasons.append(f"n={summary.n} < n_min={n_min}")
    if summary.n_clusters < cluster_min:
        reasons.append(f"clusters={summary.n_clusters} < cluster_min={cluster_min}")

    if reasons:
        # Unreif ist kein Urteil ueber die Sachfrage. NIE NOT_MET.
        verdict = VERDICT_INCONCLUSIVE
    elif summary.p_value <= alpha and summary.mean_bps >= economic_floor_bps:
        verdict = VERDICT_PASS
    else:
        verdict = VERDICT_NOT_MET
        if summary.p_value > alpha:
            reasons.append(f"p={summary.p_value:.4f} > alpha={alpha}")
        if summary.mean_bps < economic_floor_bps:
            reasons.append(f"mean={summary.mean_bps:.2f}bps < floor={economic_floor_bps}bps")

    return PrimaryConfirmatoryResult(
        hypothesis=hypothesis,
        universe_sha256=universe_sha256,
        n_symbols=len(panels),
        verdict=verdict,
        summary=summary,
        clusters=clusters,
        per_symbol=tuple(diagnostics),
        n_min=n_min,
        cluster_min=cluster_min,
        alpha=alpha,
        economic_floor_bps=economic_floor_bps,
        reasons=tuple(reasons),
    )
