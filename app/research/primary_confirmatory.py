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
from app.research.prereg_window import MaturityCounts, WindowDecision, assert_evaluable
from app.research.samples import Decider, decisions_to_trades_with_counts
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
class Disclosure:
    """Pflichtoffenlegung. ``n_valid`` ist NICHT die Zahl der Feuerungen.

    Eine Feuerung ohne auswertbares Label ist nicht "kein Signal", sondern ein
    nicht beobachtbares Ergebnis. Wer beides zusammenwirft, meldet ein n_valid,
    das eine andere Groesse ist als die, die es zu sein vorgibt.
    """

    raw_fires: int
    label_capable_fires: int
    n_valid: int
    data_unavailable_count: int
    symbols_with_valid_signals: int


@dataclass(frozen=True)
class CostSensitivity:
    """NICHT gatend. Was der Mittelwert bei anderen Kostenannahmen waere.

    Ausdruecklich kein Alternativ-Gate: hinterher "bei 20 bps hat es nicht
    gereicht, bei einem anderen Kostenmodell schon" zu sagen, waere
    nachtraegliche Kriterienaenderung. Die versiegelten Kosten entscheiden.
    """

    round_trip_cost_bps: float
    mean_net_bps: float
    margin_above_floor_bps: float
    status: str = DIAGNOSTIC_STATUS


@dataclass(frozen=True)
class RobustnessDiagnostic:
    """NICHT gatend. CR1 kann gegenueber einem grossen Einzelcluster empfindlich sein.

    Sagt der Primaertest PASS bei +7 bps, ohne den groessten Cluster aber nur
    +1 bps, dann aendert das das versiegelte Verdikt NICHT — es ist aber fuer die
    Entscheidung ueber das anschliessende Shadow-/Operational-Gate genau die
    Zahl, die man sehen will.
    """

    label: str
    without_unit: str | None
    n: int
    n_clusters: int
    mean_bps: float
    p_value: float
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
    round_trip_cost_bps: float = 0.0
    disclosure: Disclosure | None = None
    cost_sensitivity: tuple[CostSensitivity, ...] = ()
    robustness: tuple[RobustnessDiagnostic, ...] = ()
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
    sensitivity_cost_bps: Sequence[float] = (),
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
    pooled_gross: list[float] = []
    signals: list[Signal] = []
    diagnostics: list[SymbolDiagnostic] = []
    raw_fires = 0
    data_unavailable = 0

    for panel in panels:
        trades, counts = decisions_to_trades_with_counts(
            panel.rows, panel.labels, decide, round_trip_cost_bps
        )
        raw_fires += counts.raw_fires
        data_unavailable += counts.data_unavailable
        for trade in trades:
            pooled_gross.append(trade.gross_bps)
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

    disclosure = Disclosure(
        raw_fires=raw_fires,
        label_capable_fires=len(pooled_net),
        n_valid=len(pooled_net),
        data_unavailable_count=data_unavailable,
        symbols_with_valid_signals=sum(1 for d in diagnostics if d.n_signals > 0),
    )

    # net = gross - cost, also ist der Mittelwert bei einer anderen Kostenannahme
    # exakt der Brutto-Mittelwert minus jener Kosten. Keine Neuschaetzung noetig.
    mean_gross = sum(pooled_gross) / len(pooled_gross) if pooled_gross else 0.0
    sensitivity = tuple(
        CostSensitivity(
            round_trip_cost_bps=cost,
            mean_net_bps=mean_gross - cost,
            margin_above_floor_bps=(mean_gross - cost) - economic_floor_bps,
        )
        for cost in sensitivity_cost_bps
    )

    robustness = _robustness(pooled_net, cluster_ids, signals)

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
        round_trip_cost_bps=round_trip_cost_bps,
        disclosure=disclosure,
        cost_sensitivity=sensitivity,
        robustness=robustness,
        reasons=tuple(reasons),
    )


