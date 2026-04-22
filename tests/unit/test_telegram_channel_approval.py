"""Tests for telegram_channel_approval (B-6, pure + JSONL tier).

These tests avoid the Telegram bot transport — they cover the pure helpers
(format, keyboard, parse, ttl, build_approval_record) and the JSONL-side of
``handle_signal_approval`` (load/dedup/ttl/re-emit). The bot callback wiring
is covered by a separate integration test once the dispatch is wired.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.ingestion.telegram_channel_approval import (
    APPROVED_SUFFIX,
    build_approval_record,
    build_inline_keyboard,
    format_approval_message,
    handle_signal_approval,
    is_already_approved,
    is_ttl_expired,
    load_envelope_by_id,
    parse_callback_data,
)


def _shadow_record(
    *,
    envelope_id: str = "ENV-20260421111700-abc12345",
    timestamp: datetime | None = None,
    source: str = "telegram_premium_channel",
) -> dict[str, Any]:
    ts = (timestamp or datetime(2026, 4, 21, 11, 17, 0, tzinfo=UTC)).isoformat()
    return {
        "timestamp_utc": ts,
        "event": "telegram_channel_envelope",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": source,
        "execution_enabled": False,
        "write_back_allowed": False,
        "envelope_id": envelope_id,
        "idempotency_key": "deadbeefcafebabe",
        "payload": {
            "signal_id": "SIG-TGCH-20260421111700-GUNUSDT",
            "source": source,
            "symbol": "GUNUSDT",
            "display_symbol": "GUN/USDT",
            "direction": "long",
            "side": "buy",
            "entry_value": 2800,
            "entry_min": None,
            "entry_max": None,
            "stop_loss": 2680,
            "targets": [2815, 2830, 2840, 2855],
            "leverage": 10,
            "timestamp_utc": ts,
        },
        "chat_id": -1001275462917,
    }


def _write_log(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestFormatApprovalMessage:
    def test_contains_core_fields(self) -> None:
        rec = _shadow_record()
        msg = format_approval_message(rec, ttl_minutes=60)
        assert "GUN/USDT" in msg
        assert "LONG" in msg
        assert "2800" in msg
        assert "2680" in msg
        # all four targets shown
        for tp in ("2815", "2830", "2840", "2855"):
            assert tp in msg
        assert "TTL: 60 Min" in msg
        # percentage annotation near SL
        assert "-4.29%" in msg or "-4.286%" in msg or "-4.3%" in msg

    def test_range_entry_renders_as_pair(self) -> None:
        rec = _shadow_record()
        rec["payload"]["entry_min"] = 2790
        rec["payload"]["entry_max"] = 2810
        msg = format_approval_message(rec, ttl_minutes=30)
        assert "2790" in msg and "2810" in msg

    def test_missing_leverage_shows_dash(self) -> None:
        rec = _shadow_record()
        rec["payload"]["leverage"] = None
        msg = format_approval_message(rec, ttl_minutes=60)
        # leverage slot renders "—" instead of crashing
        assert "—" in msg


class TestBuildInlineKeyboard:
    def test_has_fill_and_ignore_buttons(self) -> None:
        kb = build_inline_keyboard("ENV-X")
        assert len(kb) == 1 and len(kb[0]) == 2
        fill, ignore = kb[0]
        assert "Fill" in fill["text"]
        assert "Ignore" in ignore["text"]
        assert fill["callback_data"] == "sig:f:ENV-X"
        assert ignore["callback_data"] == "sig:i:ENV-X"

    def test_callback_data_fits_telegram_64_byte_limit(self) -> None:
        # Real envelope ids are ENV-YYYYMMDDHHMMSS-<hex8> ≈ 27 chars.
        kb = build_inline_keyboard("ENV-20260421111700-abc12345")
        for btn in kb[0]:
            assert len(btn["callback_data"].encode("utf-8")) <= 64


class TestParseCallbackData:
    def test_fill(self) -> None:
        act = parse_callback_data("sig:f:ENV-123")
        assert act is not None and act.action == "fill" and act.envelope_id == "ENV-123"

    def test_ignore(self) -> None:
        act = parse_callback_data("sig:i:ENV-123")
        assert act is not None and act.action == "ignore"

    def test_foreign_prefix_is_none(self) -> None:
        assert parse_callback_data("menu:main") is None
        assert parse_callback_data("cmd:status") is None

    def test_malformed_is_none(self) -> None:
        assert parse_callback_data("sig:f") is None
        assert parse_callback_data("") is None
        assert parse_callback_data("sig:z:foo") is None


class TestIsTtlExpired:
    def test_fresh_not_expired(self) -> None:
        ts = datetime(2026, 4, 21, 11, 0, 0, tzinfo=UTC).isoformat()
        now = datetime(2026, 4, 21, 11, 30, 0, tzinfo=UTC)
        assert is_ttl_expired(ts, ttl_minutes=60, now=now) is False

    def test_older_than_ttl_is_expired(self) -> None:
        ts = datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC).isoformat()
        now = datetime(2026, 4, 21, 11, 30, 0, tzinfo=UTC)
        assert is_ttl_expired(ts, ttl_minutes=60, now=now) is True

    def test_unparseable_is_expired(self) -> None:
        assert is_ttl_expired("not-a-date", ttl_minutes=60) is True

    def test_naive_timestamp_treated_as_utc(self) -> None:
        ts = datetime(2026, 4, 21, 11, 0, 0).isoformat()  # no tz
        now = datetime(2026, 4, 21, 11, 30, 0, tzinfo=UTC)
        assert is_ttl_expired(ts, ttl_minutes=60, now=now) is False


class TestBuildApprovalRecord:
    def test_new_envelope_has_approved_source(self) -> None:
        orig = _shadow_record()
        rec = build_approval_record(orig, approved_by=388516496)
        assert rec["source"] == f"telegram_premium_channel{APPROVED_SUFFIX}"
        assert rec["origin_envelope_id"] == orig["envelope_id"]
        assert rec["origin_source"] == "telegram_premium_channel"
        assert rec["approved_by"] == 388516496
        assert rec["payload"]["source"] == rec["source"]

    def test_idempotency_key_differs_from_origin(self) -> None:
        orig = _shadow_record()
        rec = build_approval_record(orig)
        assert rec["idempotency_key"] != orig["idempotency_key"]

    def test_envelope_id_is_fresh(self) -> None:
        orig = _shadow_record()
        later = datetime(2026, 4, 21, 11, 20, 0, tzinfo=UTC)
        rec = build_approval_record(orig, now=later)
        assert rec["envelope_id"] != orig["envelope_id"]
        assert "20260421112000" in rec["envelope_id"]

    def test_stage_and_execution_flags(self) -> None:
        orig = _shadow_record()
        rec = build_approval_record(orig)
        # Bridge consumes stage=accepted / status=ok / message_type=signal
        assert rec["stage"] == "accepted"
        assert rec["status"] == "ok"
        assert rec["message_type"] == "signal"
        assert rec["execution_enabled"] is True


class TestLoadEnvelopeById:
    def test_returns_matching_record(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        a = _shadow_record(envelope_id="ENV-A")
        b = _shadow_record(envelope_id="ENV-B")
        _write_log(log, [a, b])
        found = load_envelope_by_id(log, "ENV-B")
        assert found is not None and found["envelope_id"] == "ENV-B"

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        _write_log(log, [_shadow_record(envelope_id="ENV-A")])
        assert load_envelope_by_id(log, "ENV-X") is None

    def test_empty_envelope_id(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        _write_log(log, [_shadow_record()])
        assert load_envelope_by_id(log, "") is None


class TestIsAlreadyApproved:
    def test_no_prior_approval(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        _write_log(log, [_shadow_record(envelope_id="ENV-A")])
        assert is_already_approved(log, "ENV-A") is False

    def test_detects_prior_approval(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        orig = _shadow_record(envelope_id="ENV-A")
        approved = build_approval_record(orig)
        _write_log(log, [orig, approved])
        assert is_already_approved(log, "ENV-A") is True


class TestHandleSignalApproval:
    def test_fill_happy_path(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        orig = _shadow_record(envelope_id="ENV-A")
        _write_log(log, [orig])

        later = datetime(2026, 4, 21, 11, 20, 0, tzinfo=UTC)
        outcome = handle_signal_approval(
            "fill",
            "ENV-A",
            envelope_log=log,
            ttl_minutes=60,
            approved_by=42,
            now=later,
        )
        assert outcome.status == "filled"
        assert outcome.new_envelope_id is not None
        # JSONL now contains origin + approved re-emit
        records = _read_log(log)
        assert len(records) == 2
        approved = records[-1]
        assert approved["source"] == "telegram_premium_channel_approved"
        assert approved["origin_envelope_id"] == "ENV-A"
        assert approved["approved_by"] == 42

    def test_ignore_writes_audit_no_reemit(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        orig = _shadow_record(envelope_id="ENV-A")
        _write_log(log, [orig])

        outcome = handle_signal_approval(
            "ignore",
            "ENV-A",
            envelope_log=log,
            ttl_minutes=60,
            approved_by=42,
        )
        assert outcome.status == "ignored"
        assert outcome.new_envelope_id is None
        records = _read_log(log)
        assert len(records) == 2
        audit = records[-1]
        assert audit["stage"] == "ignored"
        assert audit["ignored_by"] == 42
        # No _approved source record was written
        assert not any(
            str(r.get("source", "")).endswith("_approved") for r in records
        )

    def test_expired_fill_refused(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        orig = _shadow_record(
            envelope_id="ENV-A",
            timestamp=datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC),
        )
        _write_log(log, [orig])
        # 90 min later, TTL=60 min → expired
        later = datetime(2026, 4, 21, 11, 30, 0, tzinfo=UTC)

        outcome = handle_signal_approval(
            "fill",
            "ENV-A",
            envelope_log=log,
            ttl_minutes=60,
            now=later,
        )
        assert outcome.status == "expired"
        # No approved record was written
        records = _read_log(log)
        assert not any(
            str(r.get("source", "")).endswith("_approved") for r in records
        )
        # But an "expired" audit row is written
        assert any(r.get("stage") == "expired" for r in records)

    def test_double_click_dedup(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        orig = _shadow_record(envelope_id="ENV-A")
        _write_log(log, [orig])
        later = datetime(2026, 4, 21, 11, 20, 0, tzinfo=UTC)

        first = handle_signal_approval(
            "fill", "ENV-A", envelope_log=log, ttl_minutes=60, now=later
        )
        second = handle_signal_approval(
            "fill", "ENV-A", envelope_log=log, ttl_minutes=60, now=later
        )
        assert first.status == "filled"
        assert second.status == "duplicate"
        # Only one _approved re-emit present
        approved_count = sum(
            1
            for r in _read_log(log)
            if str(r.get("source", "")).endswith("_approved")
        )
        assert approved_count == 1

    def test_not_found(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        _write_log(log, [_shadow_record(envelope_id="ENV-A")])
        outcome = handle_signal_approval(
            "fill", "ENV-DOES-NOT-EXIST", envelope_log=log, ttl_minutes=60
        )
        assert outcome.status == "not_found"

    def test_unknown_action(self, tmp_path: Path) -> None:
        log = tmp_path / "envelope.jsonl"
        _write_log(log, [_shadow_record(envelope_id="ENV-A")])
        outcome = handle_signal_approval(
            "detonate", "ENV-A", envelope_log=log, ttl_minutes=60
        )
        assert outcome.status == "not_found"
