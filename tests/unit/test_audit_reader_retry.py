"""NEO-P-002 (D): Reader-Retry bei JSONDecodeError auf letzter Zeile.

Deckt die drei toleranten Reader in ``app.alerts.audit`` ab:
- ``load_alert_audits``
- ``iter_alert_audit_document_ids``
- ``load_outcome_annotations``

Szenarien:
- Normalfall unverändert (Retry nicht nötig)
- Mid-file-Decode-Error: weiterhin silent skipped (keine Fehlerverdeckungs-Änderung)
- Half-written last line: erster Read-Versuch sieht sie partiell, Retry sieht
  sie vollständig — Record wird geliefert
- Permanent kaputte last line: gedropt nach Retry
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.alerts.audit import (
    _read_jsonl_tolerant,
    iter_alert_audit_document_ids,
    load_alert_audits,
    load_outcome_annotations,
)
from app.storage import jsonl_io as jsonl_io_mod

_AUDIT_LINE_A = (
    '{"document_id": "a", "channel": "telegram", "message_id": null, '
    '"is_digest": false, "dispatched_at": "2026-04-20T00:00:00+00:00"}'
)
_AUDIT_LINE_B = (
    '{"document_id": "b", "channel": "email", "message_id": null, '
    '"is_digest": false, "dispatched_at": "2026-04-20T00:00:01+00:00"}'
)
_AUDIT_LINE_B_PARTIAL = '{"document_id": "b", "channel":'
_OUTCOME_LINE_A = '{"document_id": "a", "outcome": "hit"}'
_OUTCOME_LINE_B = '{"document_id": "b", "outcome": "miss"}'


@pytest.fixture(autouse=True)
def _silence_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: skip the retry wait in tests. Individual tests can override
    via a side-effecting sleep to simulate a writer finishing mid-race."""
    monkeypatch.setattr(jsonl_io_mod.time, "sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# _read_jsonl_tolerant — direct behaviour
# ---------------------------------------------------------------------------


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _read_jsonl_tolerant(tmp_path / "nope.jsonl") == []


def test_clean_file_parses_all_rows(tmp_path: Path) -> None:
    p = tmp_path / "a.jsonl"
    p.write_text(f"{_AUDIT_LINE_A}\n{_AUDIT_LINE_B}\n", encoding="utf-8")
    records = _read_jsonl_tolerant(p)
    assert [r["document_id"] for r in records] == ["a", "b"]


def test_mid_file_decode_error_silently_skipped(tmp_path: Path) -> None:
    """Middle garbage must not abort the read — legacy policy."""
    p = tmp_path / "a.jsonl"
    p.write_text(f"{_AUDIT_LINE_A}\nnot-json\n{_AUDIT_LINE_B}\n", encoding="utf-8")
    records = _read_jsonl_tolerant(p)
    # only rows A and B survive; middle row is dropped (no retry triggered
    # because the LAST line parsed fine)
    assert [r["document_id"] for r in records] == ["a", "b"]


def test_last_line_retry_recovers_complete_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half-written last line → retry after 'sleep' sees the completed line."""
    p = tmp_path / "a.jsonl"
    p.write_text(f"{_AUDIT_LINE_A}\n{_AUDIT_LINE_B_PARTIAL}", encoding="utf-8")

    sleep_calls = {"n": 0}

    def _finishing_writer(_s: float) -> None:
        sleep_calls["n"] += 1
        # Simulate the writer finishing its append during our sleep.
        p.write_text(f"{_AUDIT_LINE_A}\n{_AUDIT_LINE_B}\n", encoding="utf-8")

    monkeypatch.setattr(jsonl_io_mod.time, "sleep", _finishing_writer)

    records = _read_jsonl_tolerant(p)
    assert sleep_calls["n"] == 1
    assert [r["document_id"] for r in records] == ["a", "b"]


def test_last_line_still_broken_after_retry_is_dropped(tmp_path: Path) -> None:
    """If the last line is genuinely corrupt, the retry drops it — quiet data
    loss is better than crashing the pipeline on one malformed row."""
    p = tmp_path / "a.jsonl"
    p.write_text(f"{_AUDIT_LINE_A}\n{_AUDIT_LINE_B_PARTIAL}", encoding="utf-8")
    records = _read_jsonl_tolerant(p)
    assert [r["document_id"] for r in records] == ["a"]


def test_clean_file_does_not_invoke_sleep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy-path reads must not pay the 100 ms penalty."""
    p = tmp_path / "a.jsonl"
    p.write_text(f"{_AUDIT_LINE_A}\n{_AUDIT_LINE_B}\n", encoding="utf-8")

    sleep_calls = {"n": 0}
    monkeypatch.setattr(
        jsonl_io_mod.time, "sleep", lambda _s: sleep_calls.__setitem__("n", sleep_calls["n"] + 1)
    )

    _read_jsonl_tolerant(p)
    assert sleep_calls["n"] == 0


# ---------------------------------------------------------------------------
# Public API — retry integrates at each call site
# ---------------------------------------------------------------------------


def test_load_alert_audits_recovers_half_written_last_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "alert_audit.jsonl"
    p.write_text(f"{_AUDIT_LINE_A}\n{_AUDIT_LINE_B_PARTIAL}", encoding="utf-8")

    def _finishing_writer(_s: float) -> None:
        p.write_text(f"{_AUDIT_LINE_A}\n{_AUDIT_LINE_B}\n", encoding="utf-8")

    monkeypatch.setattr(jsonl_io_mod.time, "sleep", _finishing_writer)

    records = load_alert_audits(tmp_path)
    assert [r.document_id for r in records] == ["a", "b"]


def test_iter_alert_audit_document_ids_recovers_half_written_last_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "alert_audit.jsonl"
    p.write_text(f"{_AUDIT_LINE_A}\n{_AUDIT_LINE_B_PARTIAL}", encoding="utf-8")

    def _finishing_writer(_s: float) -> None:
        p.write_text(f"{_AUDIT_LINE_A}\n{_AUDIT_LINE_B}\n", encoding="utf-8")

    monkeypatch.setattr(jsonl_io_mod.time, "sleep", _finishing_writer)

    ids = iter_alert_audit_document_ids(tmp_path)
    assert ids == {"a", "b"}


def test_load_outcome_annotations_recovers_half_written_last_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "alert_outcomes.jsonl"
    partial = '{"document_id": "b", "outcome":'
    p.write_text(f"{_OUTCOME_LINE_A}\n{partial}", encoding="utf-8")

    def _finishing_writer(_s: float) -> None:
        p.write_text(f"{_OUTCOME_LINE_A}\n{_OUTCOME_LINE_B}\n", encoding="utf-8")

    monkeypatch.setattr(jsonl_io_mod.time, "sleep", _finishing_writer)

    annotations = load_outcome_annotations(tmp_path)
    assert [a.document_id for a in annotations] == ["a", "b"]
