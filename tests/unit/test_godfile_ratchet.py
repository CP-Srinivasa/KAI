"""God-file ratchet contract (Sprint S7, D-234): down-only, loud on growth."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import godfile_ratchet as gr  # noqa: E402


def _setup(tmp_path: Path, monkeypatch, *, file_lines: int, baseline: int) -> Path:
    target = tmp_path / "app" / "big.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n" * file_lines, encoding="utf-8")
    bl = tmp_path / "godfile_baseline.json"
    bl.write_text(json.dumps({"app/big.py": baseline}), encoding="utf-8")
    monkeypatch.setattr(gr, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gr, "BASELINE_PATH", bl)
    return bl


def test_growth_fails(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch, file_lines=101, baseline=100)
    assert gr.main([]) == 1


def test_at_baseline_passes(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch, file_lines=100, baseline=100)
    assert gr.main([]) == 0


def test_shrink_passes_and_update_tightens(tmp_path: Path, monkeypatch) -> None:
    bl = _setup(tmp_path, monkeypatch, file_lines=90, baseline=100)
    assert gr.main([]) == 0
    # Baseline unverändert ohne --update
    assert json.loads(bl.read_text(encoding="utf-8"))["app/big.py"] == 100
    assert gr.main(["--update"]) == 0
    assert json.loads(bl.read_text(encoding="utf-8"))["app/big.py"] == 90


def test_update_never_raises_baseline(tmp_path: Path, monkeypatch) -> None:
    bl = _setup(tmp_path, monkeypatch, file_lines=120, baseline=100)
    assert gr.main(["--update"]) == 1  # Wachstum bleibt Verstoß
    assert json.loads(bl.read_text(encoding="utf-8"))["app/big.py"] == 100


def test_missing_file_is_violation(tmp_path: Path, monkeypatch) -> None:
    bl = _setup(tmp_path, monkeypatch, file_lines=10, baseline=100)
    (tmp_path / "app" / "big.py").unlink()
    assert gr.main([]) == 1
    assert bl.exists()


# --- Abdeckung: der Ratchet muss die tatsaechlich groessten Dateien kennen ---
#
# Befund 2026-08-17: ``app/cli/commands/trading.py`` (3423 Zeilen) und
# ``app/api/routers/dashboard.py`` (3064) waren NICHT in der Baseline —
# obwohl trading.py die groesste Python-Datei des Repos ist, groesser als
# jedes der fuenf gelisteten God-Files. Der Ratchet liess sich damit
# erfuellen, indem Code aus einer gelisteten Datei in eine ungelistete
# verschoben wurde: die Baseline sinkt, das God-File wandert nur.
#
# Dieser Test bindet die Abdeckung an die Realitaet statt an eine Liste, die
# beim Wachsen des Repos still veraltet.

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Unterhalb dieser Groesse ist eine Datei kein God-File, sondern ein Modul.
_GODFILE_THRESHOLD = 1800


def _repo_line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def test_every_oversized_file_is_covered_by_the_baseline() -> None:
    baseline = json.loads(
        (_REPO_ROOT / "scripts" / "godfile_baseline.json").read_text(encoding="utf-8")
    )
    uncovered: list[tuple[str, int]] = []
    for path in sorted((_REPO_ROOT / "app").rglob("*.py")):
        lines = _repo_line_count(path)
        if lines < _GODFILE_THRESHOLD:
            continue
        rel = path.relative_to(_REPO_ROOT).as_posix()
        if rel not in baseline:
            uncovered.append((rel, lines))

    assert not uncovered, (
        "Diese Dateien sind God-File-gross, stehen aber nicht in "
        "scripts/godfile_baseline.json — Code laesst sich dorthin verschieben, "
        f"ohne dass der Ratchet es merkt: {uncovered}"
    )


def test_baseline_entries_all_exist() -> None:
    """Ein toter Baseline-Eintrag macht das Gate dauerhaft rot oder blind."""
    baseline = json.loads(
        (_REPO_ROOT / "scripts" / "godfile_baseline.json").read_text(encoding="utf-8")
    )
    missing = [rel for rel in baseline if not (_REPO_ROOT / rel).exists()]

    assert not missing, f"Baseline verweist auf nicht existierende Dateien: {missing}"
