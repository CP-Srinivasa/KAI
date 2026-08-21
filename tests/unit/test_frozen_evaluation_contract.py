r"""Aus einer versiegelten Absicht eine EINMALIGE, reproduzierbare Auswertung machen.

Die vollstaendige Tabelle, ausschliesslich aus dem Journal::

    T2 EVALUATE + Verdikt    -> CLOSED       T1 EVALUATE + Verdikt  -> CLOSED
    T2 EVALUATE ohne Verdikt -> RESUME T2    T1 EVALUATE ohne V.    -> RESUME T1
    T2 INCONCLUSIVE          -> CLOSED       T1 EXTEND, < T2        -> WAIT
    nichts, < T1             -> WAIT         T1 EXTEND, >= T2       -> UNDECIDED(T2)

Vier Zusicherungen, die ueber "der Ausgang stimmt" hinausgehen:

**Der Plan entsteht ohne Daten.** ``rows_loader`` wird auf CLOSED, WAIT und
RESUME nicht aufgerufen — ein Loader, der wirft, beweist das.

**Die Frozen-Grenze liegt VOR der Signalauswahl.** Eingefroren wird der
vollstaendige OOS-Schnitt; der Decider laeuft danach aus dem Artefakt. Sonst
koennte das Artefakt zwar zeigen, welche Feuerungen gewertet wurden, aber nicht,
ob die richtigen ausgewaehlt wurden.

**Das Universum wird geladen, nicht uebergeben.** Der staerkere Angriff ist
nicht "33 statt 34", sondern *irgendeine* andere 34er-Liste neben dem korrekten
offiziellen Hash als getrennt uebergebenem String.

**Der laufende Code wird bewiesen.** Ein wohlgeformter Hash im Artefakt ist
keine Aussage darueber, welche Implementierung gerade rechnet.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.analysis.features.feature_matrix import FeatureRow
from app.research.evaluator_identity import (
    EvaluatorIdentityError,
    assert_runtime_matches,
    evaluator_bundle_sha256,
)
from app.research.frozen_dataset import (
    FrozenDatasetError,
    FrozenRow,
    build_frozen_dataset,
    canonical_bytes,
    dataset_to_dict,
)
from app.research.frozen_input import (
    FrozenInputError,
    build_frozen_input,
    evaluation_input_sha256,
    load_sealed_universe,
    write_frozen_artifact,
)
from app.research.prereg_candidate import activate, build_rsi_reentry_volume_candidate
from app.research.prereg_evaluation import (
    PLAN_CLOSED,
    PLAN_RESUME,
    PLAN_UNDECIDED,
    PLAN_WAIT,
    CheckpointPlan,
    SealedEvaluationError,
    decide_and_freeze,
    frozen_rows_from_panel,
    load_verdicts,
    plan_checkpoint,
    resolve_decider,
    run_sealed_evaluation,
)
from app.research.prereg_storage import (
    PreRegStorageError,
    initialise_activation,
    read_active,
    verdict_journal_path,
)

REPO = Path(__file__).resolve().parents[2]
_UNIVERSE_ARTIFACT = json.loads(
    (REPO / "docs" / "research" / "universe_rsi_reentry_v1.json").read_text(encoding="utf-8")
)
_UNIVERSE_SHA = _UNIVERSE_ARTIFACT["universe_sha256"]
_SYMBOLS = tuple(_UNIVERSE_ARTIFACT["canonical_universe"])

_CODE_SHA = "c" * 40
_EVAL_SHA = "e" * 64
_T0 = "2026-09-01T00:00:00+00:00"
_T1 = "2026-11-30T00:00:00+00:00"
_T2 = "2027-02-28T00:00:00+00:00"
_AFTER_T2 = "2027-03-05T00:00:00+00:00"
_BETWEEN = "2026-12-20T00:00:00+00:00"


def _candidate(**overrides):
    """Echte Struktur, aber Schranken, die ein kleines Sample erreichen kann."""
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


def _feature_row(hour: int, *, fires: bool) -> FeatureRow:
    return FeatureRow(
        timestamp_utc=f"2026-10-{1 + hour // 24:02d}T{hour % 24:02d}:00:00+00:00",
        close=100.0,
        log_return=None,
        rsi_14=31.0 if fires else 50.0,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
        rsi_14_prev=28.0 if fires else 50.0,
        volume_z_20=3.0 if fires else 0.0,
    )


def _row(hour: int, *, fires: bool = True, label: float | None = 50.0) -> FrozenRow:
    row = _feature_row(hour, fires=fires)
    exit_hour = hour + 4
    return FrozenRow(
        signal_timestamp_utc=row.timestamp_utc,
        label_exit_utc=f"2026-10-{1 + exit_hour // 24:02d}T{exit_hour % 24:02d}:00:00+00:00",
        features={k: v for k, v in asdict(row).items() if k != "timestamp_utc"},
        label_bps=label,
    )


def _rows(fires: int = 3, *, quiet: int = 2, missing: int = 0):
    """Ein vollstaendiger Schnitt: feuernde UND stille Zeilen."""
    out: dict[str, list[FrozenRow]] = {symbol: [] for symbol in _SYMBOLS}
    hour = 0
    for index in range(fires):
        out[_SYMBOLS[index % 3]].append(
            _row(hour, label=None if index < missing else 50.0 + index * 3.0)
        )
        hour += 40
    for _ in range(quiet):
        out[_SYMBOLS[0]].append(_row(hour, fires=False))
        hour += 40
    return out


def _tree(tmp_path: Path, activation) -> Path:
    root = tmp_path / "prereg"
    initialise_activation(root, activation)
    return root


# ── Ablagestruktur ──────────────────────────────────────────────────────────


def test_activation_creates_the_complete_tree(tmp_path: Path) -> None:
    """Nach T0 steht alles — ein fehlendes Verzeichnis darf nicht erst am
    Checkpoint auffallen."""
    activation = _activation()
    root = _tree(tmp_path, activation)
    sha = read_active(root)

    directory = root / sha
    assert (directory / "activation.json").exists()
    assert (directory / "checkpoints.jsonl").read_text(encoding="utf-8") == ""
    assert (directory / "verdicts.jsonl").read_text(encoding="utf-8") == ""
    assert (directory / "frozen" / "T1").is_dir()
    assert (directory / "frozen" / "T2").is_dir()


def test_the_active_pointer_is_validated_not_trusted(tmp_path: Path) -> None:
    """Ein Zeiger auf eine unvollstaendige Ablage ist schlimmer als keiner."""
    root = tmp_path / "prereg"
    root.mkdir()
    (root / "ACTIVE").write_text("nicht-hex\n", encoding="utf-8")

    with pytest.raises(PreRegStorageError, match="erwartet 64 Hex"):
        read_active(root)

    (root / "ACTIVE").write_text("a" * 64 + "\n", encoding="utf-8")
    with pytest.raises(PreRegStorageError, match="unvollstaendig"):
        read_active(root)


def test_an_activation_is_never_overwritten(tmp_path: Path) -> None:
    activation = _activation()
    root = _tree(tmp_path, activation)
    initialise_activation(root, activation)  # identisch -> No-Op

    sha = read_active(root)
    (root / sha / "activation.json").write_text("{}", encoding="utf-8")

    with pytest.raises(PreRegStorageError, match="ANDEREM Inhalt"):
        initialise_activation(root, activation)


def test_the_backup_covers_the_runtime_tree() -> None:
    """Nicht in Git ⇒ das Backup ist der EINZIGE Rueckweg."""
    script = (REPO / "scripts" / "kai_backup_artifacts.sh").read_text(encoding="utf-8")

    assert '"artifacts/research/prereg"' in script


# ── Das Universum wird geladen, nicht uebergeben ────────────────────────────


def test_the_universe_comes_from_the_repo_artifact() -> None:
    sha, symbols = load_sealed_universe(REPO, expected_sha256=_UNIVERSE_SHA)

    assert sha == _UNIVERSE_SHA
    assert len(symbols) == 34
    assert symbols == _SYMBOLS


def test_a_candidate_pointing_elsewhere_is_refused() -> None:
    with pytest.raises(FrozenInputError, match="verschiedene Populationen"):
        load_sealed_universe(REPO, expected_sha256="d" * 64)


def test_a_forged_symbol_list_cannot_pass_with_the_official_hash(tmp_path: Path) -> None:
    """DER staerkere Angriff: andere 34 Symbole, korrekter offizieller Hash.

    Frueher haette der Aufrufer beide Wahrheiten mitgebracht. Jetzt wird der
    Hash aus dem INHALT nachgerechnet — die Faelschung faellt auf.
    """
    forged = dict(_UNIVERSE_ARTIFACT)
    forged["canonical_universe"] = [*list(_SYMBOLS)[:33], "SCAM/USDT"]
    fake_repo = tmp_path / "repo"
    (fake_repo / "docs" / "research").mkdir(parents=True)
    (fake_repo / "docs" / "research" / "universe_rsi_reentry_v1.json").write_text(
        json.dumps(forged), encoding="utf-8"
    )

    with pytest.raises(FrozenInputError, match="passt nicht zur Liste"):
        load_sealed_universe(fake_repo, expected_sha256=_UNIVERSE_SHA)


def test_a_blocked_universe_is_not_used(tmp_path: Path) -> None:
    blocked = dict(_UNIVERSE_ARTIFACT)
    blocked["ok"] = False
    fake_repo = tmp_path / "repo"
    (fake_repo / "docs" / "research").mkdir(parents=True)
    (fake_repo / "docs" / "research" / "universe_rsi_reentry_v1.json").write_text(
        json.dumps(blocked), encoding="utf-8"
    )

    with pytest.raises(FrozenInputError, match="ok=false"):
        load_sealed_universe(fake_repo, expected_sha256=_UNIVERSE_SHA)


# ── Der eingefrorene Datenschnitt ───────────────────────────────────────────


def test_all_thirty_four_symbols_stay_members(tmp_path: Path) -> None:
    """``DATA_UNAVAILABLE`` ist NICHT ``asset removed``."""
    dataset = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS,
        rows_by_symbol=_rows(),
    )

    assert len(dataset.panels) == 34
    assert tuple(p.symbol for p in dataset.panels) == _SYMBOLS


def test_none_and_zero_stay_different() -> None:
    dataset = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS,
        rows_by_symbol={_SYMBOLS[0]: [_row(0, label=0.0), _row(40, label=None)]},
    )

    payload = dataset_to_dict(dataset)
    assert payload["panels"][0]["rows"][0]["label_bps"] == 0.0
    assert payload["panels"][0]["rows"][1]["label_bps"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_cannot_be_frozen(bad: float) -> None:
    with pytest.raises(FrozenDatasetError, match="nicht endlich"):
        build_frozen_dataset(
            checkpoint="T1",
            t0_utc=_T0,
            cutoff_utc=_T1,
            sealed_symbols=_SYMBOLS,
            rows_by_symbol={_SYMBOLS[0]: [_row(0, label=bad)]},
        )


def test_canonical_bytes_refuse_nan() -> None:
    with pytest.raises(ValueError, match="Out of range"):
        canonical_bytes({"x": float("nan")})


def test_the_window_is_about_observability() -> None:
    """Ein Label zaehlt nur, wenn es bis zum Checkpoint VOLLSTAENDIG vorlag."""
    inside = FrozenRow(
        signal_timestamp_utc="2026-11-29T00:00:00+00:00",
        label_exit_utc="2026-11-29T04:00:00+00:00",
        features=_row(0).features,
        label_bps=10.0,
    )
    exits_after = replace(
        inside,
        signal_timestamp_utc="2026-11-29T23:00:00+00:00",
        label_exit_utc="2026-11-30T03:00:00+00:00",
    )

    dataset = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS,
        rows_by_symbol={_SYMBOLS[0]: [inside, exits_after]},
    )

    assert [r.signal_timestamp_utc for r in dataset.panels[0].rows] == ["2026-11-29T00:00:00+00:00"]


def test_the_frozen_boundary_lies_before_signal_selection() -> None:
    """Auch NICHT feuernde Zeilen werden eingefroren.

    Sonst koennte das Artefakt zeigen, WELCHE Feuerungen gewertet wurden, aber
    nicht, ob aus dem urspruenglichen Schnitt die richtigen ausgewaehlt wurden.
    """
    rows = [_feature_row(0, fires=True), _feature_row(1, fires=False)]
    frozen = frozen_rows_from_panel(
        rows, [50.0, 10.0], ["2026-10-01T04:00:00+00:00", "2026-10-01T05:00:00+00:00"]
    )

    assert len(frozen) == 2, "der Decider wird beim Einfrieren NICHT gefragt"
    assert frozen[1].features["rsi_14"] == 50.0


# ── Artefakt ────────────────────────────────────────────────────────────────


def _input(tmp_path: Path, candidate=None, activation=None, rows=None):
    from app.research.prereg_window import MaturityCounts

    candidate = candidate or _candidate()
    dataset = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS,
        rows_by_symbol=rows if rows is not None else _rows(),
    )
    frozen = build_frozen_input(
        dataset=dataset,
        candidate=candidate,
        activation=activation or _activation(candidate),
        sealed_universe_sha256=_UNIVERSE_SHA,
        sealed_symbols=_SYMBOLS,
        maturity_counts=MaturityCounts(n_valid=3, n_clusters=3),
    )
    return frozen, dataset


def test_the_sealed_sensitivity_axis_is_part_of_the_contract(tmp_path: Path) -> None:
    """ "Ausschliesslich aus dem Frozen Input" soll buchstaeblich stimmen."""
    frozen, _ = _input(tmp_path)

    assert frozen.resolved_contract["sensitivity_cost_bps"] == [20.0, 25.0, 30.0]


def test_an_existing_artifact_is_revalidated_not_trusted(tmp_path: Path) -> None:
    """Der Dateiname ist kein Beweis — gerade weil danach EVALUATE folgt."""
    frozen, dataset = _input(tmp_path)
    digest = evaluation_input_sha256(frozen)
    directory = tmp_path / "frozen"
    write_frozen_artifact(directory, frozen, dataset)

    assert write_frozen_artifact(directory, frozen, dataset).name.endswith(f"{digest}.json")

    path = directory / f"evaluation_input_{digest}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["input"]["n_symbols"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FrozenInputError, match="passt nicht zum Inhalt"):
        write_frozen_artifact(directory, frozen, dataset)


def test_thirty_three_symbols_are_refused(tmp_path: Path) -> None:
    # 33 Symbole, sonst alles korrekt — der Datensatz selbst ist in sich
    # stimmig, nur eben nicht das versiegelte Universum.
    thirty_three = _SYMBOLS[:33]
    short = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=thirty_three,
        rows_by_symbol={s: rows for s, rows in _rows().items() if s in thirty_three},
    )
    from app.research.prereg_window import MaturityCounts

    with pytest.raises(FrozenInputError, match="nicht das versiegelte Universum"):
        build_frozen_input(
            dataset=short,
            candidate=_candidate(),
            activation=_activation(),
            sealed_universe_sha256=_UNIVERSE_SHA,
            sealed_symbols=_SYMBOLS,
            maturity_counts=MaturityCounts(n_valid=1, n_clusters=1),
        )


# ── Der Plan: ohne Daten ────────────────────────────────────────────────────


class _ExplodingLoader:
    """Ein Loader, der beweist, dass er nicht aufgerufen wurde."""

    def __call__(self):
        raise AssertionError("es wurden Daten geladen, obwohl der Plan das verbietet")


def _decide(root: Path, candidate, activation, now: str, rows=None):
    return decide_and_freeze(
        now_utc=now,
        candidate=candidate,
        activation=activation,
        root=root,
        repo_root=REPO,
        rows_loader=(lambda: rows) if rows is not None else _ExplodingLoader(),
    )


def test_before_t1_no_data_is_loaded(tmp_path: Path) -> None:
    candidate = _candidate()
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)

    plan, counts = _decide(root, candidate, activation, "2026-11-01T00:00:00+00:00")

    assert plan.action == PLAN_WAIT
    assert counts is None


def test_the_artifact_exists_before_the_journal_says_evaluate(tmp_path: Path) -> None:
    candidate = _candidate()
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)

    plan, counts = _decide(root, candidate, activation, _T1, rows=_rows())
    sha = read_active(root)

    assert plan.action == PLAN_UNDECIDED
    assert counts is not None and counts.n_valid == 3
    artifact = (
        root / sha / "frozen" / "T1" / f"evaluation_input_{plan.evaluation_input_sha256}.json"
    )
    assert artifact.exists()
    journal = [
        json.loads(line)
        for line in (root / sha / "checkpoints.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert journal[0]["action"] == "EVALUATE"
    assert journal[0]["evaluation_input_sha256"] == plan.evaluation_input_sha256


def test_a_restart_resumes_without_touching_the_loader(tmp_path: Path) -> None:
    """Die Zusicherung ueber den WEG, nicht nur ueber den Ausgang."""
    candidate = _candidate()
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)
    first, _ = _decide(root, candidate, activation, _T1, rows=_rows())

    plan, counts = _decide(root, candidate, activation, _BETWEEN)  # Loader explodiert

    assert plan.action == PLAN_RESUME
    assert plan.must_use_frozen_input
    assert plan.evaluation_input_sha256 == first.evaluation_input_sha256
    assert counts is None


def test_an_immature_t1_extends_and_then_waits(tmp_path: Path) -> None:
    candidate = _candidate(n_valid_min=100, cluster_min=50)
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)

    extended, counts = _decide(root, candidate, activation, _T1, rows=_rows(fires=2))
    assert extended.action == PLAN_WAIT
    assert counts is not None and counts.n_valid == 2

    waiting, _ = _decide(root, candidate, activation, _BETWEEN)
    assert waiting.action == PLAN_WAIT


def test_an_immature_t2_is_terminal(tmp_path: Path) -> None:
    """Fristende ohne Reife ist INCONCLUSIVE — und danach ist Schluss."""
    candidate = _candidate(n_valid_min=100, cluster_min=50)
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)
    _decide(root, candidate, activation, _T1, rows=_rows(fires=2))

    at_t2, _ = _decide(root, candidate, activation, _AFTER_T2, rows=_rows(fires=2))
    assert at_t2.action == PLAN_CLOSED

    later, _ = _decide(root, candidate, activation, "2027-06-01T00:00:00+00:00")
    assert later.action == PLAN_CLOSED
    assert "terminal" in " ".join(later.reasons)


def test_a_t2_evaluate_without_a_verdict_resumes_on_t2(tmp_path: Path) -> None:
    """Der Pfad, der vorher gar nicht existierte."""
    candidate = _candidate(n_valid_min=3, cluster_min=1)
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)
    _decide(root, candidate, activation, _T1, rows=_rows(fires=2))  # unreif -> EXTEND

    frozen_at_t2, _ = _decide(root, candidate, activation, _AFTER_T2, rows=_rows(fires=3))
    assert frozen_at_t2.action == PLAN_UNDECIDED
    assert frozen_at_t2.checkpoint == "T2"

    resumed, _ = _decide(root, candidate, activation, "2027-03-10T00:00:00+00:00")
    assert resumed.action == PLAN_RESUME
    assert resumed.checkpoint == "T2"
    assert resumed.evaluation_input_sha256 == frozen_at_t2.evaluation_input_sha256


def test_a_journal_evaluate_without_a_hash_cannot_be_resumed(tmp_path: Path) -> None:
    from app.research.prereg_storage import checkpoint_journal_path
    from app.research.prereg_window_state import CheckpointRecord, record_checkpoint

    activation = _activation()
    root = _tree(tmp_path, activation)
    sha = read_active(root)
    record_checkpoint(
        checkpoint_journal_path(root, sha),
        CheckpointRecord(
            activation_sha256=sha,
            checkpoint="T1",
            action="EVALUATE",
            mature=True,
            recorded_at_utc=_T1,
            counts={"n_valid": 5},
        ),
    )

    with pytest.raises(SealedEvaluationError, match="nicht wiederherstellbar"):
        plan_checkpoint(now_utc=_T1, activation=activation, root=root)


# ── Auswertung ──────────────────────────────────────────────────────────────


def _evaluate(root: Path, activation, plan, now=_T1, *, head=None):
    import app.research.evaluator_identity as identity

    bundle = evaluator_bundle_sha256(REPO, decider_name="rsi_reentry_volume_confirmed")
    original = identity.assert_runtime_matches

    def _stub(**kwargs):
        # Die Bindung selbst hat eigene Tests; hier soll die Auswertung
        # geprueft werden, nicht der Checkout-Zustand des Testlaeufers.
        assert kwargs["research_code_sha"] == _CODE_SHA
        assert kwargs["evaluator_sha256"] == _EVAL_SHA
        assert bundle

    identity.assert_runtime_matches = _stub  # type: ignore[assignment]
    try:
        return run_sealed_evaluation(
            plan=plan, activation=activation, root=root, repo_root=REPO, now_utc=now
        )
    finally:
        identity.assert_runtime_matches = original  # type: ignore[assignment]


def test_the_sealed_evaluator_takes_no_research_parameters() -> None:
    import inspect

    parameters = inspect.signature(run_sealed_evaluation).parameters

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
        "candidate",
        "sensitivity_cost_bps",
        "kwargs",
    ):
        assert forbidden not in parameters, forbidden


def test_the_evaluation_uses_the_sealed_cost(tmp_path: Path) -> None:
    candidate = _candidate()
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)
    plan, _ = _decide(root, candidate, activation, _T1, rows=_rows())

    result = _evaluate(root, activation, plan)

    assert result.round_trip_cost_bps == 20.0
    assert result.economic_floor_bps == 5.0
    assert result.summary.mean_bps == pytest.approx((50.0 + 53.0 + 56.0) / 3 - 20.0)


def test_re_evaluating_reproduces_the_same_result(tmp_path: Path) -> None:
    candidate = _candidate()
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)
    plan, _ = _decide(root, candidate, activation, _T1, rows=_rows())

    first = _evaluate(root, activation, plan)
    second = _evaluate(root, activation, plan, now="2026-12-01T00:00:00+00:00")

    assert first.verdict == second.verdict
    assert first.summary.p_value == second.summary.p_value
    assert (
        len(
            load_verdicts(
                verdict_journal_path(root, read_active(root)),
                activation_sha256_value=read_active(root),
            )
        )
        == 1
    )


def test_only_a_recorded_verdict_closes(tmp_path: Path) -> None:
    candidate = _candidate()
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)
    plan, _ = _decide(root, candidate, activation, _T1, rows=_rows())
    _evaluate(root, activation, plan)

    closed, _ = _decide(root, candidate, activation, _AFTER_T2)

    assert closed.action == PLAN_CLOSED


def test_the_verdict_links_the_whole_chain_and_carries_its_decomposition(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)
    plan, _ = _decide(root, candidate, activation, _T1, rows=_rows())
    _evaluate(root, activation, plan)
    sha = read_active(root)

    record = load_verdicts(verdict_journal_path(root, sha), activation_sha256_value=sha)[0]

    assert record.evaluation_input_sha256 == plan.evaluation_input_sha256
    assert record.evaluator_sha256 == _EVAL_SHA
    assert record.decomposition["status"] == "DIAGNOSTIC_NON_GATING"
    assert record.decomposition["per_symbol_signals"]
    assert len(record.result_sha256) == 64


def test_the_evaluator_refuses_outside_a_decision_point(tmp_path: Path) -> None:
    activation = _activation()
    root = _tree(tmp_path, activation)

    with pytest.raises(SealedEvaluationError, match="darf nicht gewertet werden"):
        _evaluate(root, activation, CheckpointPlan(action=PLAN_WAIT, checkpoint="T1"))


def test_an_unregistered_hypothesis_cannot_be_resolved() -> None:
    with pytest.raises(SealedEvaluationError, match="nicht registriert"):
        resolve_decider("etwas_anderes")


# ── Verdikt-Journal: strikte Schema-Disziplin ───────────────────────────────


def _verdict_payload(tmp_path: Path, **overrides):
    candidate = _candidate()
    activation = _activation(candidate)
    root = _tree(tmp_path, activation)
    plan, _ = _decide(root, candidate, activation, _T1, rows=_rows())
    _evaluate(root, activation, plan)
    sha = read_active(root)
    path = verdict_journal_path(root, sha)
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    payload.update(overrides)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path, sha


@pytest.mark.parametrize(
    ("field_name", "value", "pattern"),
    [
        ("schema_version", "kai/prereg-verdict/v9", "schema_version"),
        ("checkpoint", "T3", "checkpoint"),
        ("verdict", "SIEHT_GUT_AUS", "verdict"),
        ("p_value", 1.5, "ausserhalb"),
        ("p_value", "0.03", "erwartet Zahl"),
        ("alpha", 0.0, "ausserhalb"),
        ("n_valid", True, "erwartet int"),
        ("n_valid", -1, "negativ"),
        ("evaluator_sha256", "kurz", "SHA-256"),
        ("recorded_at_utc", "2026-11-30T00:00:00", "zeitzonenlos"),
        ("decomposition", [1, 2], "kein Objekt"),
    ],
)
def test_every_invalid_verdict_field_aborts(
    tmp_path: Path, field_name: str, value: object, pattern: str
) -> None:
    """An der letzten Wahrheitsschicht keine implizite Python-Semantik."""
    from app.research.prereg_window_state import CheckpointJournalError

    path, sha = _verdict_payload(tmp_path, **{field_name: value})

    with pytest.raises(CheckpointJournalError, match=pattern):
        load_verdicts(path, activation_sha256_value=sha)


def test_a_tampered_verdict_is_detected(tmp_path: Path) -> None:
    from app.research.prereg_window_state import CheckpointJournalError

    path, sha = _verdict_payload(tmp_path, p_value=0.0001)

    with pytest.raises(CheckpointJournalError, match="nachtraeglich veraendert"):
        load_verdicts(path, activation_sha256_value=sha)


# ── Code-Identitaet ─────────────────────────────────────────────────────────


def test_the_bundle_hash_is_deterministic() -> None:
    a = evaluator_bundle_sha256(REPO, decider_name="rsi_reentry_volume_confirmed")
    b = evaluator_bundle_sha256(REPO, decider_name="rsi_reentry_volume_confirmed")

    assert a == b
    assert len(a) == 64


def test_a_wrong_git_head_aborts_before_any_number() -> None:
    """Ein Verdikt unter anderem Code ist kein schwaecheres — es ist ein anderes."""
    with pytest.raises(EvaluatorIdentityError, match="nicht der praeregistrierte"):
        assert_runtime_matches(
            repo_root=REPO,
            research_code_sha="a" * 40,
            evaluator_sha256="b" * 64,
            decider_name="rsi_reentry_volume_confirmed",
            head_provider=lambda _root: "f" * 40,
        )


def test_a_changed_evaluator_bundle_aborts() -> None:
    head = "a" * 40
    with pytest.raises(EvaluatorIdentityError, match="seit T0 veraendert"):
        assert_runtime_matches(
            repo_root=REPO,
            research_code_sha=head,
            evaluator_sha256="b" * 64,
            decider_name="rsi_reentry_volume_confirmed",
            head_provider=lambda _root: head,
        )


def test_the_matching_runtime_passes() -> None:
    """Gegenprobe — sonst waere die Bindung nur eine Mauer."""
    head = "a" * 40
    bundle = evaluator_bundle_sha256(REPO, decider_name="rsi_reentry_volume_confirmed")

    assert_runtime_matches(
        repo_root=REPO,
        research_code_sha=head,
        evaluator_sha256=bundle,
        decider_name="rsi_reentry_volume_confirmed",
        head_provider=lambda _root: head,
    )
