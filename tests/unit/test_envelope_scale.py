"""Envelope tests for scale_factor wiring (P1 #8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingestion import telegram_channel_envelope as envelope_mod
from app.ingestion.telegram_channel_parser import ParsedSignal


def _make_parsed(entry_value: float = 32450.0) -> ParsedSignal:
    return ParsedSignal(
        symbol="SWARMSUSDT",
        display_symbol="SWARMS/USDT",
        side="buy",
        direction="long",
        entry_type="at",
        entry_value=entry_value,
        entry_min=None,
        entry_max=None,
        targets=[33000.0, 34000.0],
        stop_loss=31000.0,
        leverage=10,
        margin_pct=None,
        exchange_scope=("bybit",),
        raw_text="SWARMS/USDT long entry 32450 sl 31000 tp1 33000",
    )


def test_build_envelope_record_scale_none_marks_unknown():
    rec = envelope_mod.build_envelope_record(_make_parsed(), scale_factor=None)
    payload = rec["payload"]
    assert payload.get("scale_unknown") is True
    assert "scale_resolved_at_emit" not in payload
    # Values must remain raw — bridge will re-resolve later.
    assert payload["entry_value"] == 32450.0


def test_build_envelope_record_scale_1_marks_resolved_no_apply():
    rec = envelope_mod.build_envelope_record(_make_parsed(60000.0), scale_factor=1.0)
    payload = rec["payload"]
    assert payload.get("scale_resolved_at_emit") is True
    assert payload.get("scale_factor") == 1.0
    assert "scale_unknown" not in payload
    # No rescale at factor=1
    assert payload["entry_value"] == 60000.0


def test_build_envelope_record_scale_1e6_applies_to_payload():
    rec = envelope_mod.build_envelope_record(_make_parsed(), scale_factor=1e6)
    payload = rec["payload"]
    assert payload.get("scale_resolved_at_emit") is True
    assert payload.get("scale_factor") == 1e6
    assert "scale_unknown" not in payload
    assert payload["entry_value"] == pytest.approx(32450.0 / 1e6)
    assert payload["stop_loss"] == pytest.approx(31000.0 / 1e6)
    assert payload["targets"] == [pytest.approx(33000.0 / 1e6), pytest.approx(34000.0 / 1e6)]


def test_emit_parsed_signal_forwards_scale_factor(tmp_path: Path):
    log = tmp_path / "envelope.jsonl"
    rec = envelope_mod.emit_parsed_signal(_make_parsed(), envelope_log=log, scale_factor=1e6)
    assert rec is not None
    line = log.read_text(encoding="utf-8").splitlines()[0]
    persisted = json.loads(line)
    assert persisted["payload"].get("scale_resolved_at_emit") is True
    assert persisted["payload"]["entry_value"] == pytest.approx(32450.0 / 1e6)


def test_emit_parsed_signal_without_scale_marks_unknown(tmp_path: Path):
    log = tmp_path / "envelope.jsonl"
    rec = envelope_mod.emit_parsed_signal(_make_parsed(), envelope_log=log)
    assert rec is not None
    line = log.read_text(encoding="utf-8").splitlines()[0]
    persisted = json.loads(line)
    assert persisted["payload"].get("scale_unknown") is True
    assert persisted["payload"]["entry_value"] == 32450.0
