r"""Aus 34 Symbolen darf kein 34-facher Test werden — und n ist nicht n.

Zwei Befunde am bestehenden Code, beide vor T0 aufgefallen:

**1. Der Runner testet pro Symbol.** ``run_symbol_search`` ruft je Symbol
``search_hypotheses``, also einen eigenen p-Wert je Asset. Ueber das versiegelte
34er-Universum waeren das 34 Tests derselben Hypothese, und ``m = 1`` aus C3b
waere gebrochen — die Forschungsfrage waere unbemerkt von "hat diese Regel einen
Edge?" zu "auf welchem Asset funktioniert sie?" gerutscht. Das Zweite ist
Discovery, nicht Konfirmation.

**2. ``stats.summarize_net_bps`` rechnet ``se = std / sqrt(n)``**, also i.i.d.
Bei 34 korrelierten Assets mit ``horizon = 4`` ist das falsch: gleichzeitige
Signale sind naeherungsweise ein Marktimpuls, aufeinanderfolgende teilen sich
Haltekerzen. Der Standardfehler faellt dadurch zu klein aus — systematisch in
die Richtung, in die man sich gern irrt.

Die Tests hier pinnen die Gegenmittel: ein gepoolter Estimand, ein
cluster-robuster Standardfehler mit Freiheitsgraden in **Clustern**, und
Reife-Schranken, die auch die Clusterzahl pruefen.
"""

from __future__ import annotations

import math

import pytest

from app.analysis.features.feature_matrix import FeatureRow
from app.analysis.student_t import student_t_sf
from app.research.pooled_inference import cluster_robust_mean
from app.research.primary_confirmatory import (
    DIAGNOSTIC_STATUS,
    VERDICT_INCONCLUSIVE,
    VERDICT_NOT_MET,
    VERDICT_PASS,
    SymbolPanel,
    evaluate_primary,
)
from app.research.signal_clusters import Signal, assign_clusters, summarize_clusters

_HOUR_MS = 3_600_000
_T0 = 1_770_000_000_000  # fester Anker; kein now()


# ── Student-t ───────────────────────────────────────────────────────────────


def test_student_t_survival_matches_known_values() -> None:
    """Gegen Tabellenwerte, nicht gegen sich selbst."""
    assert student_t_sf(2.0, 10.0) == pytest.approx(0.036694, abs=1e-6)
    assert student_t_sf(0.0, 10.0) == pytest.approx(0.5, abs=1e-9)
    assert student_t_sf(-2.0, 10.0) == pytest.approx(1.0 - 0.036694, abs=1e-6)


def test_t_is_more_conservative_than_normal_at_small_dof() -> None:
    """Genau deshalb wird t verwendet und nicht ``erfc``.

    Bei cluster-robuster Inferenz zaehlt G, nicht n. Bei kleinem G behauptet die
    Normalapproximation Signifikanz, die die Daten nicht tragen.
    """
    normal_p = 0.5 * math.erfc(2.5 / math.sqrt(2.0))

    assert student_t_sf(2.5, 5.0) > normal_p


# ── Cluster-Zuordnung ───────────────────────────────────────────────────────


def test_simultaneous_signals_across_symbols_are_one_cluster() -> None:
    """Dein Beispiel: BTC/ETH/SOL/ADA/AVAX um 10:00 sind ein Marktimpuls."""
    signals = [Signal(sym, _T0) for sym in ("BTC", "ETH", "SOL", "ADA", "AVAX")]

    ids = assign_clusters(signals, timeframe_ms=_HOUR_MS, horizon=4)

    assert len(set(ids)) == 1


def test_overlapping_holding_windows_are_one_cluster() -> None:
    """10:00, 11:00, 12:00 bei h=4 teilen Haltekerzen."""
    signals = [Signal("BTC", _T0 + k * _HOUR_MS) for k in (0, 1, 2)]

    ids = assign_clusters(signals, timeframe_ms=_HOUR_MS, horizon=4)

    assert len(set(ids)) == 1


