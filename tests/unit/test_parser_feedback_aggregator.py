"""Unit-Tests für parser_feedback_aggregator (P1 #10).

Verifiziert:
- ``scan_unparsed`` matched nur not_a_signal + text_len > threshold
- Window-cutoff filtert ältere Records aus
- Malformed JSON-Zeilen werden tolerant gedroppt
- Missing raw_log → empty list (no error)
- ``format_alert`` includes counter + samples + truncation
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import importlib.util
import sys

# Load the aggregator as a module (it lives in scripts/, not in app/)
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "parser_feedback_aggregator.py"
_spec = importlib.util.spec_from_file_location("parser_feedback_aggregator", _SCRIPT)
assert _spec is not None and _spec.loader is not None
pfa = importlib.util.module_from_spec(_spec)
sys.modules["parser_feedback_aggregator"] = pfa
_spec.loader.exec_module(pfa)


def _write_log(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def test_scan_returns_empty_list_when_file_missing(tmp_path: Path):
    result = pfa.scan_unparsed(tmp_path / "nonexistent.jsonl")
    assert result == []


def test_scan_returns_empty_list_when_no_records(tmp_path: Path):
    log = tmp_path / "raw.jsonl"
    log.write_text("")
    result = pfa.scan_unparsed(log)
    assert result == []


def test_scan_matches_only_not_a_signal_with_long_text(tmp_path: Path):
    now = datetime.now(UTC)
    log = tmp_path / "raw.jsonl"
    _write_log(log, [
        # Match: not_a_signal + long
        {"timestamp_utc": now.isoformat(), "outcome": "not_a_signal", "text_len": 100, "text_preview": "lang"},
        # Skip: parsed (signal worked)
        {"timestamp_utc": now.isoformat(), "outcome": "parsed", "text_len": 100},
        # Skip: too short
        {"timestamp_utc": now.isoformat(), "outcome": "not_a_signal", "text_len": 20},
        # Skip: target_completion (separate flow)
        {"timestamp_utc": now.isoformat(), "outcome": "target_completion", "text_len": 100},
    ])
    matches = pfa.scan_unparsed(log, now=now)
    assert len(matches) == 1
    assert matches[0]["text_preview"] == "lang"


def test_scan_window_cutoff_excludes_old_records(tmp_path: Path):
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    old_ts = (now - timedelta(minutes=90)).isoformat()
    fresh_ts = (now - timedelta(minutes=30)).isoformat()
    log = tmp_path / "raw.jsonl"
    _write_log(log, [
        {"timestamp_utc": old_ts, "outcome": "not_a_signal", "text_len": 100, "text_preview": "old"},
        {"timestamp_utc": fresh_ts, "outcome": "not_a_signal", "text_len": 100, "text_preview": "fresh"},
    ])
    matches = pfa.scan_unparsed(log, window_minutes=60, now=now)
    assert len(matches) == 1
    assert matches[0]["text_preview"] == "fresh"


def test_scan_tolerates_malformed_json_lines(tmp_path: Path):
    now = datetime.now(UTC)
    log = tmp_path / "raw.jsonl"
    log.write_text(
        "not-json-line\n"
        + json.dumps({"timestamp_utc": now.isoformat(), "outcome": "not_a_signal", "text_len": 100, "text_preview": "ok"})
        + "\n"
        + "{broken\n",
        encoding="utf-8",
    )
    matches = pfa.scan_unparsed(log, now=now)
    assert len(matches) == 1


def test_scan_skips_records_without_timestamp(tmp_path: Path):
    now = datetime.now(UTC)
    log = tmp_path / "raw.jsonl"
    _write_log(log, [
        {"outcome": "not_a_signal", "text_len": 100},
        {"timestamp_utc": now.isoformat(), "outcome": "not_a_signal", "text_len": 100},
    ])
    matches = pfa.scan_unparsed(log, now=now)
    assert len(matches) == 1


def test_format_alert_contains_count_and_samples():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    records = [
        {
            "timestamp_utc": now.isoformat(),
            "text_len": 120,
            "text_preview": "sample-1",
        },
        {
            "timestamp_utc": now.isoformat(),
            "text_len": 150,
            "text_preview": "sample-2",
        },
    ]
    text = pfa.format_alert(records, window_minutes=60)
    assert "2 nicht parsbare" in text
    assert "sample-1" in text
    assert "sample-2" in text
    assert "Parser-Regex prüfen" in text


def test_format_alert_caps_sample_count_at_max():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    records = [
        {"timestamp_utc": now.isoformat(), "text_len": 120, "text_preview": f"sample-{i}"}
        for i in range(10)
    ]
    text = pfa.format_alert(records)
    # Only first MAX_SAMPLES_IN_ALERT shown; rest summarised
    assert "10 nicht parsbare" in text
    assert "sample-0" in text
    assert "sample-4" in text  # 5th sample (0-indexed)
    assert "sample-5" not in text
    assert "+ 5 weitere" in text


def test_format_alert_truncates_long_preview():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    long_preview = "x" * 500
    records = [{"timestamp_utc": now.isoformat(), "text_len": 500, "text_preview": long_preview}]
    text = pfa.format_alert(records)
    # Preview must be truncated to PREVIEW_LIMIT (200) chars in the alert
    assert "x" * 500 not in text
    assert "x" * 200 in text
