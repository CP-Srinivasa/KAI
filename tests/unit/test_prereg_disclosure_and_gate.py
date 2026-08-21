r"""Offenlegung, Sensitivitaet, Robustheit — und der Torwaechter davor.

Drei Dinge, die eine Praeregistrierung erst pruefbar machen:

**``n_valid`` ist nicht die Zahl der Feuerungen.** Eine Feuerung ohne
auswertbares Label ist nicht "kein Signal" — die Regel hat gefeuert, nur war das
Ergebnis nicht beobachtbar. Wer beides zusammenwirft, meldet ein ``n_valid``,
das eine andere Groesse ist als die, die es zu sein vorgibt, und
``n_valid_min = 100`` waere dann eine andere Huerde als die versiegelte.

**Sensitivitaet ist kein Alternativ-Gate.** Der Mittelwert bei 25 oder 30 bps
Kosten steht im Bericht, damit man ihn SIEHT — nicht, damit hinterher jemand
sagt "bei 20 bps hat es nicht gereicht, bei einem anderen Kostenmodell schon".
Die versiegelten Kosten entscheiden.

**Man kann den p-Wert nicht aus Versehen bekommen.** ``run_confirmatory``
verlangt eine ``EVALUATE``-Entscheidung des Fenster-Gates und bricht sonst ab.
Ein p-Wert, den niemand haette sehen duerfen, laesst sich nicht zurueckziehen.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.analysis.features.feature_matrix import FeatureRow
from app.research.prereg_window import (
    ACTION_EVALUATE,
    ACTION_WAIT,
    MaturityCounts,
    PrematureEvaluationError,
    WindowDecision,
)
from app.research.primary_confirmatory import (
    DIAGNOSTIC_STATUS,
    VERDICT_NOT_MET,
    VERDICT_PASS,
    SymbolPanel,
    evaluate_primary,
    maturity_counts,
    run_confirmatory,
)

_HOUR_MS = 3_600_000
_ANCHOR_MS = 1_770_000_000_000


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _row(bar_index: int) -> FeatureRow:
    """Eine Zeile, auf der die Regel feuert (Crossover + Spike)."""
    return FeatureRow(
        timestamp_utc=_iso(_ANCHOR_MS + bar_index * _HOUR_MS),
        close=100.0,
        log_return=None,
        rsi_14=31.0,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
        rsi_14_prev=28.0,
        volume_z_20=3.0,
    )


def _always_long(_row_in: FeatureRow) -> int:
    return 1


def _panel(symbol: str, *, n: int, spacing_bars: int, missing: int = 0) -> SymbolPanel:
    rows = [_row(i * spacing_bars) for i in range(n)]
    labels: list[float | None] = [50.0] * n
    for i in range(missing):
        labels[i] = None
    return SymbolPanel(symbol=symbol, rows=rows, labels=labels)


def _evaluate(panels, **overrides):
    kwargs = {
        "hypothesis": "rsi_reentry_volume_confirmed",
        "universe_sha256": "d28e10d5",
        "round_trip_cost_bps": 0.0,
        "timeframe_ms": _HOUR_MS,
        "horizon": 4,
        "n_min": 1,
        "cluster_min": 1,
    }
    kwargs.update(overrides)
    return evaluate_primary(panels, _always_long, **kwargs)


# ── Offenlegung ─────────────────────────────────────────────────────────────


def test_a_fire_without_a_label_is_not_a_missing_signal() -> None:
    """Nichtbeobachtbarkeit ist kein Nullsignal — und zaehlt nicht zu ``n_valid``."""
    result = _evaluate([_panel("BTC", n=30, spacing_bars=40, missing=7)])

    assert result.disclosure is not None
    assert result.disclosure.raw_fires == 30
    assert result.disclosure.n_valid == 23
    assert result.disclosure.data_unavailable_count == 7
    assert result.summary.n == 23, "die nicht auswertbaren zaehlen nicht mit"


def test_unavailable_signals_are_counted_not_dropped() -> None:
    """Der Unterschied zwischen "gab es nicht" und "war nicht messbar"."""
    result = _evaluate(
        [
            _panel("BTC", n=10, spacing_bars=40),
            _panel("ETH", n=5, spacing_bars=40, missing=5),
        ]
    )

    assert result.disclosure is not None
    assert result.disclosure.raw_fires == 15
    assert result.disclosure.n_valid == 10
    assert result.disclosure.data_unavailable_count == 5
    assert result.disclosure.symbols_with_valid_signals == 1


# ── Kosten-Sensitivitaet ────────────────────────────────────────────────────


def test_cost_sensitivity_is_exact_and_non_gating() -> None:
    """``net = gross - cost``, die Sensitivitaet ist deshalb exakt statt geschaetzt."""
    result = _evaluate(
        [_panel("BTC", n=20, spacing_bars=40)],  # gross = 50 bps
        round_trip_cost_bps=20.0,
        economic_floor_bps=5.0,
        sensitivity_cost_bps=(20.0, 25.0, 30.0),
    )

    means = {s.round_trip_cost_bps: s.mean_net_bps for s in result.cost_sensitivity}
    margins = {s.round_trip_cost_bps: s.margin_above_floor_bps for s in result.cost_sensitivity}

    assert means == pytest.approx({20.0: 30.0, 25.0: 25.0, 30.0: 20.0})
    assert margins == pytest.approx({20.0: 25.0, 25.0: 20.0, 30.0: 15.0})
    assert all(s.status == DIAGNOSTIC_STATUS for s in result.cost_sensitivity)


def test_sensitivity_never_changes_the_verdict() -> None:
    """Ein guenstigeres Kostenmodell darf das versiegelte Urteil nicht beruehren."""
    panels = [_panel(sym, n=30, spacing_bars=40) for sym in ("BTC", "ETH", "SOL")]

    plain = _evaluate(panels, round_trip_cost_bps=20.0, economic_floor_bps=5.0)
    with_sensitivity = _evaluate(
        panels,
        round_trip_cost_bps=20.0,
        economic_floor_bps=5.0,
        sensitivity_cost_bps=(5.0, 10.0, 15.0),
    )

    assert with_sensitivity.verdict == plain.verdict
    assert with_sensitivity.summary.mean_bps == pytest.approx(plain.summary.mean_bps)
    assert with_sensitivity.round_trip_cost_bps == 20.0


# ── Robustheit ──────────────────────────────────────────────────────────────


def test_robustness_shows_what_a_single_p_value_hides() -> None:
    """Traegt das Ergebnis ein Prozess — oder eine Stunde und ein Asset?"""
    panels = [_panel(sym, n=30, spacing_bars=40) for sym in ("BTC", "ETH", "SOL")]

    result = _evaluate(panels)

    labels = {d.label for d in result.robustness}
    assert labels == {"result_without_largest_cluster", "result_without_top_symbol"}
    assert all(d.status == DIAGNOSTIC_STATUS for d in result.robustness)
    for diagnostic in result.robustness:
        assert diagnostic.n < result.summary.n, "es muss wirklich etwas entfernt worden sein"


def test_robustness_names_what_it_removed() -> None:
    result = _evaluate([_panel("BTC", n=40, spacing_bars=40), _panel("ETH", n=5, spacing_bars=400)])

    by_label = {d.label: d for d in result.robustness}

    assert "BTC" in (by_label["result_without_top_symbol"].without_unit or "")


def test_robustness_is_reported_but_the_verdict_stands() -> None:
    """Es darf sichtbar machen, aber nicht rueckwirkend umetikettieren."""
    panels = [_panel(sym, n=30, spacing_bars=40) for sym in ("BTC", "ETH", "SOL")]

    result = _evaluate(panels, economic_floor_bps=5.0)

    assert result.verdict in {VERDICT_PASS, VERDICT_NOT_MET}
    assert result.robustness, "die Diagnose existiert"
    assert all(d.status == DIAGNOSTIC_STATUS for d in result.robustness)


# ── Der Torwaechter ─────────────────────────────────────────────────────────


def _decision(action: str) -> WindowDecision:
    return WindowDecision(
        action=action,
        checkpoint="PRE_T1" if action == ACTION_WAIT else "T1",
        mature=True,
        counts=MaturityCounts(n_valid=146, n_clusters=75),
    )


def test_no_p_value_before_t1_even_with_a_full_sample() -> None:
    """Der eigentliche Schutz gegen optional stopping, Ende zu Ende."""
    with pytest.raises(PrematureEvaluationError):
        run_confirmatory(
            _decision(ACTION_WAIT),
            [_panel("BTC", n=30, spacing_bars=40)],
            _always_long,
            hypothesis="x",
            universe_sha256="abc",
            round_trip_cost_bps=20.0,
            timeframe_ms=_HOUR_MS,
            horizon=4,
            n_min=1,
            cluster_min=1,
        )


def test_the_gate_lets_the_real_checkpoint_through() -> None:
    """Gegenprobe — ein Torwaechter, der nie oeffnet, ist nur eine Mauer."""
    result = run_confirmatory(
        _decision(ACTION_EVALUATE),
        [_panel("BTC", n=30, spacing_bars=40)],
        _always_long,
        hypothesis="x",
        universe_sha256="abc",
        round_trip_cost_bps=20.0,
        timeframe_ms=_HOUR_MS,
        horizon=4,
        n_min=1,
        cluster_min=1,
    )

    assert result.verdict in {VERDICT_PASS, VERDICT_NOT_MET}


def test_maturity_counts_agree_with_the_later_disclosure() -> None:
    """Vor T1 darf man zaehlen — und die Zahlen muessen spaeter dieselben sein.

    Waeren sie es nicht, haette der Operator vor T1 etwas anderes beobachtet als
    das, was das Verdikt am Ende benutzt.
    """
    panels = [_panel("BTC", n=30, spacing_bars=40, missing=7)]

    counts = maturity_counts(
        panels, _always_long, round_trip_cost_bps=20.0, timeframe_ms=_HOUR_MS, horizon=4
    )
    result = _evaluate(panels, round_trip_cost_bps=20.0)

    assert result.disclosure is not None
    assert counts.n_valid == result.disclosure.n_valid
    assert counts.raw_fires == result.disclosure.raw_fires
    assert counts.data_unavailable_count == result.disclosure.data_unavailable_count
    assert counts.n_clusters == result.summary.n_clusters


# ── Kosten duerfen nicht aus der Konfiguration kommen ───────────────────────


def test_the_prereg_path_never_reads_costs_from_configuration() -> None:
    """Die versiegelten 20 bps duerfen nicht zur Verdikt-Zeit aus Config kommen.

    Sonst koennte sich in 90 Tagen eine Einstellung aendern und dieselben Trades
    wuerden an einer anderen Huerde gemessen — eine Kriterienaenderung, die
    niemand als solche bemerkt haette.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app" / "research"
    for name in ("prereg_candidate.py", "prereg_window.py", "primary_confirmatory.py"):
        source = (root / name).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        for forbidden in ("get_settings", "CostModel", "_resolve_cost_bps", "settings."):
            assert forbidden not in code, f"{name} liest Kosten/Config zur Laufzeit ({forbidden})"
