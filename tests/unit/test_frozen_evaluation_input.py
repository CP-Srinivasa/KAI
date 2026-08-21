r"""Der aufgeloeste Vertrag und seine Veroeffentlichung.

Die gefaehrlichste Variante eines Konfirmationstests ist der, dessen Parameter
der Aufrufer mitbringt. ``evaluate_primary`` nimmt Kosten, Horizont, Alpha und
sogar den Universe-Hash bis heute als freie Schluesselwortargumente entgegen;
``run_confirmatory`` reicht sie per ``**kwargs`` durch. Wer den Test startet,
kann damit ohne jede Spur andere Werte einsetzen als die versiegelten — und das
Ergebnis sieht danach genauso aus wie ein ehrliches.

Deshalb kommt der Vertrag hier ausschliesslich aus Candidate und Activation,
und beide werden nachgerechnet statt geglaubt.

Die Veroeffentlichung ist der zweite Teil: eine Auswertungs-Eingabe, die nur im
Arbeitsspeicher existiert, ueberlebt keinen Absturz. Sie muss auf der Platte
liegen, unveraenderlich sein, und ein zweiter Lauf mit anderem Inhalt fuer
dieselbe Identitaet muss auffliegen statt zu ueberschreiben.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.analysis.features.feature_matrix import FeatureRow
from app.research.frozen_evaluation import (
    FrozenSymbolPanel,
    PublishConflictError,
    build_frozen_dataset,
    freeze_evaluation_input,
    load_frozen_input,
    publish_frozen_input,
    resolve_contract,
)
from app.research.prereg_candidate import (
    activate,
    build_rsi_reentry_volume_candidate,
)
from app.research.prereg_window import MaturityCounts

_UNIVERSE_SHA = "d" * 64
_CODE_SHA = "9d1502dc7c6f4f2b1a3e5c7d9b0f2a4c6e8d0b2f"
_EVALUATOR_SHA = "a" * 64
_T0 = "2026-09-01T00:00:00+00:00"
_SYMBOLS = ("BTC/USDT", "ETH/USDT")


def _candidate():
    # n_symbols wird bewusst auf die Testpopulation gesetzt: der Vertrag
    # verlangt Uebereinstimmung, nicht eine bestimmte Zahl.
    return replace(build_rsi_reentry_volume_candidate(_UNIVERSE_SHA, 34), n_symbols=2)


def _activation(candidate=None):
    return activate(
        candidate or _candidate(),
        t0_utc=_T0,
        research_code_sha=_CODE_SHA,
        evaluator_sha256=_EVALUATOR_SHA,
        operator_approved=True,
    )


def _row(ts: str = "2026-09-02T00:00:00+00:00") -> FeatureRow:
    return FeatureRow(
        timestamp_utc=ts,
        close=100.0,
        log_return=0.001,
        rsi_14=30.0,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
    )


def _dataset(activation, *, checkpoint="T1", cutoff=None, symbols=_SYMBOLS):
    return build_frozen_dataset(
        canonical_symbols=symbols,
        panels=tuple(
            FrozenSymbolPanel(
                symbol=s,
                rows=(_row(),),
                labels=(12.5,),
                label_exit_utc=("2026-09-02T04:00:00+00:00",),
            )
            for s in symbols
        ),
        universe_sha256=activation.universe_sha256,
        t0_utc=activation.t0_utc,
        checkpoint=checkpoint,
        checkpoint_cutoff_utc=cutoff or activation.t1_utc,
    )


_COUNTS = MaturityCounts(
    n_valid=120,
    n_clusters=61,
    raw_fires=300,
    label_capable_fires=140,
    data_unavailable_count=7,
    symbols_with_valid_signals=2,
)


def _freeze(**overrides):
    candidate = overrides.pop("candidate", None) or _candidate()
    activation = overrides.pop("activation", None) or _activation(candidate)
    dataset = overrides.pop("dataset", None) or _dataset(activation)
    return freeze_evaluation_input(
        candidate=candidate,
        activation=activation,
        dataset=dataset,
        counts=overrides.pop("counts", None) or _COUNTS,
        **overrides,
    )


# ── Der aufgeloeste Vertrag ─────────────────────────────────────────────────


def test_the_contract_comes_from_the_candidate_not_from_the_caller() -> None:
    candidate = _candidate()

    contract = resolve_contract(candidate, _activation(candidate))

    assert contract.alpha == candidate.alpha
    assert contract.round_trip_cost_bps == candidate.round_trip_cost_bps
    assert contract.economic_floor_bps == candidate.economic_floor_bps
    assert contract.horizon == candidate.horizon
    assert contract.timeframe == candidate.timeframe
    assert contract.n_valid_min == candidate.n_valid_min
    assert contract.cluster_min == candidate.cluster_min
    assert contract.hypothesis == candidate.hypothesis


def test_a_candidate_that_does_not_hash_to_the_activation_is_refused() -> None:
    """Genau der Fall, den ein Verdikt niemals ueberleben darf: die Aktivierung
    verweist auf einen Candidate, und ausgewertet wird ein anderer."""
    candidate = _candidate()
    activation = _activation(candidate)
    tampered = replace(candidate, alpha=0.10)

    with pytest.raises(ValueError, match="candidate_sha256"):
        resolve_contract(tampered, activation)


def test_a_universe_mismatch_is_refused() -> None:
    candidate = _candidate()
    activation = replace(_activation(candidate), universe_sha256="f" * 64)

    with pytest.raises(ValueError, match="universe_sha256"):
        resolve_contract(candidate, activation)


# ── Die eingefrorene Eingabe ────────────────────────────────────────────────


def test_freezing_binds_the_whole_chain() -> None:
    frozen = _freeze()

    assert len(frozen.evaluation_input_sha256) == 64
    assert frozen.dataset_sha256 == _dataset(_activation()).dataset_sha256
    assert frozen.research_code_sha == _CODE_SHA
    assert frozen.evaluator_sha256 == _EVALUATOR_SHA
    assert frozen.maturity_counts["n_valid"] == 120
    assert frozen.maturity_counts["n_clusters"] == 61


def test_the_input_hash_changes_with_the_dataset() -> None:
    activation = _activation()
    other_rows = build_frozen_dataset(
        canonical_symbols=_SYMBOLS,
        panels=tuple(
            FrozenSymbolPanel(
                symbol=s,
                rows=(_row("2026-09-03T00:00:00+00:00"),),
                labels=(12.5,),
                label_exit_utc=("2026-09-03T04:00:00+00:00",),
            )
            for s in _SYMBOLS
        ),
        universe_sha256=activation.universe_sha256,
        t0_utc=activation.t0_utc,
        checkpoint="T1",
        checkpoint_cutoff_utc=activation.t1_utc,
    )

    assert (
        _freeze(activation=activation, dataset=other_rows).evaluation_input_sha256
        != _freeze(activation=activation).evaluation_input_sha256
    )


def test_the_input_hash_changes_with_the_maturity_counts() -> None:
    """Dieselbe Auswertung auf anderer Reifegrundlage ist eine andere Auswertung."""
    other = replace(_COUNTS, n_valid=121)

    assert _freeze(counts=other).evaluation_input_sha256 != _freeze().evaluation_input_sha256


def test_a_checkpoint_cutoff_that_is_not_t1_or_t2_is_refused() -> None:
    """Ein frei gewaehlter Stichtag waere ein frei gewaehltes Ergebnis."""
    activation = _activation()
    dataset = _dataset(activation, cutoff="2026-12-15T00:00:00+00:00")

    with pytest.raises(ValueError, match="cutoff"):
        _freeze(activation=activation, dataset=dataset)


def test_the_t2_checkpoint_must_use_t2(tmp_path: Path) -> None:
    activation = _activation()
    dataset = _dataset(activation, checkpoint="T2", cutoff=activation.t2_utc)

    frozen = _freeze(activation=activation, dataset=dataset)

    assert frozen.checkpoint == "T2"
    assert frozen.checkpoint_cutoff_utc == activation.t2_utc


def test_a_dataset_from_a_different_t0_is_refused() -> None:
    activation = _activation()
    foreign = build_frozen_dataset(
        canonical_symbols=_SYMBOLS,
        panels=tuple(
            FrozenSymbolPanel(
                symbol=s,
                rows=(_row(),),
                labels=(12.5,),
                label_exit_utc=("2026-09-02T04:00:00+00:00",),
            )
            for s in _SYMBOLS
        ),
        universe_sha256=activation.universe_sha256,
        t0_utc="2026-08-01T00:00:00+00:00",
        checkpoint="T1",
        checkpoint_cutoff_utc=activation.t1_utc,
    )

    with pytest.raises(ValueError, match="t0_utc"):
        _freeze(activation=activation, dataset=foreign)


def test_a_symbol_count_that_differs_from_the_candidate_is_refused() -> None:
    candidate = replace(_candidate(), n_symbols=34)
    activation = _activation(candidate)

    with pytest.raises(ValueError, match="n_symbols"):
        _freeze(candidate=candidate, activation=activation, dataset=_dataset(activation))


# ── Dauerhafte Veroeffentlichung ────────────────────────────────────────────


def test_publishing_writes_the_exact_bytes_and_reports_creation(tmp_path: Path) -> None:
    frozen = _freeze()

    result = publish_frozen_input(tmp_path, frozen)

    assert result.created is True
    assert result.path.read_bytes() == frozen.canonical_bytes
    assert (
        load_frozen_input(
            tmp_path,
            activation_sha256=frozen.activation_sha256,
            checkpoint=frozen.checkpoint,
        )
        == frozen.canonical_bytes
    )


def test_publishing_the_same_input_twice_is_idempotent(tmp_path: Path) -> None:
    frozen = _freeze()
    first = publish_frozen_input(tmp_path, frozen)

    second = publish_frozen_input(tmp_path, frozen)

    assert first.created is True
    assert second.created is False
    assert second.evaluation_input_sha256 == first.evaluation_input_sha256
    assert second.path.read_bytes() == frozen.canonical_bytes


def test_a_different_input_for_the_same_identity_is_a_conflict(tmp_path: Path) -> None:
    """Derselbe Checkpoint derselben Aktivierung, andere Bytes: das ist der
    Moment, in dem eine Auswertung heimlich ausgetauscht wuerde."""
    frozen = _freeze()
    publish_frozen_input(tmp_path, frozen)
    other = _freeze(counts=replace(_COUNTS, n_valid=999))

    with pytest.raises(PublishConflictError, match="conflict"):
        publish_frozen_input(tmp_path, other)

    assert (
        load_frozen_input(
            tmp_path,
            activation_sha256=frozen.activation_sha256,
            checkpoint=frozen.checkpoint,
        )
        == frozen.canonical_bytes
    )


def test_publishing_leaves_no_temporary_files_behind(tmp_path: Path) -> None:
    """Ein liegengebliebener Rest waere ein zweiter, halber Wahrheitsstand."""
    publish_frozen_input(tmp_path, _freeze())

    assert len(list(tmp_path.iterdir())) == 1


def test_loading_an_absent_input_returns_none(tmp_path: Path) -> None:
    assert load_frozen_input(tmp_path, activation_sha256="b" * 64, checkpoint="T1") is None
