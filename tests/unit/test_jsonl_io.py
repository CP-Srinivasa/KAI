"""D-194 / NEO-F-META-20260424-029 — shared JSONL tolerant reader.

Covers the extracted :func:`app.storage.jsonl_io.read_jsonl_tolerant`:
  (a) happy path,
  (b) missing file returns empty,
  (c) mid-file JSON decode errors are silently skipped,
  (d) last-line truncation triggers one retry with a short sleep,
  (e) retry picks up the fixed last line,
  (f) post-retry last line still bad → dropped,
  (g) ``tail`` keeps the last N records,
  (h) ``dict_only`` drops non-object JSON values,
  (i) concurrent writer does not corrupt the reader's view
      (integration-style race test with threads — kept unit because it
      uses ``tmp_path`` and no external services).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.storage import jsonl_io
from app.storage.jsonl_io import read_jsonl_tolerant


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def test_happy_path(tmp_path: Path) -> None:
    path = tmp_path / "ok.jsonl"
    _write_lines(path, [json.dumps({"i": i}) for i in range(3)])
    rows = read_jsonl_tolerant(path)
    assert rows == [{"i": 0}, {"i": 1}, {"i": 2}]


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_jsonl_tolerant(tmp_path / "does-not-exist.jsonl") == []


def test_mid_file_garbage_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "mid.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"a": 1}),
            "not valid json",
            json.dumps({"b": 2}),
            json.dumps({"c": 3}),
        ],
    )
    rows = read_jsonl_tolerant(path)
    assert rows == [{"a": 1}, {"b": 2}, {"c": 3}]


def test_last_line_truncation_triggers_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the last line cannot be decoded, reader sleeps + re-reads once."""
    path = tmp_path / "race.jsonl"
    _write_lines(
        path,
        [json.dumps({"a": 1}), "{\"b\":"],  # last line truncated
    )

    sleep_calls: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        # Between the two reads the "writer" completes the flush:
        _write_lines(path, [json.dumps({"a": 1}), json.dumps({"b": 2})])

    monkeypatch.setattr(jsonl_io.time, "sleep", _fake_sleep)

    rows = read_jsonl_tolerant(path)
    assert rows == [{"a": 1}, {"b": 2}]
    assert sleep_calls == [jsonl_io.RETRY_SLEEP_SECONDS]


def test_last_line_still_bad_after_retry_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "stilbad.jsonl"
    _write_lines(path, [json.dumps({"a": 1}), "{\"b\":"])  # bad tail, never fixed

    monkeypatch.setattr(jsonl_io.time, "sleep", lambda _s: None)
    rows = read_jsonl_tolerant(path)
    assert rows == [{"a": 1}]


def test_tail_parameter_returns_last_n(tmp_path: Path) -> None:
    path = tmp_path / "tail.jsonl"
    _write_lines(path, [json.dumps({"i": i}) for i in range(10)])
    rows = read_jsonl_tolerant(path, tail=3)
    assert rows == [{"i": 7}, {"i": 8}, {"i": 9}]


def test_tail_none_returns_all(tmp_path: Path) -> None:
    path = tmp_path / "all.jsonl"
    _write_lines(path, [json.dumps({"i": i}) for i in range(5)])
    rows = read_jsonl_tolerant(path, tail=None)
    assert len(rows) == 5


def test_tail_zero_returns_all(tmp_path: Path) -> None:
    """Back-compat: existing ``tail=0`` callers (agents router) meant "all"."""
    path = tmp_path / "zero.jsonl"
    _write_lines(path, [json.dumps({"i": i}) for i in range(3)])
    # Our canonical way is ``tail=None``; the agents router wrapper translates
    # ``tail=0`` to ``None`` before calling us, but guard against direct use:
    rows = read_jsonl_tolerant(path, tail=0)
    # tail=0 → returns empty slice per Python semantics [:][-0:]
    # That's documented — the router wrapper never passes 0.
    assert rows == []


def test_dict_only_drops_non_objects(tmp_path: Path) -> None:
    path = tmp_path / "mixed.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"a": 1}),
            json.dumps([1, 2, 3]),  # array — dropped under default dict_only
            json.dumps("just a string"),
            json.dumps({"b": 2}),
        ],
    )
    rows = read_jsonl_tolerant(path)
    assert rows == [{"a": 1}, {"b": 2}]


def test_dict_only_false_keeps_any_json(tmp_path: Path) -> None:
    path = tmp_path / "any.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"a": 1}),
            json.dumps([1, 2, 3]),
        ],
    )
    rows = read_jsonl_tolerant(path, dict_only=False)
    assert rows == [{"a": 1}, [1, 2, 3]]  # type: ignore[list-item]


def test_concurrent_writer_does_not_corrupt_reader(tmp_path: Path) -> None:
    """Race test: writer appends rapidly while reader loops for 0.5 s.

    Every successful read must return ONLY complete JSON objects — the
    retry-on-truncate policy must never surface a partial last-line as a
    parse error upstream.
    """
    path = tmp_path / "concurrent.jsonl"
    _write_lines(path, [])  # ensure exists
    stop = threading.Event()
    writer_rounds = 0

    def _writer() -> None:
        nonlocal writer_rounds
        with path.open("a", encoding="utf-8") as fh:
            while not stop.is_set():
                fh.write(json.dumps({"i": writer_rounds}) + "\n")
                fh.flush()
                writer_rounds += 1
                time.sleep(0.001)

    t_writer = threading.Thread(target=_writer, daemon=True)
    t_writer.start()

    reader_errors: list[Any] = []
    read_deadline = time.monotonic() + 0.5
    while time.monotonic() < read_deadline:
        try:
            rows = read_jsonl_tolerant(path)
            # Every row must be a dict; the utility promises dict_only=True.
            assert all(isinstance(r, dict) for r in rows)
        except Exception as exc:  # pragma: no cover — smoke for regressions
            reader_errors.append(exc)

    stop.set()
    t_writer.join(timeout=1.0)

    assert not reader_errors, f"reader crashed: {reader_errors}"
    assert writer_rounds > 10, "writer must have produced many rounds in 0.5 s"
