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
