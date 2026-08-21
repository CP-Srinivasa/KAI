r"""Der Konfirmationstest darf nichts mehr selbst mitbringen.

``evaluate_primary`` nimmt Kosten, Horizont, Alpha, die oekonomische Schranke
und sogar den Universe-Hash als freie Schluesselwortargumente entgegen, und
``run_confirmatory`` reicht sie per ``**kwargs`` durch. Wer den Test startet,
kann damit andere Werte einsetzen als die versiegelten, ohne dass irgendwo eine
Spur bleibt — das Ergebnis sieht aus wie ein ehrliches.

``evaluate_frozen`` schliesst diesen Weg: es gibt genau zwei Eingaben, den
eingefrorenen Input und das dazugehoerige Datenset, und beide tragen einen
Hash. Alles andere wird daraus gelesen.
"""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from app.analysis.features.feature_matrix import FeatureRow
from app.research.frozen_evaluation import (
    FrozenSymbolPanel,
    build_frozen_dataset,
    evaluate_frozen,
    freeze_evaluation_input,
    timeframe_to_ms,
)
from app.research.prereg_candidate import activate, build_rsi_reentry_volume_candidate
from app.research.prereg_window import MaturityCounts

_UNIVERSE_SHA = "d" * 64
_CODE_SHA = "9d1502dc7c6f4f2b1a3e5c7d9b0f2a4c6e8d0b2f"
_EVALUATOR_SHA = "a" * 64
_T0 = "2026-09-01T00:00:00+00:00"
_SYMBOLS = ("BTC/USDT", "ETH/USDT")
_COUNTS = MaturityCounts(
    n_valid=4,
    n_clusters=2,
    raw_fires=4,
    label_capable_fires=4,
    data_unavailable_count=0,
    symbols_with_valid_signals=2,
)


def _candidate():
    return replace(build_rsi_reentry_volume_candidate(_UNIVERSE_SHA, 34), n_symbols=2)


def _activation(candidate):
    return activate(
        candidate,
        t0_utc=_T0,
        research_code_sha=_CODE_SHA,
        evaluator_sha256=_EVALUATOR_SHA,
        operator_approved=True,
    )


def _row(ts: str, rsi: float) -> FeatureRow:
    return FeatureRow(
        timestamp_utc=ts,
        close=100.0,
        log_return=0.001,
        rsi_14=rsi,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
    )


def _dataset(activation):
    return build_frozen_dataset(
        canonical_symbols=_SYMBOLS,
        panels=tuple(
            FrozenSymbolPanel(
                symbol=s,
                rows=(
                    _row("2026-09-02T00:00:00+00:00", 25.0),
                    _row("2026-09-02T01:00:00+00:00", 70.0),
                ),
                labels=(40.0, -10.0),
                label_exit_utc=("2026-09-02T04:00:00+00:00", "2026-09-02T05:00:00+00:00"),
            )
            for s in _SYMBOLS
        ),
        universe_sha256=activation.universe_sha256,
        t0_utc=activation.t0_utc,
        checkpoint="T1",
        checkpoint_cutoff_utc=activation.t1_utc,
    )


def _frozen():
    candidate = _candidate()
    activation = _activation(candidate)
    dataset = _dataset(activation)
    return (
        freeze_evaluation_input(
            candidate=candidate, activation=activation, dataset=dataset, counts=_COUNTS
        ),
        dataset,
    )


def _decide(row: FeatureRow) -> int:
    return 1 if (row.rsi_14 or 100.0) < 30.0 else 0


# ── Die Signatur ist die halbe Garantie ─────────────────────────────────────


def test_the_bound_entry_point_accepts_no_free_parameters() -> None:
    """Kein ``**kwargs``, kein ``alpha=``, kein ``round_trip_cost_bps=``.

    Ein Durchreicher waere derselbe offene Weg unter neuem Namen.
    """
    parameters = inspect.signature(evaluate_frozen).parameters

    assert set(parameters) == {"frozen_input", "dataset", "decide"}
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())


# ── Die Werte stammen aus dem Vertrag ───────────────────────────────────────


def test_the_sealed_contract_values_reach_the_test(monkeypatch: pytest.MonkeyPatch) -> None:
    frozen, dataset = _frozen()
    seen: dict[str, object] = {}

    import app.research.frozen_evaluation as module

    def _spy(panels, decide, **kwargs):
        seen.update(kwargs)
        seen["n_panels"] = len(panels)
        return "result"

    monkeypatch.setattr(module, "evaluate_primary", _spy)

    assert evaluate_frozen(frozen_input=frozen, dataset=dataset, decide=_decide) == "result"
    assert seen["alpha"] == frozen.contract.alpha
    assert seen["round_trip_cost_bps"] == frozen.contract.round_trip_cost_bps
    assert seen["economic_floor_bps"] == frozen.contract.economic_floor_bps
    assert seen["horizon"] == frozen.contract.horizon
    assert seen["n_min"] == frozen.contract.n_valid_min
    assert seen["cluster_min"] == frozen.contract.cluster_min
    assert seen["universe_sha256"] == frozen.universe_sha256
    assert seen["timeframe_ms"] == timeframe_to_ms(frozen.contract.timeframe)
    assert seen["n_panels"] == len(_SYMBOLS)


def test_a_dataset_that_does_not_match_the_frozen_input_is_refused() -> None:
    """Das Datenset ist Teil der Identitaet — ein anderes ist eine andere Auswertung."""
    frozen, _ = _frozen()
    candidate = _candidate()
    activation = _activation(candidate)
    other = build_frozen_dataset(
        canonical_symbols=_SYMBOLS,
        panels=tuple(
            FrozenSymbolPanel(
                symbol=s,
                rows=(_row("2026-09-03T00:00:00+00:00", 25.0),),
                labels=(40.0,),
                label_exit_utc=("2026-09-03T04:00:00+00:00",),
            )
            for s in _SYMBOLS
        ),
        universe_sha256=activation.universe_sha256,
        t0_utc=activation.t0_utc,
        checkpoint="T1",
        checkpoint_cutoff_utc=activation.t1_utc,
    )

    with pytest.raises(ValueError, match="dataset_sha256"):
        evaluate_frozen(frozen_input=frozen, dataset=other, decide=_decide)


def test_the_evaluation_really_runs_end_to_end() -> None:
    """Nicht nur die Bindung, auch der Durchlauf: ein Verdikt entsteht."""
    frozen, dataset = _frozen()

    result = evaluate_frozen(frozen_input=frozen, dataset=dataset, decide=_decide)

    assert result.universe_sha256 == frozen.universe_sha256
    assert isinstance(result.verdict, str) and result.verdict


# ── Timeframe ───────────────────────────────────────────────────────────────


def test_known_timeframes_convert() -> None:
    assert timeframe_to_ms("1h") == 3_600_000
    assert timeframe_to_ms("15m") == 900_000
    assert timeframe_to_ms("1d") == 86_400_000


def test_an_unknown_timeframe_is_refused_not_guessed() -> None:
    """Ein geratener Takt verschiebt die Cluster und damit die Freiheitsgrade."""
    with pytest.raises(ValueError, match="timeframe"):
        timeframe_to_ms("1 hour")