def test_windows_that_only_touch_are_separate() -> None:
    """Genau ``h`` Kerzen Abstand: das Fenster ist zu Ende, bevor das naechste beginnt."""
    signals = [Signal("BTC", _T0), Signal("BTC", _T0 + 4 * _HOUR_MS)]

    ids = assign_clusters(signals, timeframe_ms=_HOUR_MS, horizon=4)

    assert len(set(ids)) == 2


def test_one_bar_less_makes_them_overlap() -> None:
    """Die Gegenprobe zur Grenze — sonst waere sie nur behauptet."""
    signals = [Signal("BTC", _T0), Signal("BTC", _T0 + 3 * _HOUR_MS)]

    assert len(set(assign_clusters(signals, timeframe_ms=_HOUR_MS, horizon=4))) == 1


def test_cluster_ids_do_not_depend_on_input_order() -> None:
    a = [Signal("BTC", _T0), Signal("ETH", _T0 + 10 * _HOUR_MS)]
    b = list(reversed(a))

    ids_a = assign_clusters(a, timeframe_ms=_HOUR_MS, horizon=4)
    ids_b = assign_clusters(b, timeframe_ms=_HOUR_MS, horizon=4)

    assert ids_a == list(reversed(ids_b))


def test_chaining_is_visible_not_hidden() -> None:
    """Single-Linkage kann verketten — das muss man SEHEN, nicht ahnen.

    Zwanzig Signale im Stundenabstand bilden bei h=4 einen einzigen Cluster.
    Genau dafuer meldet ClusterStats ``max_cluster_size`` und
    ``max_cluster_span_bars``: wenn die Verkettung den Bestand zusammenzieht,
    steht es im Bericht, statt still die Freiheitsgrade zu verschlucken.
    """
    signals = [Signal("BTC", _T0 + k * _HOUR_MS) for k in range(20)]

    stats = summarize_clusters(signals, timeframe_ms=_HOUR_MS, horizon=4)

    assert stats.n_clusters == 1
    assert stats.max_cluster_size == 20
    assert stats.max_cluster_span_bars == 19
    assert stats.effective_sample_ratio == pytest.approx(1 / 20)


def test_stats_decompose_concentration() -> None:
    """Kein Aggregat ohne Zerlegung — auch hier nicht."""
    signals = [
        *[Signal("BTC", _T0 + k * 100 * _HOUR_MS) for k in range(8)],
        Signal("ETH", _T0 + 5000 * _HOUR_MS),
        Signal("SOL", _T0 + 9000 * _HOUR_MS),
    ]

    stats = summarize_clusters(signals, timeframe_ms=_HOUR_MS, horizon=4)

    assert stats.n_signals == 10
    assert stats.n_symbols == 3
    assert stats.n_clusters == 10, "weit auseinander -> jeder fuer sich"
    assert stats.top_symbol_share == pytest.approx(0.8)
    assert stats.effective_sample_ratio == pytest.approx(1.0)


def test_empty_input_is_not_a_crash() -> None:
    stats = summarize_clusters([], timeframe_ms=_HOUR_MS, horizon=4)

    assert stats.n_signals == 0
    assert stats.n_clusters == 0
    assert stats.effective_sample_ratio == 0.0


def test_invalid_parameters_are_rejected() -> None:
    with pytest.raises(ValueError, match="horizon must be >= 1"):
        assign_clusters([Signal("BTC", _T0)], timeframe_ms=_HOUR_MS, horizon=0)
    with pytest.raises(ValueError, match="timeframe_ms must be > 0"):
        assign_clusters([Signal("BTC", _T0)], timeframe_ms=0, horizon=4)


# ── Cluster-robuster Standardfehler ─────────────────────────────────────────


