r"""Zwei gleichzeitige Einfrier-Versuche: genau einer darf durchkommen.

Der sequentielle Fall ist seit #761 geschlossen — wer NACH einem fremden
Artefakt schreibt, sieht es und bricht ab. Der parallele Fall ist es nicht:

    Writer A                     Writer B
    --------                     --------
    glob() -> leer               glob() -> leer
    target A fehlt               target B fehlt
    write A                      write B

Beide Pruefungen sehen ein leeres Verzeichnis, beide schreiben, und danach
liegen zwei Datenschnitte fuer denselben Checkpoint nebeneinander — genau der
Zustand, den der Guard verhindern sollte. Zwischen Pruefen und Schreiben liegt
kein exklusiver Abschnitt; das ist TOCTOU, derselbe Fehlertyp, den #756 hinter
einen Lock geschoben hat.

Der Test bringt beide Threads per Barriere bis unmittelbar vor den kritischen
Abschnitt und laeuft mehrere Runden, damit das Rennen tatsaechlich stattfindet
und nicht zufaellig serialisiert.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, replace
from pathlib import Path

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
        activation=activate(
            candidate,
            t0_utc=_T0,
            research_code_sha=_CODE_SHA,
            evaluator_sha256=_EVAL_SHA,
            operator_approved=True,
        ),
        sealed_universe_sha256=_UNIVERSE_SHA,
        sealed_symbols=_SYMBOLS,
        maturity_counts=counts,
    )
    return frozen, dataset


def _race(directory: Path, payloads) -> tuple[int, list[BaseException]]:
    """Beide Aufrufe treffen sich an der Barriere und starten gemeinsam."""
    barrier = threading.Barrier(len(payloads))
    committed = 0
    failures: list[BaseException] = []
    lock = threading.Lock()

    def _worker(frozen, dataset) -> None:
        nonlocal committed
        barrier.wait()
        try:
            write_frozen_artifact(directory, frozen, dataset)
        except BaseException as exc:  # noqa: BLE001 - der Test klassifiziert selbst
            with lock:
                failures.append(exc)
        else:
            with lock:
                committed += 1

    threads = [threading.Thread(target=_worker, args=pair) for pair in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return committed, failures


def test_two_concurrent_different_freezes_leave_exactly_one(tmp_path: Path) -> None:
    """Genau einer committet, der andere endet fail-closed — jede Runde."""
    first = _input(MaturityCounts(n_valid=3, n_clusters=3))
    second = _input(MaturityCounts(n_valid=4, n_clusters=3))
    assert evaluation_input_sha256(first[0]) != evaluation_input_sha256(second[0])

    for round_number in range(8):
        directory = tmp_path / f"round{round_number}" / "T1"

        committed, failures = _race(directory, [first, second])

        artifacts = sorted(directory.glob("evaluation_input_*.json"))
        assert committed == 1, f"Runde {round_number}: {committed} Schreiber kamen durch"
        assert len(artifacts) == 1, (
            f"Runde {round_number}: {[p.name for p in artifacts]} — ein Checkpoint traegt "
            "genau einen Datenschnitt"
        )
        assert len(failures) == 1 and isinstance(failures[0], FrozenInputError), (
            f"Runde {round_number}: der unterlegene Schreiber muss FrozenInputError sehen, "
            f"nicht {failures!r}"
        )
        # Kein Rest: der Lock darf das Verzeichnis nicht dauerhaft belegen.
        assert not (directory / ".freeze.lock").exists()


def test_two_concurrent_identical_freezes_are_both_idempotent(tmp_path: Path) -> None:
    """Zwei Wiederanlaeufe desselben Inputs sind kein Konflikt.

    Sonst waere die Haertung schlimmer als die Luecke: ein doppelt gestarteter
    Wiederanlauf wuerde als Beschaedigung gemeldet.
    """
    payload = _input(MaturityCounts(n_valid=3, n_clusters=3))

    for round_number in range(8):
        directory = tmp_path / f"same{round_number}" / "T1"

        committed, failures = _race(directory, [payload, payload])

        assert failures == [], f"Runde {round_number}: {failures!r}"
        assert committed == 2
        assert len(list(directory.glob("evaluation_input_*.json"))) == 1
