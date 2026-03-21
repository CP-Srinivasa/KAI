"""Unit tests for the Sprint 20C consumer collection surface (I-116–I-122)."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.research.consumer_collection import (
    CONSUMER_ACK_JSONL_FILENAME,
    ConsumerAcknowledgement,
    append_consumer_acknowledgement,
    build_consumer_audit_summary,
    create_consumer_acknowledgement,
    load_consumer_acknowledgements,
)


def test_create_consumer_acknowledgement_maps_fields() -> None:
    """The factory creates a properly structured judgement."""
    ack = create_consumer_acknowledgement(
        handoff_id="handoff-123",
        signal_id="sig-456",
        consumer_agent_id="test-buyer",
        visibility_class="consumer-visible",
    )
    assert ack.handoff_id == "handoff-123"
    assert ack.signal_id == "sig-456"
    assert ack.consumer_agent_id == "test-buyer"
    assert ack.visibility_class == "consumer-visible"


def test_create_consumer_acknowledgement_generates_unique_ack_id() -> None:
    """I-118: ack_id is a unique UUID, not inherited."""
    ack1 = create_consumer_acknowledgement("h-1", "s-1", "c-1")
    ack2 = create_consumer_acknowledgement("h-1", "s-1", "c-1")
    assert ack1.ack_id != ack2.ack_id
    assert len(ack1.ack_id) == 36


def test_create_consumer_acknowledgement_sets_acknowledged_at() -> None:
    """ISO 8601 timestamp must be captured at creation."""
    ack = create_consumer_acknowledgement("h", "s", "c")
    assert "T" in ack.acknowledged_at
    assert ack.acknowledged_at.endswith("+00:00")


def test_create_consumer_acknowledgement_is_acknowledged_always_true() -> None:
    """I-119: The record only exists if acknowledged, hence always True."""
    ack = create_consumer_acknowledgement("h", "s", "c")
    assert ack.is_acknowledged is True


def test_create_consumer_acknowledgement_default_visibility_class() -> None:
    ack = create_consumer_acknowledgement("h", "s", "c")
    assert ack.visibility_class == "unknown"


def test_create_consumer_acknowledgement_audit_note_always_present() -> None:
    """I-116/I-117: Required disclaimer MUST always be structurally present."""
    ack = create_consumer_acknowledgement("h", "s", "c")
    assert "Acknowledgement is audit only" in ack.audit_note
    assert "Receipt does not confirm trade intent" in ack.audit_note
    assert "I-116" in ack.audit_note


def test_consumer_acknowledgement_is_frozen() -> None:
    """Consumer records must be immutable in memory."""
    ack = create_consumer_acknowledgement("h", "s", "c")
    with pytest.raises(FrozenInstanceError):
        ack.consumer_agent_id = "hacked-agent"  # type: ignore


def test_consumer_acknowledgement_to_json_dict_structure() -> None:
    ack = create_consumer_acknowledgement(
        handoff_id="test-h",
        signal_id="test-s",
        consumer_agent_id="test-c",
    )
    payload = ack.to_json_dict()
    assert payload["handoff_id"] == "test-h"
    assert payload["signal_id"] == "test-s"
    assert payload["consumer_agent_id"] == "test-c"
    assert payload["is_acknowledged"] is True
    assert "audit_note" in payload


def test_consumer_acknowledgement_no_execution_fields() -> None:
    """I-121: Absolutely no fields that could imply approval or execution intent."""
    payload = create_consumer_acknowledgement("h", "s", "c").to_json_dict()
    assert "approved" not in payload
    assert "executed" not in payload
    assert "execution_status" not in payload
    assert "status" not in payload


# --- Persistence (I-120) ---


def test_append_consumer_acknowledgement_creates_file(tmp_path: Path) -> None:
    out_file = tmp_path / CONSUMER_ACK_JSONL_FILENAME
    ack = create_consumer_acknowledgement("h", "s", "c")

    append_consumer_acknowledgement(ack, out_file)

    assert out_file.exists()
    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    loaded = json.loads(lines[0])
    assert loaded["handoff_id"] == "h"


def test_append_consumer_acknowledgement_creates_parent_dirs(tmp_path: Path) -> None:
    out_file = tmp_path / "deep" / "dir" / CONSUMER_ACK_JSONL_FILENAME
    ack = create_consumer_acknowledgement("h", "s", "c")
    append_consumer_acknowledgement(ack, out_file)
    assert out_file.exists()


def test_append_consumer_acknowledgement_is_append_only(tmp_path: Path) -> None:
    out_file = tmp_path / CONSUMER_ACK_JSONL_FILENAME
    ack1 = create_consumer_acknowledgement("h1", "s1", "c1")
    ack2 = create_consumer_acknowledgement("h2", "s2", "c2")

    append_consumer_acknowledgement(ack1, out_file)
    append_consumer_acknowledgement(ack2, out_file)

    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["handoff_id"] == "h1"
    assert json.loads(lines[1])["handoff_id"] == "h2"


def test_load_consumer_acknowledgements_empty_file(tmp_path: Path) -> None:
    out_file = tmp_path / "empty.jsonl"
    out_file.touch()
    acks = load_consumer_acknowledgements(out_file)
    assert len(acks) == 0


def test_load_consumer_acknowledgements_missing_file(tmp_path: Path) -> None:
    missing_file = tmp_path / "does-not-exist.jsonl"
    acks = load_consumer_acknowledgements(missing_file)
    assert len(acks) == 0


def test_load_consumer_acknowledgements_roundtrip(tmp_path: Path) -> None:
    out_file = tmp_path / CONSUMER_ACK_JSONL_FILENAME
    orig_ack = create_consumer_acknowledgement("hx", "sx", "cx", visibility_class="test-vis")
    append_consumer_acknowledgement(orig_ack, out_file)

    loaded_acks = load_consumer_acknowledgements(out_file)
    assert len(loaded_acks) == 1
    loaded = loaded_acks[0]

    assert isinstance(loaded, ConsumerAcknowledgement)
    assert loaded.handoff_id == orig_ack.handoff_id
    assert loaded.signal_id == orig_ack.signal_id
    assert loaded.consumer_agent_id == orig_ack.consumer_agent_id
    assert loaded.visibility_class == orig_ack.visibility_class
    assert loaded.acknowledged_at == orig_ack.acknowledged_at


def test_load_consumer_acknowledgements_skips_malformed_lines(tmp_path: Path) -> None:
    out_file = tmp_path / CONSUMER_ACK_JSONL_FILENAME

    append_consumer_acknowledgement(create_consumer_acknowledgement("h1", "s1", "c1"), out_file)

    with out_file.open("a", encoding="utf-8") as f:
        f.write("this is not json\n")
        f.write('{"missing": "fields"}\n')
        f.write('{"handoff_id": "h", "signal_id": "s", "consumer_agent_id": "c"}\n')

    append_consumer_acknowledgement(create_consumer_acknowledgement("h2", "s2", "c2"), out_file)

    acks = load_consumer_acknowledgements(out_file)
    assert len(acks) == 2
    assert acks[0].handoff_id == "h1"
    assert acks[1].handoff_id == "h2"


# --- Audit Summary ---

def test_build_consumer_audit_summary_empty() -> None:
    summary = build_consumer_audit_summary([], [])
    assert summary.total_handoffs == 0
    assert summary.acknowledged_count == 0
    assert summary.pending_count == 0
    assert summary.acknowledgements_by_consumer == {}


def test_build_consumer_audit_summary_counts_by_consumer() -> None:
    acks = [
        create_consumer_acknowledgement("h1", "s1", "agent-alpha"),
        create_consumer_acknowledgement("h2", "s2", "agent-alpha"),
        create_consumer_acknowledgement("h3", "s3", "agent-beta"),
    ]
    summary = build_consumer_audit_summary([], acks)
    assert summary.total_handoffs == 0
    assert summary.acknowledged_count == 3
    assert summary.pending_count == 0
    counts = summary.acknowledgements_by_consumer
    assert counts["agent-alpha"] == 2
    assert counts["agent-beta"] == 1


def test_build_consumer_audit_summary_counts_by_signal() -> None:
    class MockHandoff:
        signal_id: str
        handoff_id: str
        def __init__(self, sid: str, hid: str):
            self.signal_id = sid
            self.handoff_id = hid

    handoffs = [ MockHandoff("s1", "h1"), MockHandoff("s2", "h2") ] # type: ignore
    acks = [
        create_consumer_acknowledgement("hx", "s1", "c1"),
        create_consumer_acknowledgement("hy", "s1", "c2"),
    ]
    summary = build_consumer_audit_summary(handoffs, acks) # type: ignore
    assert summary.total_handoffs == 2
    assert summary.acknowledged_count == 2
    assert summary.pending_count == 2

    sig_counts = summary.acknowledgements_by_signal
    assert sig_counts["s1"] == 2
    assert sig_counts.get("s2", 0) == 0


def test_consumer_audit_summary_to_json_dict_structure() -> None:
    acks = [
        create_consumer_acknowledgement("h1", "s1", "agent-alpha"),
    ]
    summary = build_consumer_audit_summary([], acks)
    payload = summary.to_json_dict()
    assert payload["total_handoffs"] == 0
    assert payload["acknowledged_count"] == 1
    assert payload["pending_count"] == 0
    assert payload["interface_mode"] == "read_only"
    assert payload["consumers"]["agent-alpha"] == 1
    assert payload["acknowledged_handoffs"][0]["handoff_id"] == "h1"


def test_consumer_audit_summary_interface_mode_always_read_only() -> None:
    """I-116: Audit structures must transparently declare read_only nature."""
    summary = build_consumer_audit_summary([], [])
    assert summary.interface_mode == "read_only"
    assert "execution" not in summary.to_json_dict()