def test_independent_clusters_reproduce_the_naive_error() -> None:
    """Sind die Signale wirklich unabhaengig, darf die Korrektur nichts kosten.

    Ohne diesen Test waere der Sandwich nur ein Pessimist.
    """
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    ids = list(range(6))  # jeder fuer sich

    result = cluster_robust_mean(values, ids)

    assert result.n_clusters == 6
    assert result.variance_inflation == pytest.approx(1.0, rel=0.15)


def test_perfectly_correlated_clusters_inflate_the_error() -> None:
    """Zehn identische Beobachtungen in EINEM Cluster tragen die Evidenz von einem.

    Das ist der Kern: ``se = std/sqrt(n)`` haette hier n=20 gezaehlt.
    """
    values = [10.0] * 10 + [-6.0] * 10
    ids = [0] * 10 + [1] * 10

    result = cluster_robust_mean(values, ids)

    assert result.n_clusters == 2
    assert result.dof == 1
    assert result.se_bps > result.naive_se_bps
    assert result.variance_inflation > 3.0


def test_degrees_of_freedom_count_clusters_not_observations() -> None:
    values = [1.0] * 50 + [3.0] * 50
    ids = [0] * 50 + [1] * 50

    result = cluster_robust_mean(values, ids)

    assert result.n == 100
    assert result.dof == 1, "100 Beobachtungen, aber nur 2 unabhaengige Einheiten"


def test_single_cluster_cannot_be_tested() -> None:
    """Alles haengt zusammen -> keine Streuung zwischen Einheiten schaetzbar."""
    result = cluster_robust_mean([5.0, 6.0, 7.0], [0, 0, 0])

    assert result.n_clusters == 1
    assert result.p_value == 1.0


def test_non_finite_sample_claims_nothing() -> None:
    """Fail-closed, wie in stats.summarize_net_bps."""
    result = cluster_robust_mean([1.0, float("inf"), 2.0], [0, 1, 2])

    assert result.p_value == 1.0
    assert result.mean_bps == 0.0


def test_length_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="equal length"):
        cluster_robust_mean([1.0, 2.0], [0])


