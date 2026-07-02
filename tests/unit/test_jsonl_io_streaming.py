"""KAI-01: constant-memory streaming JSONL reader (``iter_jsonl_tolerant``).

Pins the streaming contract that replaces the ``read_text().splitlines()``
full-file slurp on the dashboard hot path: missing-file → empty, in-order
yield, blank/malformed/non-dict skip, and — crucially — that it never calls
``Path.read_text`` (the regression that would re-introduce the Pi OOM risk).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.storage.jsonl_io import iter_jsonl_tolerant


def _write(path: Path, rows: list[dict[str, int]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_iter_missing_file_is_empty(tmp_path: Path) -> None:
    assert list(iter_jsonl_tolerant(tmp_path / "nope.jsonl")) == []


def test_iter_yields_dicts_in_order(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    _write(path, [{"i": 0}, {"i": 1}, {"i": 2}])
    assert [row["i"] for row in iter_jsonl_tolerant(path)] == [0, 1, 2]


def test_iter_skips_blank_and_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "b.jsonl"
    path.write_text('{"ok": 1}\n\nnot-json\n{"ok": 2}\n', encoding="utf-8")
    assert [row["ok"] for row in iter_jsonl_tolerant(path)] == [1, 2]


def test_iter_skips_non_dict_when_dict_only(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text('{"ok": 1}\n[1, 2, 3]\n"str"\n{"ok": 2}\n', encoding="utf-8")
    assert [row["ok"] for row in iter_jsonl_tolerant(path)] == [1, 2]


def test_iter_keeps_non_dict_when_dict_only_false(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    path.write_text('{"ok": 1}\n[1, 2]\n', encoding="utf-8")
    assert list(iter_jsonl_tolerant(path, dict_only=False)) == [{"ok": 1}, [1, 2]]


def test_iter_does_not_slurp_whole_file_into_memory(tmp_path: Path, monkeypatch) -> None:
    """Regression guard: streaming must use open()/iteration, never read_text()."""
    path = tmp_path / "e.jsonl"
    _write(path, [{"i": i} for i in range(100)])

    def _boom(self: Path, *args: object, **kwargs: object) -> str:
        raise AssertionError("iter_jsonl_tolerant must not call Path.read_text()")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert sum(1 for _ in iter_jsonl_tolerant(path)) == 100