def _robustness(
    values: list[float],
    cluster_ids: list[int],
    signals: list[Signal],
) -> tuple[RobustnessDiagnostic, ...]:
    """Zwei Auslass-Proben, beide NICHT gatend.

    Sie beantworten die Frage, die ein einzelner p-Wert nie beantwortet: traegt
    das Ergebnis ein Prozess — oder eine Stunde und ein Asset?
    """
    if not values:
        return ()

    out: list[RobustnessDiagnostic] = []

    sizes: dict[int, int] = {}
    for cluster in cluster_ids:
        sizes[cluster] = sizes.get(cluster, 0) + 1
    largest = max(sizes, key=lambda key: sizes[key])
    keep = [i for i, cluster in enumerate(cluster_ids) if cluster != largest]
    if keep:
        summary = cluster_robust_mean([values[i] for i in keep], [cluster_ids[i] for i in keep])
        out.append(
            RobustnessDiagnostic(
                label="result_without_largest_cluster",
                without_unit=f"cluster#{largest} (n={sizes[largest]})",
                n=summary.n,
                n_clusters=summary.n_clusters,
                mean_bps=summary.mean_bps,
                p_value=summary.p_value,
            )
        )

    per_symbol: dict[str, int] = {}
    for signal in signals:
        per_symbol[signal.symbol] = per_symbol.get(signal.symbol, 0) + 1
    if per_symbol:
        top = max(per_symbol, key=lambda key: per_symbol[key])
        keep = [i for i, signal in enumerate(signals) if signal.symbol != top]
        if keep:
            summary = cluster_robust_mean([values[i] for i in keep], [cluster_ids[i] for i in keep])
            out.append(
                RobustnessDiagnostic(
                    label="result_without_top_symbol",
                    without_unit=f"{top} (n={per_symbol[top]})",
                    n=summary.n,
                    n_clusters=summary.n_clusters,
                    mean_bps=summary.mean_bps,
                    p_value=summary.p_value,
                )
            )
    return tuple(out)


def maturity_counts(
    panels: Sequence[SymbolPanel],
    decide: Decider,
    *,
    round_trip_cost_bps: float,
    timeframe_ms: int,
    horizon: int,
) -> MaturityCounts:
    """NUR blinde Reifezahlen — kein Mittelwert, kein p-Wert.

    Das ist die einzige Funktion, die vor T1 aufgerufen werden darf. Sie kann
    strukturell keine Performance zurueckgeben, weil ``MaturityCounts`` kein
    Feld dafuer hat.
    """
    signals: list[Signal] = []
    raw_fires = 0
    unavailable = 0
    symbols_with_signals = 0

    for panel in panels:
        trades, counts = decisions_to_trades_with_counts(
            panel.rows, panel.labels, decide, round_trip_cost_bps
        )
        raw_fires += counts.raw_fires
        unavailable += counts.data_unavailable
        if trades:
            symbols_with_signals += 1
        signals.extend(Signal(panel.symbol, _to_ms(t.timestamp_utc), t.side) for t in trades)

    ids = assign_clusters(signals, timeframe_ms=timeframe_ms, horizon=horizon)
    return MaturityCounts(
        n_valid=len(signals),
        n_clusters=len(set(ids)),
        raw_fires=raw_fires,
        label_capable_fires=len(signals),
        data_unavailable_count=unavailable,
        symbols_with_valid_signals=symbols_with_signals,
    )


def run_confirmatory(
    decision: WindowDecision,
    panels: Sequence[SymbolPanel],
    decide: Decider,
    **kwargs: object,
) -> PrimaryConfirmatoryResult:
    """Torwaechter: der Primaertest laeuft NUR an einem Entscheidungszeitpunkt.

    Fail-closed. Ein p-Wert, den niemand haette sehen duerfen, laesst sich nicht
    zurueckziehen — deshalb ein Abbruch statt einer Warnung.
    """
    assert_evaluable(decision)
    return evaluate_primary(panels, decide, **kwargs)  # type: ignore[arg-type]
