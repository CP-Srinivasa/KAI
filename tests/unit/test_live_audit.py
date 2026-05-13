"""Phase-0 live-v1 Audit-Stream Tests (Task N+4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.execution.live_audit import (
    LIVE_AUDIT_SCHEMA_VERSION,
    AuditEvent,
    AuditEventType,
    GateCheckRecord,
    filter_events,
    read_events,
    write_event,
)


def _attempted_event(audit_id: str = "test-001") -> AuditEvent:
    return AuditEvent(
        event_type=AuditEventType.ATTEMPTED.value,
        audit_id=audit_id,
        exchange="binance",
        symbol="BTCUSDT",
        side="buy",
        quantity=0.001,
        entry_price=80000.0,
        notional_usd=80.0,
        stop_loss=78000.0,
        client_order_id="t-001",
        live_state="unlocked",
        idle_lock_remaining_s=3500,
    )


def _placed_event(audit_id: str = "test-002") -> AuditEvent:
    return AuditEvent(
        event_type=AuditEventType.PLACED.value,
        audit_id=audit_id,
        exchange="binance",
        symbol="BTCUSDT",
        side="buy",
        quantity=0.001,
        entry_price=80000.0,
        notional_usd=80.0,
        stop_loss=78000.0,
        client_order_id="t-002",
        hotp_counter_used=42,
        live_caps_check="passed",
        risk_engine_check="passed",
        exchange_perms_check="passed",
        server_sl_check="passed",
        gates=(
            GateCheckRecord("hotp", True, "counter=42"),
            GateCheckRecord("live_caps", True, "notional=80"),
            GateCheckRecord("risk", True, "approved"),
            GateCheckRecord("exchange_perms", True, "phase0_compliant"),
            GateCheckRecord("server_sl", True, "order_id=ord_xyz sl=sl_xyz"),
        ),
        order_id="ord_xyz",
        sl_order_id="sl_xyz",
        sl_price=78000.0,
        current_open_positions_at_send=1,
        live_state="unlocked",
    )


def _rejected_event(audit_id: str = "test-003", failed_gate: str = "hotp") -> AuditEvent:
    return AuditEvent(
        event_type=AuditEventType.REJECTED.value,
        audit_id=audit_id,
        exchange="binance",
        symbol="BTCUSDT",
        side="buy",
        quantity=0.001,
        notional_usd=80.0,
        gates=(GateCheckRecord(failed_gate, False, "fail-detail"),),
        reject_reason="hotp_failed",
        gate_failed=failed_gate,
        detail="counter mismatch",
        live_state="unlocked",
    )


class TestWriteAndReadRoundtrip:
    def test_single_event_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        ev = _attempted_event()
        write_event(path, ev)
        events = read_events(path)
        assert len(events) == 1
        assert events[0].audit_id == "test-001"
        assert events[0].schema_version == LIVE_AUDIT_SCHEMA_VERSION
        assert events[0].timestamp_utc.endswith("+00:00")

    def test_three_events_preserve_order(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        write_event(path, _attempted_event("a"))
        write_event(path, _placed_event("b"))
        write_event(path, _rejected_event("c"))
        events = read_events(path)
        assert [e.audit_id for e in events] == ["a", "b", "c"]
        assert events[1].order_id == "ord_xyz"
        assert events[1].sl_price == 78000.0
        assert events[2].gate_failed == "hotp"

    def test_gates_serialize_correctly(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        write_event(path, _placed_event())
        events = read_events(path)
        assert len(events[0].gates) == 5
        assert events[0].gates[0].name == "hotp"
        assert events[0].gates[0].passed is True
        assert events[0].gates[4].name == "server_sl"


class TestErrorHandling:
    def test_write_to_nonexistent_dir_creates_it(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "audit.jsonl"
        write_event(path, _attempted_event())
        assert path.exists()

    def test_read_missing_file_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.jsonl"
        assert read_events(path) == []

    def test_read_skips_corrupt_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        write_event(path, _placed_event("a"))
        # Append garbage
        with path.open("a", encoding="utf-8") as fh:
            fh.write("this is not json\n")
        write_event(path, _placed_event("b"))
        events = read_events(path)
        # Garbage-Line skipped, 2 valid events durch
        assert len(events) == 2

    def test_read_skips_schema_mismatch(self, tmp_path: Path) -> None:
        import json

        path = tmp_path / "audit.jsonl"
        # Schema-Mismatch: unknown field
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"unknown_field": 42}) + "\n")
        write_event(path, _attempted_event())
        events = read_events(path)
        # Schema-Mismatch-Line skipped, 1 valid event durch
        assert len(events) == 1


class TestFilterEvents:
    def test_filter_by_event_type(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        write_event(path, _attempted_event())
        write_event(path, _placed_event())
        write_event(path, _rejected_event())
        events = read_events(path)
        placed = filter_events(events, event_type=AuditEventType.PLACED)
        assert len(placed) == 1
        assert placed[0].order_id == "ord_xyz"

    def test_filter_by_symbol(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        ev1 = _placed_event("a")
        ev2 = AuditEvent(
            event_type=AuditEventType.PLACED.value,
            audit_id="b",
            symbol="ETHUSDT",
        )
        write_event(path, ev1)
        write_event(path, ev2)
        eth = filter_events(read_events(path), symbol="ETHUSDT")
        assert len(eth) == 1
        assert eth[0].audit_id == "b"

    def test_success_only(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        write_event(path, _attempted_event("a"))
        write_event(path, _placed_event("b"))
        write_event(path, _rejected_event("c"))
        success = filter_events(read_events(path), success_only=True)
        assert [e.audit_id for e in success] == ["b"]


class TestSchemaShape:
    def test_schema_version_is_live_v1(self) -> None:
        assert LIVE_AUDIT_SCHEMA_VERSION == "live-v1"

    def test_all_event_types_known(self) -> None:
        assert AuditEventType.ATTEMPTED.value == "live_order_attempted"
        assert AuditEventType.PLACED.value == "live_order_placed"
        assert AuditEventType.REJECTED.value == "live_order_rejected"

    def test_audit_event_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        ev = _attempted_event()
        with pytest.raises(FrozenInstanceError):
            ev.audit_id = "modified"  # type: ignore[misc]
