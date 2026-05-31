"""Tests for telegram_channel_envelope (B-2).

Covers:
- build_envelope_record: pure shape/contract (no IO).
- emit_parsed_signal: writes JSONL, de-dups by idempotency_key.
- Contract with the bridge: the emitted record is pickable by the bridge's
  `_collect_pending_signals` filter (stage=accepted, status=ok,
  message_type=signal).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.execution.envelope_to_paper_bridge import (
    _collect_pending_signals,
    _extract_source,
    _latest_bridge_stage_by_envelope,
)
from app.ingestion.telegram_channel_envelope import (
    DEFAULT_SOURCE,
    build_envelope_record,
    build_source_uid,
    emit_parsed_signal,
)
from app.ingestion.telegram_channel_parser import parse_premium_channel_message

SAMPLE_GUN = """\
Long/Buy #GUN/USDT

Entry Point - 2800

Targets: 2815 - 2830 - 2840 - 2855

Leverage - 10x

Stop Loss - 2680"""

SAMPLE_BTC_RANGE = """\
Binance Futures, OKX, Bybit
🚀 #BTC/USDT Long/BUY
Entry Zone: 70565 – 70590
🎯 70700
🎯 70850
🎯 71000
🛑 Stop Loss - 69800
⚡️ Leverage: 10x"""


@pytest.fixture
def parsed_gun():
    sig = parse_premium_channel_message(SAMPLE_GUN)
    assert sig is not None
    return sig


@pytest.fixture
def parsed_btc_range():
    sig = parse_premium_channel_message(SAMPLE_BTC_RANGE)
    assert sig is not None
    return sig


@pytest.fixture(autouse=True)
def _isolated_event_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KAI_PREMIUM_EVENT_STORE_PATH", str(tmp_path / "premium_events.sqlite3"))


# ── build_envelope_record ───────────────────────────────────────────────────


class TestBuildRecord:
    def test_record_shape(self, parsed_gun) -> None:
        rec = build_envelope_record(parsed_gun)
        # Top-level fields required by bridge's pending-collector.
        assert rec["stage"] == "accepted"
        assert rec["status"] == "ok"
        assert rec["message_type"] == "signal"
        assert rec["source"] == DEFAULT_SOURCE
        assert rec["execution_enabled"] is False
        assert rec["write_back_allowed"] is False
        # Envelope identity.
        assert isinstance(rec["envelope_id"], str)
        assert rec["envelope_id"].startswith("ENV-")
        assert isinstance(rec["idempotency_key"], str)
        assert len(rec["idempotency_key"]) == 32  # sha256[:32]

    def test_payload_carries_signal_fields(self, parsed_gun) -> None:
        rec = build_envelope_record(parsed_gun)
        p = rec["payload"]
        assert isinstance(p, dict)
        assert p["symbol"] == "GUNUSDT"
        assert p["display_symbol"] == "GUN/USDT"
        assert p["direction"] == "long"
        assert p["side"] == "buy"
        assert p["entry_type"] == "at"
        assert p["entry_value"] == 2800.0
        assert p["stop_loss"] == 2680.0
        assert p["targets"] == [2815.0, 2830.0, 2840.0, 2855.0]
        assert p["leverage"] == 10

    def test_range_entry_payload(self, parsed_btc_range) -> None:
        rec = build_envelope_record(parsed_btc_range)
        p = rec["payload"]
        assert p["entry_type"] == "range"
        assert p["entry_min"] == 70565.0
        assert p["entry_max"] == 70590.0
        assert p["stop_loss"] == 69800.0
        assert p["targets"] == [70700.0, 70850.0, 71000.0]

    def test_chat_id_optional(self, parsed_gun) -> None:
        rec_no = build_envelope_record(parsed_gun)
        assert "chat_id" not in rec_no
        rec_with = build_envelope_record(parsed_gun, chat_id=-100123456789)
        assert rec_with["chat_id"] == -100123456789

    def test_deterministic_idempotency_across_instances(self, parsed_gun) -> None:
        """Same parsed signal + same `now` → same idempotency key."""
        now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
        a = build_envelope_record(parsed_gun, now=now)
        b = build_envelope_record(parsed_gun, now=now)
        assert a["idempotency_key"] == b["idempotency_key"]

    def test_custom_source_round_trips(self, parsed_gun) -> None:
        rec = build_envelope_record(parsed_gun, source="test_channel")
        assert rec["source"] == "test_channel"
        assert rec["payload"]["source"] == "test_channel"

    def test_telegram_source_identity_is_stable(self, parsed_gun) -> None:
        now_a = datetime(2026, 5, 30, 13, 43, 52, tzinfo=UTC)
        now_b = datetime(2026, 5, 31, 13, 43, 52, tzinfo=UTC)
        a = build_envelope_record(
            parsed_gun,
            chat_id=-1001275462917,
            message_id=23878,
            now=now_a,
            scale_factor=100000.0,
        )
        b = build_envelope_record(
            parsed_gun,
            chat_id=-1001275462917,
            message_id=23878,
            now=now_b,
            scale_factor=100000.0,
        )
        assert a["source_uid"] == "telegram:-1001275462917:23878"
        assert b["source_uid"] == a["source_uid"]
        assert b["envelope_id"] == a["envelope_id"]
        assert b["idempotency_key"] == a["idempotency_key"]
        assert b["payload"]["signal_id"] == a["payload"]["signal_id"]
        assert b["payload"]["source_message_id"] == 23878

    def test_build_source_uid(self) -> None:
        assert build_source_uid(chat_id=-1001, message_id=42) == "telegram:-1001:42"


# ── emit_parsed_signal (IO) ─────────────────────────────────────────────────


class TestEmit:
    def test_writes_single_record(self, parsed_gun, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        rec = emit_parsed_signal(parsed_gun, envelope_log=log)
        assert rec is not None
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        parsed_back = json.loads(lines[0])
        assert parsed_back["envelope_id"] == rec["envelope_id"]

    def test_deduplicates_same_signal(self, parsed_gun, tmp_path: Path) -> None:
        """Emitting the same parsed signal twice → second call returns None."""
        log = tmp_path / "envelope.jsonl"
        fixed_now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
        first = emit_parsed_signal(parsed_gun, envelope_log=log, now=fixed_now)
        assert first is not None
        second = emit_parsed_signal(parsed_gun, envelope_log=log, now=fixed_now)
        assert second is None
        # Log should still contain exactly one line.
        assert len(log.read_text(encoding="utf-8").splitlines()) == 1

    def test_deduplicates_same_telegram_source_uid_even_with_different_now(
        self, parsed_gun, tmp_path: Path
    ) -> None:
        log = tmp_path / "envelope.jsonl"
        first = emit_parsed_signal(
            parsed_gun,
            envelope_log=log,
            chat_id=-1001275462917,
            message_id=23878,
            now=datetime(2026, 5, 30, 13, 43, 52, tzinfo=UTC),
        )
        assert first is not None
        second = emit_parsed_signal(
            parsed_gun,
            envelope_log=log,
            chat_id=-1001275462917,
            message_id=23878,
            now=datetime(2026, 5, 31, 13, 43, 52, tzinfo=UTC),
        )
        assert second is None
        assert len(log.read_text(encoding="utf-8").splitlines()) == 1

    def test_deduplicates_against_event_store_when_log_lookback_misses(
        self, parsed_gun, tmp_path: Path
    ) -> None:
        log = tmp_path / "envelope.jsonl"
        first = emit_parsed_signal(
            parsed_gun,
            envelope_log=log,
            chat_id=-1001275462917,
            message_id=23878,
            now=datetime(2026, 5, 30, 13, 43, 52, tzinfo=UTC),
        )
        assert first is not None
        log.unlink()

        second = emit_parsed_signal(
            parsed_gun,
            envelope_log=log,
            chat_id=-1001275462917,
            message_id=23878,
            now=datetime(2026, 5, 31, 13, 43, 52, tzinfo=UTC),
        )
        assert second is None
        assert not log.exists()

    def test_dedup_scoped_to_accepted_only(self, parsed_gun, tmp_path: Path) -> None:
        """A prior non-accepted record with same key must NOT block emit."""
        log = tmp_path / "envelope.jsonl"
        fixed_now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
        # Prepopulate with a rejected record that shares the idempotency key.
        probe = build_envelope_record(parsed_gun, now=fixed_now)
        rejected = dict(probe)
        rejected["stage"] = "rejected_schema"
        rejected["status"] = "error"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(rejected) + "\n")
        # Fresh accepted emit should still go through.
        out = emit_parsed_signal(parsed_gun, envelope_log=log, now=fixed_now)
        assert out is not None
        assert len(log.read_text(encoding="utf-8").splitlines()) == 2

    def test_creates_parent_dir(self, parsed_gun, tmp_path: Path) -> None:
        log = tmp_path / "nested" / "dir" / "envelope.jsonl"
        rec = emit_parsed_signal(parsed_gun, envelope_log=log)
        assert rec is not None
        assert log.exists()


# ── Bridge-contract (integration at record level) ───────────────────────────


class TestBridgeContract:
    """The emitted record must pass the bridge's pending-collector filter."""

    def test_bridge_would_pick_up_emitted_record(self, parsed_gun, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        rec = emit_parsed_signal(parsed_gun, envelope_log=log)
        assert rec is not None
        # Simulate a fresh bridge scan: no prior bridge stages.
        pending = _collect_pending_signals([rec], bridge_stages={})
        assert len(pending) == 1
        assert pending[0]["envelope_id"] == rec["envelope_id"]

    def test_source_normalization_keeps_channel_tag(self, parsed_gun) -> None:
        rec = build_envelope_record(parsed_gun)
        # _extract_source passes unknown sources through lowercased.
        assert _extract_source(rec) == "telegram_premium_channel"

    def test_latest_bridge_stage_terminal_suppresses_rescan(
        self, parsed_gun, tmp_path: Path
    ) -> None:
        """If a prior bridge run filled this envelope, scan should skip it."""
        log = tmp_path / "envelope.jsonl"
        rec = emit_parsed_signal(parsed_gun, envelope_log=log)
        assert rec is not None
        bridge_records = [
            {"envelope_id": rec["envelope_id"], "stage": "filled"},
        ]
        stages = _latest_bridge_stage_by_envelope(bridge_records)
        pending = _collect_pending_signals([rec], bridge_stages=stages)
        assert pending == []
