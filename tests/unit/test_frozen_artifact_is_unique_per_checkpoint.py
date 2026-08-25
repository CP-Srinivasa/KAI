r"""Ein Checkpoint traegt genau EINEN eingefrorenen Datenschnitt.

``write_frozen_artifact`` revalidiert seit dem Haerten der Ablage eine bereits
vorhandene Datei byte-genau — richtig so, und es schliesst den Fall "gleicher
Hash, anderer Inhalt".

Der Fall daneben bleibt offen: zwei VERSCHIEDENE Eingaben fuer denselben
Checkpoint ergeben zwei verschiedene Dateinamen. ``target.exists()`` ist fuer
den zweiten Hash falsch, also faellt kein Konflikt auf, und beide Artefakte
liegen friedlich nebeneinander. Damit existiert im Verzeichnis eines
Checkpoints der physische Beleg zweier Einfrier-Versuche, ohne dass irgendwo
ein Fehler entstanden waere — und welcher davon das Verdikt getragen hat, haengt
allein daran, welcher Hash spaeter im Journal steht.

Die Einmaligkeit gehoert an den Ort, an dem sie entsteht: an den Schreibvorgang.
Ein zweiter Einfrier-Versuch ist ein Befund, kein Nebenprodukt.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.analysis.features.feature_matrix import FeatureRow
from app.research.frozen_dataset import FrozenRow, build_frozen_dataset
from app.research.frozen_input import (
    FrozenInputError,
    build_frozen_input,
    evaluation_input_sha256,
    write_frozen_artifact,
)
from app.research.prereg_candidate import activate, build_rsi_reentry_volume_candidate
from app.research.prereg_window import MaturityCounts

REPO = Path(__file__).resolve().parents[2]
_HOUR_MS = 3_600_000
_UNIVERSE_ARTIFACT = json.loads(
    (REPO / "docs" / "research" / "universe_rsi_reentry_v1.json").read_text(encoding="utf-8")
)
_UNIVERSE_SHA = _UNIVERSE_ARTIFACT["universe_sha256"]
_SYMBOLS = tuple(_UNIVERSE_ARTIFACT["canonical_universe"])

_CODE_SHA = "c" * 40
_EVAL_SHA = "e" * 64
_T0 = "2026-09-01T00:00:00+00:00"
_T1 = "2026-11-30T00:00:00+00:00"


def _candidate():
    base = build_rsi_reentry_volume_candidate(_UNIVERSE_SHA, len(_SYMBOLS))
    return replace(base, n_valid_min=1, cluster_min=1)


def _activation(candidate):
    return activate(
        candidate,
        t0_utc=_T0,
        research_code_sha=_CODE_SHA,
        evaluator_sha256=_EVAL_SHA,
        operator_approved=True,
    )


def _feature_row(hour: int) -> FeatureRow:
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


def _rows() -> dict[str, list[FrozenRow]]:
    out: dict[str, list[FrozenRow]] = {symbol: [] for symbol in _SYMBOLS}
    hour = 0
    for index in range(3):
        row = _feature_row(hour)
        exit_hour = hour + 4
        out[_SYMBOLS[index % 3]].append(
            FrozenRow(
                signal_timestamp_utc=row.timestamp_utc,
                label_exit_utc=(
                    f"2026-10-{1 + exit_hour // 24:02d}T{exit_hour % 24:02d}:00:00+00:00"
                ),
                features={k: v for k, v in asdict(row).items() if k != "timestamp_utc"},
                label_bps=50.0 + index * 3.0,
            )
        )
        hour += 40
    return out


def _input(counts: MaturityCounts):
    candidate = _candidate()
    dataset = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=_T0,
        cutoff_utc=_T1,
        sealed_symbols=_SYMBOLS,
        rows_by_symbol=_rows(),
        timeframe_ms=_HOUR_MS,
        horizon=4,
        # Diese Tests pruefen nicht die Abdeckung — sie haben eigene.
        min_coverage=0.0,
    )
    frozen = build_frozen_input(
        dataset=dataset,
        candidate=candidate,
        activation=_activation(candidate),
        sealed_universe_sha256=_UNIVERSE_SHA,
        sealed_symbols=_SYMBOLS,
        maturity_counts=counts,
    )
    return frozen, dataset


def test_a_second_different_freeze_for_the_same_checkpoint_is_refused(tmp_path: Path) -> None:
    """Der Kern: zwei Eingaben, ein Checkpoint — das darf nicht still gelingen."""
    directory = tmp_path / "frozen" / "T1"
    first, first_dataset = _input(MaturityCounts(n_valid=3, n_clusters=3))
    second, second_dataset = _input(MaturityCounts(n_valid=4, n_clusters=3))
    assert evaluation_input_sha256(first) != evaluation_input_sha256(second)

    written = write_frozen_artifact(directory, first, first_dataset)

    with pytest.raises(FrozenInputError, match="bereits ein anderes"):
        write_frozen_artifact(directory, second, second_dataset)

    # Das erste Artefakt bleibt unangetastet, und es bleibt das einzige.
    assert written.exists()
    assert [p.name for p in sorted(directory.glob("evaluation_input_*.json"))] == [written.name]


def test_writing_the_same_input_twice_stays_idempotent(tmp_path: Path) -> None:
    """Der Wiederanlauf darf nicht zum Befund werden — sonst waere die Haertung
    schlimmer als die Luecke."""
    directory = tmp_path / "frozen" / "T1"
    frozen, dataset = _input(MaturityCounts(n_valid=3, n_clusters=3))

    first = write_frozen_artifact(directory, frozen, dataset)
    second = write_frozen_artifact(directory, frozen, dataset)

    assert first == second
    assert len(list(directory.glob("evaluation_input_*.json"))) == 1


def test_a_different_checkpoint_directory_is_untouched(tmp_path: Path) -> None:
    """Die Einmaligkeit gilt je Checkpoint, nicht je Aktivierung: T1 und T2
    haben getrennte Verzeichnisse und getrennte Datenschnitte."""
    frozen, dataset = _input(MaturityCounts(n_valid=3, n_clusters=3))
    t1 = tmp_path / "frozen" / "T1"
    t2 = tmp_path / "frozen" / "T2"

    write_frozen_artifact(t1, frozen, dataset)
    write_frozen_artifact(t2, frozen, dataset)

    assert len(list(t1.glob("evaluation_input_*.json"))) == 1
    assert len(list(t2.glob("evaluation_input_*.json"))) == 1
