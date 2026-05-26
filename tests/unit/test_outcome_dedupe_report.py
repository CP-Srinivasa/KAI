"""Unit tests for app.observability.outcome_dedupe_report."""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.outcome_dedupe_report import build_outcome_dedupe_report


def _write_outcomes(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_missing_file_returns_zero_report(tmp_path: Path) -> None:
    report = build_outcome_dedupe_report(audit_path=tmp_path / "missing.jsonl")
    assert report.raw_total == 0
    assert report.deduped_total == 0


def test_dedupe_keeps_latest_per_document_id(tmp_path: Path) -> None:
    """Multi-Window writes one inconclusive row per window pass; once
    a later window flips to hit, the operator must see hit-not-the-
    old-inconclusive."""
    path = tmp_path / "alert_outcomes.jsonl"
    rows = [
        {"document_id": "d1", "outcome": "inconclusive"},  # 1h window
        {"document_id": "d1", "outcome": "inconclusive"},  # 4h window
        {"document_id": "d1", "outcome": "hit"},  # 24h window: resolved
        {"document_id": "d2", "outcome": "miss"},
        {"document_id": "d3", "outcome": "inconclusive"},
        {"document_id": "d3", "outcome": "inconclusive"},  # stays inconclusive
    ]
    _write_outcomes(path, rows)

    report = build_outcome_dedupe_report(audit_path=path)
    assert report.raw_total == 6
    assert report.raw_hit == 1
    assert report.raw_miss == 1
    assert report.raw_inconclusive == 4
    assert report.deduped_total == 3
    assert report.deduped_hit == 1  # d1 resolved
    assert report.deduped_miss == 1
    assert report.deduped_inconclusive == 1  # d3 still inconclusive (1 row)
    # d1 had 2 inconclusive rows superseded by hit -> 2 dropped.
    # d3 had 2 inconclusive rows, final = inconclusive -> 1 dropped (extras).
    assert report.dropped_inconclusive_dupes == 3


def test_skips_rows_without_document_id(tmp_path: Path) -> None:
    path = tmp_path / "alert_outcomes.jsonl"
    rows = [
        {"document_id": "d1", "outcome": "hit"},
        {"outcome": "miss"},  # no document_id -> only counts in raw
        {"document_id": "", "outcome": "miss"},  # empty -> raw only
    ]
    _write_outcomes(path, rows)

    report = build_outcome_dedupe_report(audit_path=path)
    assert report.raw_total == 3
    assert report.raw_miss == 2
    assert report.deduped_total == 1


def test_precision_strings_render(tmp_path: Path) -> None:
    path = tmp_path / "alert_outcomes.jsonl"
    rows = [{"document_id": f"d{i}", "outcome": "hit"} for i in range(7)] + [
        {"document_id": f"d{i + 7}", "outcome": "miss"} for i in range(3)
    ]
    _write_outcomes(path, rows)
    report = build_outcome_dedupe_report(audit_path=path)
    assert "70.0%" in report.raw_precision_str
    assert "70.0%" in report.deduped_precision_str
