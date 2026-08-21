r"""Aus einer versiegelten Absicht eine EINMALIGE, reproduzierbare Auswertung machen.

Bis hierher war die Kette an einer Stelle offen: ``EVALUATE`` stand im Journal,
das Verdikt fehlte, und der Checkpoint galt als ``CLOSED``. Ein Absturz zwischen
Entschluss und p-Wert haette das Ergebnis also verschluckt — das Experiment waere
beendet gewesen, ohne je eines gehabt zu haben.

Die zweite, groessere Luecke lag daneben: ``run_confirmatory`` nahm **beliebige**
Panels, eine **freie** Hypothese und einen **mitgelieferten** ``universe_sha256``
entgegen und schrieb Letzteren unbesehen ins Ergebnis. Ein korrekter Hash neben
33 Symbolen waere nicht aufgefallen.

Beides schliesst der Vertrag hier::

    CHECKPOINT_DECIDED -> EVALUATION_INPUT_FROZEN -> EVALUATION_RUNNING
                       -> VERDICT_RECORDED        -> CLOSED

mit der Reihenfolge als eigentlichem Gewinn: das Artefakt liegt auf der Platte,
BEVOR ``EVALUATE`` journalisiert wird. Damit gilt "Journal sagt EVALUATE ⇒ das
Artefakt existiert" — und nicht umgekehrt.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.analysis.features.feature_matrix import FeatureRow
from app.research.frozen_dataset import (
    FrozenDatasetError,
    FrozenRow,
    build_frozen_dataset,
    canonical_bytes,
    dataset_sha256,
    dataset_to_dict,
)
from app.research.frozen_input import (
    FrozenInputError,
    build_frozen_input,
    evaluation_input_sha256,
    read_frozen_artifact,
    write_frozen_artifact,
)
from app.research.prereg_candidate import (
    activate,
    build_rsi_reentry_volume_candidate,
    candidate_sha256,
)
from app.research.prereg_evaluation import (
    SealedEvaluationError,
    decide_and_freeze,
    load_verdicts,
    resume_evaluation_input_sha256,
    run_sealed_evaluation,
)
from app.research.prereg_window import (
    ACTION_CLOSED,
    ACTION_EVALUATE,
    ACTION_EXTEND_TO_T2,
    ACTION_RESUME_EVALUATION,
)

_UNIVERSE_SHA = "f" * 64
_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
_CODE_SHA = "c" * 40
_EVAL_SHA = "e" * 64

_T0 = "2026-09-01T00:00:00+00:00"
_T1 = "2026-11-30T00:00:00+00:00"
_T2 = "2027-02-28T00:00:00+00:00"


def _candidate(**overrides):
    """Ein Test-Candidate: echte Struktur, aber Schranken, die ein 6-Zeilen-Sample erreicht."""
    base = build_rsi_reentry_volume_candidate(_UNIVERSE_SHA, len(_SYMBOLS))
    values = {"n_valid_min": 1, "cluster_min": 1}
    values.update(overrides)
    return replace(base, **values)


def _activation(candidate=None):
    return activate(
        candidate or _candidate(),
        t0_utc=_T0,
        research_code_sha=_CODE_SHA,
        evaluator_sha256=_EVAL_SHA,
        operator_approved=True,
    )


def _feature_row(hour: int) -> FeatureRow:
    """Eine Zeile, auf der ``rsi_reentry_volume_confirmed`` feuert."""
    return FeatureRow(
        timestamp_utc=f"2026-10-{1 + hour // 24:02d}T{hour % 24:02d}:00:00+00:00",
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


def _frozen_row(hour: int, *, label: float | None = 50.0) -> FrozenRow:
    row = _feature_row(hour)
    exit_hour = hour + 4
    return FrozenRow(
        signal_timestamp_utc=row.timestamp_utc,
        label_exit_utc=f"2026-10-{1 + exit_hour // 24:02d}T{exit_hour % 24:02d}:00:00+00:00",
        features={k: v for k, v in asdict(row).items() if k != "timestamp_utc"},
        label_bps=label,
    )


def _rows(counts: dict[str, int], *, missing: int = 0) -> dict[str, list[FrozenRow]]:
    out: dict[str, list[FrozenRow]] = {}
    hour = 0
    for symbol, n in counts.items():
        rows = []
        for i in range(n):
            rows.append(_frozen_row(hour, label=None if i < missing else 50.0 + i * 3.0))
            hour += 40
        out[symbol] = rows
    return out


def _dataset(counts: dict[str, int] | None = None, *, checkpoint: str = "T1", **kw):
    return build_frozen_dataset(
        checkpoint=checkpoint,
        t0_utc=kw.get("t0_utc", _T0),
        cutoff_utc=kw.get("cutoff_utc", _T1 if checkpoint == "T1" else _T2),
        sealed_symbols=kw.get("sealed_symbols", _SYMBOLS),
        rows_by_symbol=_rows(counts or {"BTC/USDT": 3, "ETH/USDT": 2, "SOL/USDT": 1}),
    )


# ── A. Der eingefrorene Datenschnitt ────────────────────────────────────────


def test_a_symbol_without_signals_stays_a_member() -> None:
    """``DATA_UNAVAILABLE`` ist NICHT ``asset removed``.

    Wer ein stummes Symbol weglaesst, veraendert still die Population — und der
    naechste Leser haelt 33 fuer 34.
    """
    dataset = _dataset({"BTC/USDT": 2})

    assert dataset.symbols == _SYMBOLS
    assert [p.symbol for p in dataset.panels] == list(_SYMBOLS)
    assert len(dataset.panels[2].rows) == 0


def test_a_symbol_outside_the_sealed_universe_is_refused() -> None:
    with pytest.raises(FrozenDatasetError, match="ausserhalb des versiegelten"):
        build_frozen_dataset(
            checkpoint="T1",
            t0_utc=_T0,
            cutoff_utc=_T1,
            sealed_symbols=_SYMBOLS,
            rows_by_symbol=_rows({"DOGE/USDT": 1}),
        )


def test_none_and_zero_are_different_things() -> None:
    """``0.0`` ist eine Beobachtung, ``None`` ist ihre Abwesenheit.

    Wer beides zusammenwirft, faelscht den Mittelwert nach unten und
    ``n_valid`` nach oben.
    """
    dataset = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS,
        rows_by_symbol={"BTC/USDT": [_frozen_row(0, label=0.0), _frozen_row(40, label=None)]},
    )

    labels = [row.label_bps for row in dataset.panels[0].rows]
    assert labels == [0.0, None]
    payload = dataset_to_dict(dataset)
    assert payload["panels"][0]["rows"][0]["label_bps"] == 0.0
    assert payload["panels"][0]["rows"][1]["label_bps"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_cannot_be_frozen(bad: float) -> None:
    """``NaN`` passiert jeden ``is not None``-Guard und propagiert lautlos."""
    with pytest.raises(FrozenDatasetError, match="nicht endlich"):
        build_frozen_dataset(
            checkpoint="T1",
            t0_utc=_T0,
            cutoff_utc=_T1,
            sealed_symbols=_SYMBOLS,
            rows_by_symbol={"BTC/USDT": [_frozen_row(0, label=bad)]},
        )


def test_canonical_bytes_refuse_nan() -> None:
    """``json.dumps`` schriebe sonst ``NaN`` — kein gueltiges JSON, still fehlerhaft."""
    with pytest.raises(ValueError, match="Out of range"):
        canonical_bytes({"x": float("nan")})


def test_a_naive_timestamp_is_refused() -> None:
    row = _frozen_row(0)
    naive = replace(row, signal_timestamp_utc="2026-10-01T00:00:00")

    with pytest.raises(FrozenDatasetError, match="zeitzonenlos"):
        build_frozen_dataset(
            checkpoint="T1",
            t0_utc=_T0,
            cutoff_utc=_T1,
            sealed_symbols=_SYMBOLS,
            rows_by_symbol={"BTC/USDT": [naive]},
        )


def test_the_window_is_about_observability_not_the_signal_time() -> None:
    """Ein Label zaehlt nur, wenn es bis zum Checkpoint VOLLSTAENDIG vorlag.

    Ein Signal kurz vor dem Cutoff, dessen Ausstieg danach liegt, war am
    Checkpoint noch nicht beobachtbar — es gehoert nicht hinein.
    """
    inside = FrozenRow(
        signal_timestamp_utc="2026-11-29T00:00:00+00:00",
        label_exit_utc="2026-11-29T04:00:00+00:00",
        features=_frozen_row(0).features,
        label_bps=10.0,
    )
    exits_after_cutoff = FrozenRow(
        signal_timestamp_utc="2026-11-29T23:00:00+00:00",
        label_exit_utc="2026-11-30T03:00:00+00:00",
        features=_frozen_row(0).features,
        label_bps=10.0,
    )
    before_t0 = FrozenRow(
        signal_timestamp_utc="2026-08-30T00:00:00+00:00",
        label_exit_utc="2026-08-30T04:00:00+00:00",
        features=_frozen_row(0).features,
        label_bps=10.0,
    )

    dataset = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS,
        rows_by_symbol={"BTC/USDT": [inside, exits_after_cutoff, before_t0]},
    )

    kept = [row.signal_timestamp_utc for row in dataset.panels[0].rows]
    assert kept == ["2026-11-29T00:00:00+00:00"]


def test_the_dataset_hash_is_deterministic_and_content_bound() -> None:
    a = _dataset()
    b = _dataset()

    assert dataset_sha256(a) == dataset_sha256(b)
    changed = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS,
        rows_by_symbol={"BTC/USDT": [_frozen_row(0, label=51.0)]},
    )
    assert dataset_sha256(changed) != dataset_sha256(a)


def test_the_dataset_hash_ignores_input_row_order() -> None:
    """Sonst waere ein Retry je nach Ladereihenfolge ein anderer Datensatz."""
    forward = {"BTC/USDT": [_frozen_row(0), _frozen_row(40)]}
    reverse = {"BTC/USDT": [_frozen_row(40), _frozen_row(0)]}

    a = build_frozen_dataset(
        checkpoint="T1", t0_utc=_T0, cutoff_utc=_T1, sealed_symbols=_SYMBOLS, rows_by_symbol=forward
    )
    b = build_frozen_dataset(
        checkpoint="T1", t0_utc=_T0, cutoff_utc=_T1, sealed_symbols=_SYMBOLS, rows_by_symbol=reverse
    )

    assert dataset_sha256(a) == dataset_sha256(b)


# ── B. Die Evaluationsidentitaet ────────────────────────────────────────────


def _input(dataset=None, candidate=None, activation=None, counts=None):
    from app.research.prereg_window import MaturityCounts

    candidate = candidate or _candidate()
    return build_frozen_input(
        dataset=dataset or _dataset(),
        candidate=candidate,
        activation=activation or _activation(candidate),
        sealed_universe_sha256=_UNIVERSE_SHA,
        sealed_symbols=_SYMBOLS,
        maturity_counts=counts or MaturityCounts(n_valid=5, n_clusters=5),
    )


def test_the_contract_comes_from_the_candidate_not_from_a_caller() -> None:
    frozen = _input()

    assert frozen.resolved_contract["round_trip_cost_bps"] == 20.0
    assert frozen.resolved_contract["economic_floor_bps"] == 5.0
    assert frozen.resolved_contract["horizon"] == 4
    assert frozen.resolved_contract["alpha"] == 0.05


def test_a_different_cost_is_a_different_evaluation_identity() -> None:
    """Dieselben Daten unter anderen Kosten sind NICHT dieselbe Auswertung."""
    cheap = _candidate(round_trip_cost_bps=10.0)

    a = evaluation_input_sha256(_input())
    b = evaluation_input_sha256(_input(candidate=cheap, activation=_activation(cheap)))

    assert a != b


def test_thirty_three_symbols_are_refused_even_with_the_right_universe_hash() -> None:
    """Die Luecke, die ``run_confirmatory`` hatte.

    Ein korrekter ``universe_sha256`` beweist, WELCHES Universum versiegelt
    wurde — nicht, dass die Daten genau dieses abdecken.
    """
    short = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS[:2],
        rows_by_symbol=_rows({"BTC/USDT": 1}),
    )

    with pytest.raises(FrozenInputError, match="nicht das versiegelte Universum"):
        _input(dataset=short)


def test_an_activation_for_another_candidate_is_refused() -> None:
    other = _candidate(cluster_min=7)

    with pytest.raises(FrozenInputError, match="verweist auf Candidate"):
        _input(candidate=_candidate(), activation=_activation(other))


def test_a_cutoff_that_is_not_the_sealed_checkpoint_is_refused() -> None:
    wrong = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc="2026-12-15T00:00:00+00:00",
        sealed_symbols=_SYMBOLS,
        rows_by_symbol=_rows({"BTC/USDT": 1}),
    )

    with pytest.raises(FrozenInputError, match="nicht der versiegelte"):
        _input(dataset=wrong)


def test_the_artifact_hash_is_verified_against_its_content(tmp_path: Path) -> None:
    """Der Dateiname ist ein Hinweis, kein Beweis — er laesst sich umbenennen."""
    dataset = _dataset()
    frozen = _input(dataset=dataset)
    digest = evaluation_input_sha256(frozen)
    write_frozen_artifact(tmp_path, frozen, dataset)

    assert read_frozen_artifact(tmp_path, digest)["input"]["dataset_sha256"]

    path = tmp_path / f"evaluation_input_{digest}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["input"]["n_symbols"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FrozenInputError, match="passt nicht zum Inhalt"):
        read_frozen_artifact(tmp_path, digest)


def test_a_missing_artifact_is_an_error_not_a_reload(tmp_path: Path) -> None:
    with pytest.raises(FrozenInputError, match="das Artefakt fehlt"):
        read_frozen_artifact(tmp_path, "a" * 64)


def test_writing_the_same_artifact_twice_is_idempotent(tmp_path: Path) -> None:
    dataset = _dataset()
    frozen = _input(dataset=dataset)

    first = write_frozen_artifact(tmp_path, frozen, dataset)
    second = write_frozen_artifact(tmp_path, frozen, dataset)

    assert first == second
    assert len(list(tmp_path.glob("evaluation_input_*.json"))) == 1


# ── C/D. Zustandsautomat und Wiederaufnahme ─────────────────────────────────


def _paths(tmp_path: Path):
    return tmp_path / "checkpoints.jsonl", tmp_path / "verdicts.jsonl", tmp_path / "frozen"


def _decide(tmp_path: Path, *, now: str = _T1, counts=None, candidate=None):
    candidate = candidate or _candidate()
    checkpoints, verdicts, artifacts = _paths(tmp_path)
    return (
        decide_and_freeze(
            now_utc=now,
            candidate=candidate,
            activation=_activation(candidate),
            sealed_symbols=_SYMBOLS,
            sealed_universe_sha256=_UNIVERSE_SHA,
            rows_by_symbol=_rows(counts or {"BTC/USDT": 3, "ETH/USDT": 2, "SOL/USDT": 1}),
            checkpoint_journal=checkpoints,
            verdict_journal=verdicts,
            artifact_dir=artifacts,
        ),
        candidate,
    )


def test_an_extension_writes_no_artifact(tmp_path: Path) -> None:
    """Bei ``EXTEND`` wird nichts gewertet — also entsteht auch kein Datenschnitt."""
    (decision, digest), _ = _decide(
        tmp_path, counts={"BTC/USDT": 1}, candidate=_candidate(n_valid_min=100, cluster_min=50)
    )

    assert decision.action == ACTION_EXTEND_TO_T2
    assert digest == ""
    assert not (tmp_path / "frozen").exists()


def test_the_artifact_exists_before_the_journal_says_evaluate(tmp_path: Path) -> None:
    """Der eigentliche Sicherheitsgewinn: die Reihenfolge.

    Stuende ``EVALUATE`` im Journal ohne Artefakt, waere der Entschluss erhalten
    und seine Datengrundlage nicht — der Neustart wuerde neu laden.
    """
    (decision, digest), candidate = _decide(tmp_path)
    checkpoints, _, artifacts = _paths(tmp_path)

    assert decision.action == ACTION_EVALUATE
    assert (artifacts / f"evaluation_input_{digest}.json").exists()

    journal = [json.loads(line) for line in checkpoints.read_text(encoding="utf-8").splitlines()]
    assert journal[0]["action"] == ACTION_EVALUATE
    assert journal[0]["evaluation_input_sha256"] == digest


def test_a_restart_without_a_verdict_resumes_on_the_frozen_input(tmp_path: Path) -> None:
    """Wiederaufnahme ist nicht Wiederholung: derselbe Schnitt, kein zweiter Blick."""
    (_first, digest), candidate = _decide(tmp_path)

    # Neustart. Der Provider liefert inzwischen MEHR Zeilen — sie duerfen das
    # Ergebnis nicht beruehren.
    (second, second_digest), _ = _decide(tmp_path, counts={"BTC/USDT": 9, "ETH/USDT": 9})

    assert second.action == ACTION_RESUME_EVALUATION
    assert second.must_use_frozen_input
    assert second_digest == ""
    checkpoints, _, _ = _paths(tmp_path)
    assert (
        resume_evaluation_input_sha256(
            checkpoints,
            activation_sha256_value=json.loads(
                checkpoints.read_text(encoding="utf-8").splitlines()[0]
            )["activation_sha256"],
            checkpoint="T1",
        )
        == digest
    )


def test_re_evaluating_the_frozen_input_reproduces_the_same_result(tmp_path: Path) -> None:
    """Der Beweis, dass eine Wiederaufnahme keine zweite Auswertung ist."""
    (decision, digest), candidate = _decide(tmp_path)
    _, verdicts, artifacts = _paths(tmp_path)
    activation = _activation(candidate)

    first = run_sealed_evaluation(
        decision=decision,
        evaluation_input_sha256_value=digest,
        candidate=candidate,
        activation=activation,
        artifact_dir=artifacts,
        verdict_journal=verdicts,
        now_utc=_T1,
    )
    stored = load_verdicts(verdicts, activation_sha256_value=_act(activation))

    second = run_sealed_evaluation(
        decision=decision,
        evaluation_input_sha256_value=digest,
        candidate=candidate,
        activation=activation,
        artifact_dir=artifacts,
        verdict_journal=verdicts,
        now_utc="2026-12-01T00:00:00+00:00",
    )

    assert first.verdict == second.verdict
    assert first.summary.p_value == second.summary.p_value
    assert len(stored) == 1
    assert len(load_verdicts(verdicts, activation_sha256_value=_act(activation))) == 1


def _act(activation) -> str:
    from app.research.prereg_candidate import activation_sha256

    return activation_sha256(activation)


def test_only_a_recorded_verdict_closes_the_checkpoint(tmp_path: Path) -> None:
    (decision, digest), candidate = _decide(tmp_path)
    _, verdicts, artifacts = _paths(tmp_path)
    activation = _activation(candidate)
    run_sealed_evaluation(
        decision=decision,
        evaluation_input_sha256_value=digest,
        candidate=candidate,
        activation=activation,
        artifact_dir=artifacts,
        verdict_journal=verdicts,
        now_utc=_T1,
    )

    (after, _), _ = _decide(tmp_path, now=_T2)

    assert after.action == ACTION_CLOSED


def test_an_evaluate_without_a_hash_cannot_be_resumed(tmp_path: Path) -> None:
    """Lieber ein Abbruch als eine frische Ladung Daten."""
    from app.research.prereg_window_state import CheckpointRecord, record_checkpoint

    checkpoints, _, _ = _paths(tmp_path)
    record_checkpoint(
        checkpoints,
        CheckpointRecord(
            activation_sha256="a" * 64,
            checkpoint="T1",
            action=ACTION_EVALUATE,
            mature=True,
            recorded_at_utc=_T1,
            counts={"n_valid": 5},
        ),
    )

    with pytest.raises(SealedEvaluationError, match="nicht wiederherstellbar"):
        resume_evaluation_input_sha256(
            checkpoints, activation_sha256_value="a" * 64, checkpoint="T1"
        )


# ── E. Der aktivierungsgebundene Evaluator ──────────────────────────────────


def test_the_sealed_evaluator_takes_no_research_parameters() -> None:
    """Struktur-Wache: kein Weg, am Candidate vorbei etwas anderes zu messen."""
    import inspect

    signature = inspect.signature(run_sealed_evaluation)

    for forbidden in (
        "hypothesis",
        "decide",
        "panels",
        "universe_sha256",
        "horizon",
        "round_trip_cost_bps",
        "alpha",
        "economic_floor_bps",
        "n_min",
        "cluster_min",
        "kwargs",
    ):
        assert forbidden not in signature.parameters, forbidden


def test_the_evaluator_uses_the_sealed_cost_not_a_default(tmp_path: Path) -> None:
    """Label 50 bps, versiegelte Kosten 20 ⇒ Mittelwert 30. Kein Default greift."""
    (decision, digest), candidate = _decide(tmp_path)
    _, verdicts, artifacts = _paths(tmp_path)

    result = run_sealed_evaluation(
        decision=decision,
        evaluation_input_sha256_value=digest,
        candidate=candidate,
        activation=_activation(candidate),
        artifact_dir=artifacts,
        verdict_journal=verdicts,
        now_utc=_T1,
    )

    assert result.round_trip_cost_bps == 20.0
    assert result.economic_floor_bps == 5.0
    # Labels 50/53/56 | 50/53 | 50, minus 20 bps versiegelte Kosten.
    gross = [50.0, 53.0, 56.0, 50.0, 53.0, 50.0]
    assert result.summary.mean_bps == pytest.approx(sum(gross) / len(gross) - 20.0)


def test_the_evaluator_refuses_when_the_window_says_no(tmp_path: Path) -> None:
    from app.research.prereg_window import ACTION_WAIT, MaturityCounts, WindowDecision

    (_d, digest), candidate = _decide(tmp_path)
    _, verdicts, artifacts = _paths(tmp_path)
    waiting = WindowDecision(
        action=ACTION_WAIT,
        checkpoint="PRE_T1",
        mature=True,
        counts=MaturityCounts(n_valid=9, n_clusters=9),
    )

    with pytest.raises(SealedEvaluationError, match="darf nicht gewertet werden"):
        run_sealed_evaluation(
            decision=waiting,
            evaluation_input_sha256_value=digest,
            candidate=candidate,
            activation=_activation(candidate),
            artifact_dir=artifacts,
            verdict_journal=verdicts,
            now_utc=_T1,
        )


def test_the_verdict_record_links_the_whole_chain(tmp_path: Path) -> None:
    """WAS · UNTER WELCHEM VERTRAG · WELCHE POPULATION · WELCHER CODE · ERGEBNIS."""
    (decision, digest), candidate = _decide(tmp_path)
    _, verdicts, artifacts = _paths(tmp_path)
    activation = _activation(candidate)
    run_sealed_evaluation(
        decision=decision,
        evaluation_input_sha256_value=digest,
        candidate=candidate,
        activation=activation,
        artifact_dir=artifacts,
        verdict_journal=verdicts,
        now_utc=_T1,
    )

    record = load_verdicts(verdicts, activation_sha256_value=_act(activation))[0]

    assert record.evaluation_input_sha256 == digest
    assert record.evaluator_sha256 == _EVAL_SHA
    assert record.activation_sha256 == _act(activation)
    assert record.alpha == 0.05
    assert record.economic_floor_bps == 5.0
    assert len(record.result_sha256) == 64


def test_a_tampered_verdict_is_detected(tmp_path: Path) -> None:
    (decision, digest), candidate = _decide(tmp_path)
    _, verdicts, artifacts = _paths(tmp_path)
    activation = _activation(candidate)
    run_sealed_evaluation(
        decision=decision,
        evaluation_input_sha256_value=digest,
        candidate=candidate,
        activation=activation,
        artifact_dir=artifacts,
        verdict_journal=verdicts,
        now_utc=_T1,
    )

    payload = json.loads(verdicts.read_text(encoding="utf-8").splitlines()[0])
    payload["p_value"] = 0.0001
    verdicts.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    from app.research.prereg_window_state import CheckpointJournalError

    with pytest.raises(CheckpointJournalError, match="nachtraeglich veraendert"):
        load_verdicts(verdicts, activation_sha256_value=_act(activation))


def test_the_candidate_hash_still_matches_the_committed_artifact() -> None:
    """Gegenprobe: die Haertung von ``activate`` hat den Candidate nicht veraendert."""
    payload = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "research"
            / "prereg_rsi_reentry_volume_v1_candidate.json"
        ).read_text(encoding="utf-8")
    )

    rebuilt = build_rsi_reentry_volume_candidate(payload["universe_sha256"], payload["n_symbols"])

    assert payload["candidate_sha256"] == candidate_sha256(rebuilt)


def test_a_degenerate_statistic_is_stored_as_none_not_infinity(tmp_path: Path) -> None:
    """Bei Streuung null liefert der Sandwich ein unendliches t.

    Das ist ein legitimes Ergebnis, aber kein JSON: ``Infinity`` waere beim
    Zurueckladen etwas, das wie eine Zahl aussieht. Der p-Wert traegt dieselbe
    Information. Aufgefallen ist das erst, weil der Testdatensatz zunaechst
    lauter identische Labels hatte.
    """
    checkpoints, verdicts, artifacts = _paths(tmp_path)
    candidate = _candidate()
    identical = {
        "BTC/USDT": [_frozen_row(0, label=50.0), _frozen_row(40, label=50.0)],
        "ETH/USDT": [_frozen_row(80, label=50.0)],
    }
    decision, digest = decide_and_freeze(
        now_utc=_T1,
        candidate=candidate,
        activation=_activation(candidate),
        sealed_symbols=_SYMBOLS,
        sealed_universe_sha256=_UNIVERSE_SHA,
        rows_by_symbol=identical,
        checkpoint_journal=checkpoints,
        verdict_journal=verdicts,
        artifact_dir=artifacts,
    )
    activation = _activation(candidate)

    run_sealed_evaluation(
        decision=decision,
        evaluation_input_sha256_value=digest,
        candidate=candidate,
        activation=activation,
        artifact_dir=artifacts,
        verdict_journal=verdicts,
        now_utc=_T1,
    )

    record = load_verdicts(verdicts, activation_sha256_value=_act(activation))[0]
    assert record.t_statistic is None
    assert record.standard_error == 0.0
    assert len(record.result_sha256) == 64


def test_an_unregistered_hypothesis_cannot_be_evaluated() -> None:
    """Der Decider wird ueber den versiegelten Namen aufgeloest, nicht uebergeben."""
    from app.research.prereg_evaluation import _resolve_decider_or_fail

    with pytest.raises(SealedEvaluationError, match="nicht registriert"):
        _resolve_decider_or_fail("etwas_anderes")


def test_data_unavailable_rows_are_frozen_but_do_not_count(tmp_path: Path) -> None:
    """Eine Feuerung ohne Label bleibt im Artefakt sichtbar und ausserhalb von n_valid."""
    checkpoints, verdicts, artifacts = _paths(tmp_path)
    candidate = _candidate()
    decision, digest = decide_and_freeze(
        now_utc=_T1,
        candidate=candidate,
        activation=_activation(candidate),
        sealed_symbols=_SYMBOLS,
        sealed_universe_sha256=_UNIVERSE_SHA,
        rows_by_symbol=_rows({"BTC/USDT": 4}, missing=2),
        checkpoint_journal=checkpoints,
        verdict_journal=verdicts,
        artifact_dir=artifacts,
    )

    payload = read_frozen_artifact(artifacts, digest)
    labels = [row["label_bps"] for row in payload["dataset"]["panels"][0]["rows"]]
    counts = payload["input"]["maturity_counts"]

    assert labels.count(None) == 2, "die nicht auswertbaren Feuerungen stehen im Artefakt"
    assert counts["raw_fires"] == 4
    assert counts["n_valid"] == 2
    assert counts["data_unavailable_count"] == 2


def test_the_verdict_carries_its_own_decomposition(tmp_path: Path) -> None:
    """Der p-Wert darf gar nicht erst ohne seine Zerlegung zitierbar sein.

    Direktive 2026-08-08: kein Aggregat ohne Zerlegung. Der Ratchet hat genau
    an dieser Funktion angeschlagen — zu Recht, denn sie ist die einzige der
    neuen, die eine urteilstragende Kennzahl erzeugt.
    """
    (decision, digest), candidate = _decide(tmp_path)
    _, verdicts, artifacts = _paths(tmp_path)
    activation = _activation(candidate)
    run_sealed_evaluation(
        decision=decision,
        evaluation_input_sha256_value=digest,
        candidate=candidate,
        activation=activation,
        artifact_dir=artifacts,
        verdict_journal=verdicts,
        now_utc=_T1,
    )

    decomposition = load_verdicts(verdicts, activation_sha256_value=_act(activation))[
        0
    ].decomposition

    assert decomposition["status"] == "DIAGNOSTIC_NON_GATING"
    assert decomposition["per_symbol_signals"] == {"BTC/USDT": 3, "ETH/USDT": 2, "SOL/USDT": 1}
    assert decomposition["leave_one_out_top_symbol"]["symbol"] == "BTC/USDT"
    assert {d["label"] for d in decomposition["robustness"]} == {
        "result_without_largest_cluster",
        "result_without_top_symbol",
    }
