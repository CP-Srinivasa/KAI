"""Tests for blocked-alert audit trail (D-148 recall proxy)."""

from __future__ import annotations

import json
from pathlib import Path

from app.alerts.blocked_audit import (
    BLOCKED_ALERTS_JSONL_FILENAME,
    BlockedAlertRecord,
    BlockedOutcomeAnnotation,
    append_blocked_alert,
    append_blocked_outcome,
    load_blocked_alerts,
    load_blocked_outcomes,
)


def test_blocked_alert_record_minimal_serialisation():
    record = BlockedAlertRecord(
        document_id="doc-1",
        block_reason="low_precision_source",
    )
    data = record.to_json_dict()
    assert data["document_id"] == "doc-1"
    assert data["block_reason"] == "low_precision_source"
    assert "blocked_at" in data
    # Optional fields with default None / [] are omitted.
    assert "sentiment_label" not in data
    assert "blocked_assets" not in data
    assert "priority" not in data


def test_blocked_alert_record_full_serialisation():
    record = BlockedAlertRecord(
        document_id="doc-2",
        block_reason="weak_directional_signal",
        blocked_at="2026-04-18T10:00:00+00:00",
        sentiment_label="bullish",
        blocked_assets=["bitcoin", "ethereum"],
        priority=7,
        actionable=True,
        title_hash="abc123",
        normalized_title="bitcoin surges",
        source_name="decrypt",
    )
    data = record.to_json_dict()
    assert data["sentiment_label"] == "bullish"
    assert data["blocked_assets"] == ["bitcoin", "ethereum"]
    assert data["priority"] == 7
    assert data["actionable"] is True
    assert data["title_hash"] == "abc123"
    assert data["source_name"] == "decrypt"


def test_append_and_load_blocked_alerts_roundtrip(tmp_path: Path):
    record1 = BlockedAlertRecord(
        document_id="doc-a",
        block_reason="reactive_price_narrative",
        blocked_at="2026-04-18T10:00:00+00:00",
        sentiment_label="bearish",
        blocked_assets=["bitcoin"],
        source_name="cointelegraph",
    )
    record2 = BlockedAlertRecord(
        document_id="doc-b",
        block_reason="low_precision_source",
        blocked_at="2026-04-18T10:05:00+00:00",
        sentiment_label="bullish",
        blocked_assets=["ethereum"],
        source_name="unknown",
    )
    append_blocked_alert(record1, tmp_path)
    append_blocked_alert(record2, tmp_path)

    loaded = load_blocked_alerts(tmp_path)
    assert len(loaded) == 2
    assert loaded[0].document_id == "doc-a"
    assert loaded[0].block_reason == "reactive_price_narrative"
    assert loaded[0].blocked_assets == ["bitcoin"]
    assert loaded[1].document_id == "doc-b"
    assert loaded[1].source_name == "unknown"


def test_append_blocked_alert_accepts_file_path(tmp_path: Path):
    file_path = tmp_path / "custom_blocked.jsonl"
    record = BlockedAlertRecord(document_id="doc-x", block_reason="bearish_directional_disabled")
    append_blocked_alert(record, file_path)
    assert file_path.exists()
    line = file_path.read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert data["document_id"] == "doc-x"


def test_load_blocked_alerts_missing_file_returns_empty(tmp_path: Path):
    assert load_blocked_alerts(tmp_path) == []


def test_load_blocked_alerts_skips_malformed_lines(tmp_path: Path):
    target = tmp_path / BLOCKED_ALERTS_JSONL_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        '{"document_id": "ok", "block_reason": "x", "blocked_at": "2026-04-18T10:00:00+00:00"}\n'
        "not-json\n"
        '{"missing": "required-fields"}\n',
        encoding="utf-8",
    )
    loaded = load_blocked_alerts(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].document_id == "ok"


def test_append_blocked_alert_appends_not_overwrites(tmp_path: Path):
    """Ensure JSONL append semantics — re-appends do not truncate existing lines."""
    record_a = BlockedAlertRecord(document_id="a", block_reason="r1")
    record_b = BlockedAlertRecord(document_id="b", block_reason="r2")
    append_blocked_alert(record_a, tmp_path)
    append_blocked_alert(record_b, tmp_path)
    loaded = load_blocked_alerts(tmp_path)
    assert [r.document_id for r in loaded] == ["a", "b"]


# F3-V-0 (2026-05-24) — directional_confidence persistence in blocked_alerts.jsonl.


def test_blocked_alert_record_persists_directional_confidence(tmp_path: Path):
    """F3-V-0: directional_confidence survives a write+read round-trip."""
    record = BlockedAlertRecord(
        document_id="d_conf",
        block_reason="low_directional_confidence",
        sentiment_label="bullish",
        priority=10,
        directional_confidence=0.72,
    )
    append_blocked_alert(record, tmp_path)
    loaded = load_blocked_alerts(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].directional_confidence == 0.72


def test_blocked_alert_record_no_confidence_serialises_without_field(tmp_path: Path):
    """F3-V-0: omitting directional_confidence keeps the field out of the JSON."""
    record = BlockedAlertRecord(
        document_id="d_no_conf",
        block_reason="reactive_price_narrative",
    )
    append_blocked_alert(record, tmp_path)
    text = (tmp_path / BLOCKED_ALERTS_JSONL_FILENAME).read_text(encoding="utf-8")
    assert "directional_confidence" not in text


def test_blocked_alert_record_load_legacy_record_without_confidence(tmp_path: Path):
    """F3-V-0: pre-V-0 records (without directional_confidence) load cleanly."""
    target = tmp_path / BLOCKED_ALERTS_JSONL_FILENAME
    target.write_text(
        '{"document_id": "legacy", "block_reason": "x", '
        '"blocked_at": "2026-05-01T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    loaded = load_blocked_alerts(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].directional_confidence is None


def test_blocked_outcome_persists_gate_context(tmp_path: Path):
    """D-227: blocked outcomes must be machine-filterable by gate population."""
    annotation = BlockedOutcomeAnnotation(
        document_id="doc-low-conf",
        outcome="hit",
        asset="BTC/USDT",
        block_reason="low_directional_confidence",
        sentiment_label="bullish",
        directional_confidence=0.72,
        source_name="cointelegraph",
    )
    append_blocked_outcome(annotation, tmp_path)

    loaded = load_blocked_outcomes(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].block_reason == "low_directional_confidence"
    assert loaded[0].sentiment_label == "bullish"
    assert loaded[0].directional_confidence == 0.72
    assert loaded[0].source_name == "cointelegraph"
