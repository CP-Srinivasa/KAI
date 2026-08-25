r"""Acht Befunde aus dem Deep Review — jeder mit dem Fall, den er verhindert.

Alle acht wurden am Code nachgestellt, bevor sie geschlossen wurden. Sie haben
eine gemeinsame Form: irgendwo hing eine Zusicherung an einer Absichtserklaerung
statt an einer Pruefung — ein Kommentar, der etwas behauptete, das niemand
nachrechnete.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.research.evaluator_identity import (
    EVALUATOR_BUNDLE_MODULES,
    EvaluatorIdentityError,
    assert_modules_load_from,
    assert_worktree_clean,
    evaluator_bundle_sha256,
)
from app.research.exclusive_lock import ExclusiveLockError, exclusive_lock
from app.research.frozen_dataset import (
    MIN_BAR_COVERAGE,
    FrozenDatasetError,
    FrozenRow,
    build_frozen_dataset,
)
from app.research.prereg_candidate import activate, build_rsi_reentry_volume_candidate
from app.research.prereg_evaluation import (
    SealedEvaluationError,
    VerdictRecord,
    decide_and_freeze,
    load_verdicts,
    plan_checkpoint,
    record_verdict,
)
from app.research.prereg_storage import (
    checkpoint_journal_path,
    initialise_activation,
    read_active,
)
from app.research.prereg_window_state import (
    CheckpointJournalError,
    CheckpointRecord,
    record_checkpoint,
)

REPO = Path(__file__).resolve().parents[2]
_HOUR_MS = 3_600_000
_UNIVERSE = json.loads(
    (REPO / "docs" / "research" / "universe_rsi_reentry_v1.json").read_text(encoding="utf-8")
)
_SYMBOLS = tuple(_UNIVERSE["canonical_universe"])
_T0 = "2026-09-01T00:00:00+00:00"
_T1 = "2026-09-02T00:00:00+00:00"
_DECIDER = "rsi_reentry_volume_confirmed"


def _candidate():
    return replace(
        build_rsi_reentry_volume_candidate(_UNIVERSE["universe_sha256"], len(_SYMBOLS)),
        t1_offset_days=1,
        t2_offset_days=2,
    )


def _activation():
    return activate(
        _candidate(),
        t0_utc=_T0,
        research_code_sha="c" * 40,
        evaluator_sha256="e" * 64,
        operator_approved=True,
    )


def _row(hour: int) -> FrozenRow:
    start = datetime.fromisoformat(_T0)
    return FrozenRow(
        signal_timestamp_utc=(start + timedelta(hours=hour)).isoformat(),
        label_exit_utc=(start + timedelta(hours=hour + 4)).isoformat(),
        features={"rsi_14": 50.0},
        label_bps=10.0,
    )


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)  # noqa: S603,S607
    (tmp_path / "a.txt").write_text("eins\n", encoding="utf-8")
    for args in (
        ["add", "-A"],
        ["-c", "user.email=t@x.invalid", "-c", "user.name=T", "commit", "-qm", "init"],
    ):
        subprocess.run(  # noqa: S603
            ["git", "-C", str(tmp_path), *args],  # noqa: S607
            check=True,
            capture_output=True,
        )
    return tmp_path


# ── 1. Die Kerzenlaenge gehoert in den Seal ─────────────────────────────────


def test_the_candle_length_is_inside_the_seal() -> None:
    """``interval_to_ms`` bestimmt Cluster-Grenzen und Haltefenster.

    Aus DEMSELBEN Artefakt kaeme mit anderen Millisekunden ein anderes Verdikt —
    die Datei lag trotzdem ausserhalb des Bundles.
    """
    assert "app/market_data/kline_windows.py" in EVALUATOR_BUNDLE_MODULES

    path = REPO / "app" / "market_data" / "kline_windows.py"
    original = path.read_text(encoding="utf-8")
    needle = '"1h": 60 * _MINUTE_MS,'
    assert needle in original, "Zuordnung umbenannt? Dann ist dieser Test veraltet."
    base = evaluator_bundle_sha256(REPO, decider_name=_DECIDER)
    path.write_text(original.replace(needle, '"1h": 120 * _MINUTE_MS,', 1), encoding="utf-8")
    try:
        changed = evaluator_bundle_sha256(REPO, decider_name=_DECIDER)
    finally:
        path.write_text(original, encoding="utf-8")

    assert changed != base, "1h -> 120min MUSS den Seal brechen"


# ── 2./3. Sauberer Checkout und geladene Module ─────────────────────────────


def test_a_dirty_worktree_is_refused(tmp_path: Path) -> None:
    """``git rev-parse HEAD`` sieht uncommittete Aenderungen NICHT.

    Ohne diese Pruefung waere ``research_code_sha == HEAD`` eine Aussage ueber
    die Historie statt ueber die laufenden Bytes — und der Producer-Code, der
    bewusst nicht im Evaluator-Bundle liegt, waere ueberhaupt nicht gebunden.
    """
    repo = _git_repo(tmp_path)
    assert_worktree_clean(repo)  # Gegenprobe: sauber ist sauber

    (repo / "a.txt").write_text("zwei\n", encoding="utf-8")

    with pytest.raises(EvaluatorIdentityError, match="unversionierte Aenderungen"):
        assert_worktree_clean(repo)


def test_untracked_files_are_not_a_finding(tmp_path: Path) -> None:
    """``artifacts/`` waechst im Normalbetrieb — das ist kein dreckiger Code."""
    repo = _git_repo(tmp_path)
    (repo / "neu.jsonl").write_text("{}\n", encoding="utf-8")

    assert_worktree_clean(repo)


def test_modules_must_come_from_the_hashed_checkout(tmp_path: Path) -> None:
    """Sonst wird Checkout B gehasht und Checkout A ausgefuehrt."""
    assert_modules_load_from(REPO)  # Gegenprobe

    with pytest.raises(EvaluatorIdentityError, match="geladen, gehasht wird"):
        assert_modules_load_from(tmp_path)


# ── 4. Die Frozen-Grenze mechanisch ─────────────────────────────────────────


def test_a_prefiltered_loader_is_refused() -> None:
    """Der Fall, den die Grenze bisher nur behauptet hat.

    ``frozen_rows_from_panel`` friert ohne Decider ein — aber der Lader davor
    konnte bereits vorgefiltert haben, und der Code konnte nicht beweisen, den
    vollstaendigen Schnitt bekommen zu haben. Nur die feuernden Zeilen waeren ein
    Bruchteil eines Prozents: die Regel feuert rund 1,5-mal pro Tag ueber 34
    Symbole, gegenueber 24 Kerzen pro Symbol und Tag.
    """
    with pytest.raises(FrozenDatasetError, match="vollstaendige OOS-Schnitt"):
        build_frozen_dataset(
            checkpoint="T1",
            t0_utc=_T0,
            cutoff_utc=_T1,
            sealed_symbols=_SYMBOLS,
            rows_by_symbol={_SYMBOLS[0]: [_row(3)]},
            timeframe_ms=_HOUR_MS,
            horizon=4,
        )


def test_the_full_cut_passes_and_records_its_coverage() -> None:
    """Gegenprobe — und die Abdeckung landet IM Datensatz, also im Hash.

    Sie ist damit Teil dessen, WAS eingefroren wurde, nicht eine Randnotiz
    darueber.
    """
    dataset = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS[:1],
        rows_by_symbol={_SYMBOLS[0]: [_row(hour) for hour in range(21)]},
        timeframe_ms=_HOUR_MS,
        horizon=4,
    )

    coverage = dataset.panels[0].coverage
    # 21, nicht 20: die Kerze bei exakt ``cutoff - horizon*dt`` hat ihren
    # Ausstieg genau auf dem Cutoff und ist damit vollstaendig beobachtet.
    assert coverage.bars_expected == 21
    assert coverage.bars_present == 21
    assert coverage.ratio >= MIN_BAR_COVERAGE


def test_a_real_provider_gap_still_passes() -> None:
    """Eine echte Luecke darf nicht wie eine Vorfilterung behandelt werden."""
    dataset = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS[:1],
        rows_by_symbol={_SYMBOLS[0]: [_row(hour) for hour in range(21) if hour != 7]},
        timeframe_ms=_HOUR_MS,
        horizon=4,
    )

    assert dataset.panels[0].coverage.bars_present == 20


# ── 5. T2 nur nach einer Verlaengerung an T1 ────────────────────────────────


def test_a_t2_decision_without_a_t1_extension_is_refused(tmp_path: Path) -> None:
    """Der Kommentar sagte es, geprueft wurde es nicht.

    Ein Journal mit T2 ohne T1 beschreibt ein Fenster, das nie eroeffnet wurde.
    """
    activation = _activation()
    root = tmp_path / "prereg"
    initialise_activation(root, activation)
    sha = read_active(root)
    record_checkpoint(
        checkpoint_journal_path(root, sha),
        CheckpointRecord(
            activation_sha256=sha,
            checkpoint="T2",
            action="INCONCLUSIVE_NOT_MATURE",
            mature=False,
            recorded_at_utc=activation.t2_utc,
            counts={"n_valid": 3},
        ),
    )

    with pytest.raises(SealedEvaluationError, match="nur nach einer"):
        plan_checkpoint(now_utc=activation.t2_utc, activation=activation, root=root)


# ── 6./7. Streng VOR dem Schreiben, unter Lock, kanonisch UTC ───────────────


def _verdict(sha: str, **overrides) -> VerdictRecord:
    values: dict[str, object] = {
        "schema_version": "kai/prereg-verdict/v1",
        "activation_sha256": sha,
        "checkpoint": "T1",
        "evaluation_input_sha256": "a" * 64,
        "dataset_sha256": "b" * 64,
        "evaluator_sha256": "e" * 64,
        "verdict": "NOT_MET",
        "n_valid": 3,
        "n_clusters": 3,
        "estimate_mean_net_bps": 1.0,
        "standard_error": 0.5,
        "t_statistic": 2.0,
        "df": 2,
        "p_value": 0.3,
        "alpha": 0.05,
        "economic_floor_bps": 5.0,
        "recorded_at_utc": "2026-09-02T00:00:00+00:00",
    }
    values.update(overrides)
    return VerdictRecord(**values)  # type: ignore[arg-type]


def test_an_invalid_verdict_never_reaches_the_journal(tmp_path: Path) -> None:
    """Vorher validierte nur der Leser — der Fehler fiel auf, als er unheilbar war.

    ``n_valid=True`` ist gueltiges Python (``bool`` ist eine Unterklasse von
    ``int``) und liess sich anhaengen. Beim naechsten Lesen war das append-only
    Journal dauerhaft rot: die Zeile stand drin und niemand konnte sie entfernen.
    """
    path = tmp_path / "verdicts.jsonl"

    with pytest.raises(CheckpointJournalError, match="erwartet int"):
        record_verdict(path, _verdict("f" * 64, n_valid=True))

    assert not path.exists(), "kein einziges Byte darf geschrieben worden sein"


def test_a_bad_timestamp_never_reaches_the_journal(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.jsonl"

    with pytest.raises((CheckpointJournalError, SealedEvaluationError)):
        record_verdict(path, _verdict("f" * 64, recorded_at_utc="gestern"))

    assert not path.exists()


def test_the_writer_normalises_to_utc(tmp_path: Path) -> None:
    """Zwei Schreibweisen desselben Augenblicks ergaeben zwei Bytes, also zwei Hashes."""
    path = tmp_path / "verdicts.jsonl"
    sha = "f" * 64

    record_verdict(path, _verdict(sha, recorded_at_utc="2026-09-02T02:00:00+02:00"))

    stored = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert stored["recorded_at_utc"] == "2026-09-02T00:00:00+00:00"
    assert load_verdicts(path, activation_sha256_value=sha)[0].checkpoint == "T1"


def test_an_offset_in_a_utc_field_is_refused_on_read(tmp_path: Path) -> None:
    """``+02:00`` ist derselbe Augenblick — in einem Feld auf ``_utc`` trotzdem falsch."""
    path = tmp_path / "verdicts.jsonl"
    sha = "f" * 64
    record_verdict(path, _verdict(sha))
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    payload["recorded_at_utc"] = "2026-09-02T02:00:00+02:00"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(CheckpointJournalError, match=r"erwartet \+00:00"):
        load_verdicts(path, activation_sha256_value=sha)


def test_the_checkpoint_writer_also_validates_first(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.jsonl"

    with pytest.raises(CheckpointJournalError):
        record_checkpoint(
            path,
            CheckpointRecord(
                activation_sha256="f" * 64,
                checkpoint="T1",
                action="EVALUATE",
                mature=True,
                recorded_at_utc="2026-09-02T00:00:00+00:00",
                counts={"n_valid": True},  # type: ignore[dict-item]
            ),
        )

    assert not path.exists()


def test_the_checkpoint_writer_normalises_to_utc(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.jsonl"

    record_checkpoint(
        path,
        CheckpointRecord(
            activation_sha256="f" * 64,
            checkpoint="T1",
            action="EXTEND_TO_T2",
            mature=False,
            recorded_at_utc="2026-09-02T02:00:00+02:00",
            counts={"n_valid": 3},
        ),
    )

    stored = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert stored["recorded_at_utc"] == "2026-09-02T00:00:00+00:00"


def test_the_journal_writers_take_a_lock(tmp_path: Path) -> None:
    """Ohne ihn sehen zwei Prozesse beide "kein Eintrag" und schreiben beide."""
    path = tmp_path / "verdicts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    with exclusive_lock(path.parent / f".{path.name}.lock", timeout_s=0.05):
        with pytest.raises(ExclusiveLockError, match="belegt"):
            record_verdict(path, _verdict("f" * 64))


def test_the_same_lock_serves_every_writer() -> None:
    """Zwei Implementierungen desselben Locks wuerden auseinanderlaufen."""
    from app.research import frozen_input

    source = Path(frozen_input.__file__).read_text(encoding="utf-8")

    assert "from app.research.exclusive_lock import" in source
    assert "O_EXCL" not in source, "die Mechanik gehoert an genau eine Stelle"


def test_the_runbook_separates_recovery_by_lock_kind() -> None:
    """Erwaehnung ist keine Semantik.

    Die erste Fassung nannte alle drei Locks und verlangte danach fuer jeden
    "Checkpoint aus dem Pfad" plus Artefaktzaehlung — beides gibt es bei den
    Journal-Locks gar nicht. Eine Recovery-Anleitung, die fuer zwei von drei
    Faellen das Falsche sagt, ist schlimmer als eine, die sie verschweigt.
    """
    runbook = (REPO / "docs" / "runbooks" / "freeze_lock_recovery.md").read_text(encoding="utf-8")

    for lock in (".freeze.lock", ".checkpoints.jsonl.lock", ".verdicts.jsonl.lock"):
        assert lock in runbook, f"{lock} fehlt im Recovery-Runbook"
    for kind in ("FROZEN_PUBLISH", "CHECKPOINT_JOURNAL", "VERDICT_JOURNAL"):
        assert kind in runbook, f"{kind} ist keine eigene Lock-Art im Runbook"
    assert "exclusive_lock.py" in runbook, "die gemeinsame Mechanik gehoert benannt"

    # Die Artefaktzaehlung darf NUR im Freeze-Teil stehen.
    freeze_part = runbook[runbook.index("## Teil B1") : runbook.index("## Teil B2")]
    journal_parts = runbook[runbook.index("## Teil B2") : runbook.index("## Entfernen")]
    assert "evaluation_input_*.json" in freeze_part
    assert "evaluation_input_*.json" not in journal_parts, (
        "Journal-Locks haben kein Checkpoint-Verzeichnis mit Artefakten"
    )
    assert "erfindet ihn" in runbook, "der fehlende Checkpoint im Pfad gehoert benannt"


def test_the_runbook_audits_intent_and_outcome_separately() -> None:
    """Ein Eintrag VOR dem Entfernen beweist die Absicht, nicht das Ergebnis.

    Scheitert das ``rm``, staende im Journal dasselbe wie nach einem geglueckten
    Lauf — und "Eintrag da, Lock weg" waere nicht mehr aufloesbar zwischen
    "dieser Versuch hat ihn entfernt" und "ein spaeterer Vorgang war es".
    """
    runbook = (REPO / "docs" / "runbooks" / "freeze_lock_recovery.md").read_text(encoding="utf-8")

    for event in ("RECOVERY_PREPARED", "RECOVERY_COMPLETED", "RECOVERY_FAILED"):
        assert event in runbook, f"{event} fehlt"
    for field in ("attempt_id", "lock_kind", "lock_path", "removed", "completed_at_utc"):
        assert field in runbook, f"Auditfeld {field} fehlt"
    assert "`null`" in runbook, "checkpoint muss bei Journal-Locks nullable sein"


# ── Der Producer-Guard laeuft VOR dem Lader ─────────────────────────────────


def test_a_failing_runtime_guard_stops_before_any_data_is_read(tmp_path: Path) -> None:
    """Der Angriff, den der Guard an seiner alten Stelle offen liess.

    Er stand nur im Auswertungspfad. Damit war moeglich::

        tracked Producer-Datei aendern -> rows_loader() liefert einen ANDEREN
        Datensatz -> dataset_sha256 bindet die falschen Bytes KORREKT ->
        Arbeitsbaum wieder saeubern -> die spaetere Auswertung meldet PASS.

    Der eingefrorene Schnitt waere beweisbar konsistent und trotzdem falsch.
    Geprueft wird deshalb nicht, dass der Guard existiert, sondern dass der
    Freeze-Pfad ihn aufruft, BEVOR er irgendetwas liest.
    """
    activation = _activation()
    root = tmp_path / "prereg"
    initialise_activation(root, activation)
    sha = read_active(root)
    calls: list[int] = []

    def _loader():
        calls.append(1)
        return {}

    def _failing_guard(**_kwargs):
        raise EvaluatorIdentityError("der Checkout traegt unversionierte Aenderungen")

    with pytest.raises(EvaluatorIdentityError):
        decide_and_freeze(
            now_utc=activation.t1_utc,
            candidate=_candidate(),
            activation=activation,
            root=root,
            repo_root=REPO,
            rows_loader=_loader,
            runtime_guard=_failing_guard,
        )

    assert calls == [], "der Lader wurde trotz gescheitertem Guard aufgerufen"
    assert list((root / sha / "frozen" / "T1").glob("*.json")) == []
    assert (root / sha / "checkpoints.jsonl").read_text(encoding="utf-8") == ""
    assert (root / sha / "verdicts.jsonl").read_text(encoding="utf-8") == ""


def test_the_guard_is_the_default_not_an_opt_in() -> None:
    """Ein Guard, den man anfordern muss, ist keiner.

    Der Parameter existiert nur, damit Tests ihn gezielt scheitern lassen
    koennen — die Vorgabe ist die echte Pruefung.
    """
    import inspect

    from app.research.evaluator_identity import assert_runtime_matches
    from app.research.prereg_evaluation import decide_and_freeze as production

    assert (
        inspect.signature(production).parameters["runtime_guard"].default is assert_runtime_matches
    )


# ── Abdeckung ist eine Aussage ueber das RASTER ─────────────────────────────


def test_twenty_timestamps_off_the_grid_are_not_twenty_bars() -> None:
    """Der Gegenbeweis zur ersten Fassung.

    Sie zaehlte verschiedene Zeitstempel. Zwanzig Werte im Fuenfminutentakt
    ergaeben damit volle Abdeckung eines Zwanzig-Stunden-Rasters, obwohl fast
    jede Stunde fehlt.
    """
    start = datetime.fromisoformat(_T0)
    five_minute_ticks = [
        FrozenRow(
            signal_timestamp_utc=(start + timedelta(minutes=5 * i)).isoformat(),
            label_exit_utc=(start + timedelta(minutes=5 * i, hours=4)).isoformat(),
            features={"rsi_14": 50.0},
            label_bps=1.0,
        )
        for i in range(20)
    ]

    with pytest.raises(FrozenDatasetError, match="nicht auf dem"):
        build_frozen_dataset(
            checkpoint="T1",
            t0_utc=_T0,
            cutoff_utc=_T1,
            sealed_symbols=_SYMBOLS[:1],
            rows_by_symbol={_SYMBOLS[0]: five_minute_ticks},
            timeframe_ms=_HOUR_MS,
            horizon=4,
        )


def test_a_label_over_the_wrong_horizon_is_refused() -> None:
    """``signal < exit <= cutoff`` genuegt nicht.

    Ohne diese Pruefung koennte eine Zeile ein Label ueber eine ganz andere
    Haltedauer tragen und trotzdem korrekt gehasht werden — der Datensatz waere
    in sich konsistent und wuerde etwas anderes messen als versiegelt.
    """
    start = datetime.fromisoformat(_T0)
    wrong = FrozenRow(
        signal_timestamp_utc=start.isoformat(),
        label_exit_utc=(start + timedelta(hours=6)).isoformat(),
        features={"rsi_14": 50.0},
        label_bps=1.0,
    )

    with pytest.raises(FrozenDatasetError, match="versiegelt sind 4"):
        build_frozen_dataset(
            checkpoint="T1",
            t0_utc=_T0,
            cutoff_utc=_T1,
            sealed_symbols=_SYMBOLS[:1],
            rows_by_symbol={_SYMBOLS[0]: [wrong]},
            timeframe_ms=_HOUR_MS,
            horizon=4,
        )


def test_the_same_slot_twice_is_refused() -> None:
    """Ein Slot, eine Zeile — sonst liesse sich Abdeckung durch Duplikate erkaufen."""
    rows = [_row(hour) for hour in range(21)] + [_row(0)]

    with pytest.raises(FrozenDatasetError, match="kommt zweimal vor"):
        build_frozen_dataset(
            checkpoint="T1",
            t0_utc=_T0,
            cutoff_utc=_T1,
            sealed_symbols=_SYMBOLS[:1],
            rows_by_symbol={_SYMBOLS[0]: rows},
            timeframe_ms=_HOUR_MS,
            horizon=4,
        )


def test_the_runbook_requires_parent_directory_fsync_on_first_write() -> None:
    """``fsync`` auf den Deskriptor sichert den INHALT, nicht den Verzeichniseintrag.

    Entsteht ``lock_recovery.jsonl`` erst in diesem Lauf, liegt sein Eintrag im
    Verzeichnis noch im Cache: nach einem Stromausfall existiert die Datei gar
    nicht, obwohl ihr Inhalt geschrieben war. Betroffen ist ausgerechnet der
    erste ``RECOVERY_PREPARED`` einer Aktivierung — die einzige Spur einer
    Intervention, deren Ausgang unbekannt blieb.

    Der Code macht das an drei Stellen bereits so; hier steht es fuer die
    Handarbeit, und ohne diesen Ratchet faellt es beim naechsten Umbau des
    Runbooks lautlos weg.
    """
    runbook = (REPO / "docs" / "runbooks" / "freeze_lock_recovery.md").read_text(encoding="utf-8")
    section = runbook[runbook.index("## Entfernen") :]

    assert "fsync(elternverzeichnis)" in section, (
        "der Parent-Directory-fsync beim erstmaligen Anlegen fehlt"
    )
    assert "NEU angelegt" in section, "die Bedingung dafuer muss benannt sein"
    # Beide Phasen brauchen weiterhin ihren eigenen fsync.
    assert section.count("fsync(datei)") >= 2, (
        "PREPARED und COMPLETED/FAILED brauchen jeweils flush + fsync"
    )


def test_the_code_actually_fsyncs_the_parent_on_creation() -> None:
    """Gegenprobe im Code — das Runbook beschreibt keine Wunschvorstellung.

    Alle drei Schreiber der Wahrheitsschicht muessen es so machen, sonst
    beschriebe das Runbook eine Sorgfalt, die die Maschine nicht aufbringt.
    """
    for relative in (
        "app/research/prereg_window_state.py",
        "app/research/prereg_evaluation.py",
        "app/research/frozen_input.py",
        "app/research/prereg_storage.py",
    ):
        source = (REPO / relative).read_text(encoding="utf-8")
        assert "O_RDONLY" in source or "_fsync_dir" in source or "_fsync_directory" in source, (
            f"{relative} sichert den Verzeichniseintrag nicht"
        )