def test_clustered_p_value_is_larger_than_the_naive_one() -> None:
    """Der eigentliche Schaden des i.i.d.-Fehlers, in einer Zahl.

    Dieselben Daten, einmal als unabhaengig gelesen und einmal geclustert: der
    naive p-Wert behauptet Signifikanz, der ehrliche nicht.
    """
    values = ([4.0] * 10 + [3.0] * 10) * 3
    clustered = cluster_robust_mean(values, [i // 10 for i in range(60)])
    independent = cluster_robust_mean(values, list(range(60)))

    assert clustered.p_value > independent.p_value


# ── Der Primaertest als Ganzes ──────────────────────────────────────────────


def _row(index: int, *, fire: bool) -> FeatureRow:
    return FeatureRow(
        timestamp_utc=_iso(_T0 + index * _HOUR_MS),
        close=100.0,
        log_return=None,
        rsi_14=31.0 if fire else 50.0,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
        rsi_14_prev=28.0 if fire else 50.0,
        volume_z_20=3.0 if fire else 0.0,
    )


def _iso(ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _always_long(_row_in: FeatureRow) -> int:
    return 1


def _panel(symbol: str, *, n: int, spacing_bars: int) -> SymbolPanel:
    rows = [_row(i * spacing_bars, fire=True) for i in range(n)]
    return SymbolPanel(symbol=symbol, rows=rows, labels=[50.0] * n)


def test_per_symbol_numbers_are_diagnostic_only() -> None:
    """Sie stehen im Bericht — sie gaten nichts."""
    panels = [_panel("BTC", n=40, spacing_bars=50), _panel("ETH", n=40, spacing_bars=50)]

    result = evaluate_primary(
        panels,
        _always_long,
        hypothesis="x",
        universe_sha256="abc",
        round_trip_cost_bps=0.0,
        timeframe_ms=_HOUR_MS,
        horizon=4,
        n_min=10,
        cluster_min=5,
    )

    assert len(result.per_symbol) == 2
    assert all(d.status == DIAGNOSTIC_STATUS for d in result.per_symbol)


def test_there_is_exactly_one_p_value_for_the_whole_universe() -> None:
    """Der Bruch, den C3b verhindern soll: 34 Symbole != 34 Tests."""
    panels = [_panel(sym, n=30, spacing_bars=40) for sym in ("BTC", "ETH", "SOL", "ADA")]

    result = evaluate_primary(
        panels,
        _always_long,
        hypothesis="rsi_reentry_volume_confirmed",
        universe_sha256="abc",
        round_trip_cost_bps=0.0,
        timeframe_ms=_HOUR_MS,
        horizon=4,
        n_min=10,
        cluster_min=5,
    )

    assert result.n_symbols == 4
    assert result.summary.n == 120
    assert isinstance(result.summary.p_value, float)
    assert result.verdict in {VERDICT_PASS, VERDICT_NOT_MET}


def test_immature_sample_is_inconclusive_never_not_met() -> None:
    """Die teuerste Lektion aus ND-v2, hier als Abnahmekriterium."""
    panels = [_panel("BTC", n=5, spacing_bars=50)]

    result = evaluate_primary(
        panels,
        _always_long,
        hypothesis="x",
        universe_sha256="abc",
        round_trip_cost_bps=0.0,
        timeframe_ms=_HOUR_MS,
        horizon=4,
        n_min=100,
        cluster_min=30,
    )

    assert result.verdict == VERDICT_INCONCLUSIVE
    assert result.verdict != VERDICT_NOT_MET
    assert any("n_min" in r for r in result.reasons)


def test_enough_signals_but_too_few_clusters_is_also_immature() -> None:
    """100 Signale in drei Clustern sind keine 100 Beobachtungen.

    Ohne die zweite Schranke koennte ein einziger Marktimpuls formale Reife
    vortaeuschen.
    """
    panels = [_panel(sym, n=25, spacing_bars=1) for sym in ("BTC", "ETH", "SOL", "ADA")]

    result = evaluate_primary(
        panels,
        _always_long,
        hypothesis="x",
        universe_sha256="abc",
        round_trip_cost_bps=0.0,
        timeframe_ms=_HOUR_MS,
        horizon=4,
        n_min=100,
        cluster_min=30,
    )

    assert result.summary.n == 100
    assert result.verdict == VERDICT_INCONCLUSIVE
    assert any("cluster_min" in r for r in result.reasons)


def test_economic_floor_can_veto_a_significant_result() -> None:
    """Statistische Signifikanz allein ist keine Produktionsreife."""
    panels = [_panel(sym, n=40, spacing_bars=40) for sym in ("BTC", "ETH", "SOL")]

    result = evaluate_primary(
        panels,
        _always_long,
        hypothesis="x",
        universe_sha256="abc",
        round_trip_cost_bps=0.0,
        timeframe_ms=_HOUR_MS,
        horizon=4,
        n_min=10,
        cluster_min=5,
        economic_floor_bps=500.0,  # Label ist 50 bps
    )

    assert result.verdict == VERDICT_NOT_MET
    assert any("floor" in r for r in result.reasons)


def test_costs_are_applied_through_the_shared_arithmetic() -> None:
    """Keine zweite Kostenformel — ``decisions_to_trades`` bleibt die Quelle."""
    panels = [_panel("BTC", n=20, spacing_bars=40)]

    with_cost = evaluate_primary(
        panels,
        _always_long,
        hypothesis="x",
        universe_sha256="abc",
        round_trip_cost_bps=20.0,
        timeframe_ms=_HOUR_MS,
        horizon=4,
        n_min=1,
        cluster_min=1,
    )

    assert with_cost.summary.mean_bps == pytest.approx(30.0)


def test_universe_hash_travels_with_the_verdict() -> None:
    """Ein Verdikt darf nie ohne seine Population zitiert werden."""
    result = evaluate_primary(
        [_panel("BTC", n=20, spacing_bars=40)],
        _always_long,
        hypothesis="x",
        universe_sha256="d28e10d5",
        round_trip_cost_bps=0.0,
        timeframe_ms=_HOUR_MS,
        horizon=4,
        n_min=1,
        cluster_min=1,
    )

    assert result.universe_sha256 == "d28e10d5"


# ── Der Frequenz-Report ─────────────────────────────────────────────────────


def test_frequency_report_projects_and_decomposes() -> None:
    """Die Zahl, aus der n_min und cluster_min abgeleitet werden, muss nachrechenbar sein."""
    from app.research.dependency_preflight import build_frequency_report

    by_symbol = {
        "BTC/USDT": [Signal("BTC/USDT", _T0 + k * 100 * _HOUR_MS) for k in range(6)],
        "ETH/USDT": [Signal("ETH/USDT", _T0 + k * 100 * _HOUR_MS) for k in range(4)],
    }

    report = build_frequency_report(by_symbol, raw_fires=12, lookback_days=180, horizon=4)

    assert report.raw_fires == 12
    assert report.label_capable_fires == 10, "zwei Feuerungen hatten kein Ausstiegsfenster"
    assert report.per_symbol_fires == {"BTC/USDT": 6, "ETH/USDT": 4}
    # BTC und ETH feuern zeitgleich -> je Zeitpunkt ein Cluster.
    assert report.clusters.n_clusters == 6
    fires, clusters = report.project(90)
    assert fires == pytest.approx(10 / 180 * 90)
    assert clusters == pytest.approx(6 / 180 * 90)


def test_frequency_report_never_touches_returns() -> None:
    """Struktur-Wache: das Modul darf die Label-Funktionen nicht einmal importieren.

    Sonst waere die Behauptung "der Preflight darf vor T0 laufen" nicht mehr
    ueberpruefbar, sondern nur noch geglaubt.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "app" / "research" / "dependency_preflight.py"
    ).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

    for forbidden in (
        "compute_forward_return_bps",
        "compute_next_open_forward_return_bps",
        "decisions_to_trades",
        "net_bps",
    ):
        assert forbidden not in code, f"{forbidden} gehoert nicht in einen Pre-T0-Preflight"


def test_cluster_stats_carry_their_own_decomposition() -> None:
    """Direktive 2026-08-08: kein Aggregat ohne Zerlegung — auch nicht hier.

    Eine Reifeschranke, die auf einer Rate beruht, die zu einem Drittel von
    einem Symbol kommt, ist eine andere Zahl als eine breit getragene. Der
    Ratchet (#682/#684/#687) hat das an dieser Funktion auch prompt eingefordert.
    """
    signals = [
        *[Signal("BTC", _T0 + k * 100 * _HOUR_MS) for k in range(8)],
        Signal("ETH", _T0 + 5000 * _HOUR_MS),
        Signal("SOL", _T0 + 9000 * _HOUR_MS),
    ]

    stats = summarize_clusters(signals, timeframe_ms=_HOUR_MS, horizon=4)

    assert stats.per_symbol_signals == {"BTC": 8, "ETH": 1, "SOL": 1}
    assert stats.leave_one_out_top_symbol.symbol == "BTC"
    assert stats.leave_one_out_top_symbol.n_signals == 2
    assert stats.leave_one_out_top_symbol.n_clusters == 2


def test_leave_one_out_is_empty_when_a_single_symbol_carries_everything() -> None:
    """Der Extremfall muss eine Zahl liefern, keinen Absturz."""
    stats = summarize_clusters(
        [Signal("BTC", _T0 + k * 100 * _HOUR_MS) for k in range(5)],
        timeframe_ms=_HOUR_MS,
        horizon=4,
    )

    assert stats.top_symbol_share == pytest.approx(1.0)
    assert stats.leave_one_out_top_symbol.n_signals == 0
    assert stats.leave_one_out_top_symbol.n_clusters == 0
