"""Tests for SENTR-F-004 — HMAC tamper-detection on tv_pending_signals.jsonl."""

from __future__ import annotations

import json
from pathlib import Path

from app.alerts.tv_bridge import persist_tv_events_as_alert_audits
from app.signals.models import SignalProvenance
from app.signals.tradingview_event import (
    TV_ROW_HMAC_FIELD,
    TradingViewSignalEvent,
    append_pending_signal,
    compute_row_hmac,
    event_to_jsonl_dict,
    verify_row_hmac,
)


def _event(event_id: str = "tvsig_abc", ticker: str = "BTCUSDT") -> TradingViewSignalEvent:
    return TradingViewSignalEvent(
        event_id=event_id,
        received_at="2026-04-20T10:00:00+00:00",
        ticker=ticker,
        action="buy",
        price=60000.0,
        note=None,
        strategy=None,
        source_request_id="req-1",
        source_payload_hash="hash-1",
        external_event_id=None,
        provenance=SignalProvenance(
            source="tradingview_webhook",
            version="tv-3",
            signal_path_id="tvpath_x",
        ),
    )


def test_compute_and_verify_roundtrip() -> None:
    payload = event_to_jsonl_dict(_event())
    sig = compute_row_hmac(payload, "super-secret")
    payload[TV_ROW_HMAC_FIELD] = sig
    assert verify_row_hmac(payload, "super-secret") is True


def test_verify_rejects_tampered_payload() -> None:
    payload = event_to_jsonl_dict(_event())
    payload[TV_ROW_HMAC_FIELD] = compute_row_hmac(payload, "super-secret")
    # Attacker flips action from buy -> sell without knowing the secret.
    payload["action"] = "sell"
    assert verify_row_hmac(payload, "super-secret") is False


def test_verify_rejects_wrong_secret() -> None:
    payload = event_to_jsonl_dict(_event())
    payload[TV_ROW_HMAC_FIELD] = compute_row_hmac(payload, "correct")
    assert verify_row_hmac(payload, "wrong") is False


def test_verify_rejects_missing_sig_field() -> None:
    payload = event_to_jsonl_dict(_event())
    assert verify_row_hmac(payload, "super-secret") is False


def test_append_without_secret_produces_unsigned_row(tmp_path: Path) -> None:
    p = tmp_path / "pending.jsonl"
    append_pending_signal(p, _event())  # no secret
    row = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert TV_ROW_HMAC_FIELD not in row


def test_append_with_secret_produces_valid_signed_row(tmp_path: Path) -> None:
    p = tmp_path / "pending.jsonl"
    append_pending_signal(p, _event(), hmac_secret="s3cret")
    row = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert TV_ROW_HMAC_FIELD in row
    assert verify_row_hmac(row, "s3cret") is True


def test_bridge_with_secret_accepts_signed_row(tmp_path: Path) -> None:
    pending = tmp_path / "pending.jsonl"
    audit = tmp_path / "audit.jsonl"
    append_pending_signal(pending, _event(), hmac_secret="s3cret")
    counts = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        hmac_secret="s3cret",
    )
    assert counts["written"] == 1
    assert counts["skipped_unsigned"] == 0
    assert counts["skipped_tampered"] == 0


def test_bridge_with_secret_rejects_unsigned_row(tmp_path: Path) -> None:
    pending = tmp_path / "pending.jsonl"
    audit = tmp_path / "audit.jsonl"
    append_pending_signal(pending, _event())  # no signature
    counts = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        hmac_secret="s3cret",
    )
    assert counts["written"] == 0
    assert counts["skipped_unsigned"] == 1


def test_bridge_with_secret_rejects_tampered_row(tmp_path: Path) -> None:
    pending = tmp_path / "pending.jsonl"
    audit = tmp_path / "audit.jsonl"
    append_pending_signal(pending, _event(), hmac_secret="s3cret")
    # Attacker edits the file on disk, flipping buy -> sell.
    lines = pending.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["action"] = "sell"
    pending.write_text(json.dumps(row, separators=(",", ":")) + "\n", encoding="utf-8")

    counts = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        hmac_secret="s3cret",
    )
    assert counts["written"] == 0
    assert counts["skipped_tampered"] == 1


def test_bridge_without_secret_still_accepts_legacy_unsigned_rows(tmp_path: Path) -> None:
    # Legacy deployment: no secret configured anywhere. Behaviour unchanged.
    pending = tmp_path / "pending.jsonl"
    audit = tmp_path / "audit.jsonl"
    append_pending_signal(pending, _event())
    counts = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        hmac_secret="",
    )
    assert counts["written"] == 1
    assert counts["skipped_unsigned"] == 0
    assert counts["skipped_tampered"] == 0
