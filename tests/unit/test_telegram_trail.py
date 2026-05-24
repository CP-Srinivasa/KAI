"""Unit tests for the Telegram /trail command and signal trail formatter."""

from __future__ import annotations

import json
from pathlib import Path
from app.messaging.signal_trail import format_signal_trail_message, find_matching_signal_data


def test_format_signal_trail_empty_logs(tmp_path: Path) -> None:
    # No logs exist -> should return helpful instructions
    res = format_signal_trail_message("", tmp_path)
    assert "Keine kürzlichen Signale gefunden" in res


def test_format_signal_trail_recent_list(tmp_path: Path) -> None:
    # Create mock signal audit record
    audit_file = tmp_path / "tradingview_signal_audit.jsonl"
    r = {
        "signal_id": "SIG_TEST_001",
        "symbol": "BTC/USDT",
        "direction": "LONG",
        "timestamp": "2026-05-24T12:00:00Z",
    }
    with audit_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(r) + "\n")

    res = format_signal_trail_message("", tmp_path)
    assert "Kürzliche TradingView Signale:" in res
    assert "SIG_TEST_001" in res


def test_format_signal_trail_with_details(tmp_path: Path) -> None:
    # Write mock logs across the lifecycle
    # Ingress
    ingress_file = tmp_path / "tradingview_signal_audit.jsonl"
    ing = {
        "signal_id": "SIG_TEST_001",
        "symbol": "BTC/USDT",
        "direction": "LONG",
        "timestamp": "2026-05-24T12:00:00Z",
        "auth_valid": True,
        "auth_method": "HMAC",
        "is_replay": False,
        "directional_eligible": True,
    }
    with ingress_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(ing) + "\n")

    # Decision
    prom_file = tmp_path / "tradingview_promoted_signals.jsonl"
    prom = {"signal_id": "SIG_TEST_001", "promoted": True}
    with prom_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(prom) + "\n")

    # Approval
    app_file = tmp_path / "decision_journal.jsonl"
    app = {
        "signal_id": "SIG_TEST_001",
        "decision": "approved",
        "operator": "Operator1",
        "timestamp": "2026-05-24T12:05:00Z",
    }
    with app_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(app) + "\n")

    # Intent
    intent_file = tmp_path / "alert_audit.jsonl"
    intent = {
        "document_id": "doc_corr_abc123",
        "sentiment_label": "bullish",
        "affected_assets": ["BTC/USDT"],
    }
    # Link it via correlation ID or signal ID
    # In our parser, query matches doc_id too
    with intent_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(intent) + "\n")

    # Routing & Execution
    exec_file = tmp_path / "paper_execution_audit.jsonl"
    exe_1 = {
        "signal_id": "SIG_TEST_001",
        "correlation_id": "doc_corr_abc123",
        "event_type": "order_created",
        "timestamp_utc": "2026-05-24T12:06:00Z",
    }
    exe_2 = {
        "signal_id": "SIG_TEST_001",
        "correlation_id": "doc_corr_abc123",
        "event_type": "position_opened",
        "timestamp_utc": "2026-05-24T12:07:00Z",
    }
    with exec_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(exe_1) + "\n")
        f.write(json.dumps(exe_2) + "\n")

    res = format_signal_trail_message("SIG_TEST_001", tmp_path)
    assert "KAI Signal Trail: SIG_TEST_001" in res
    assert "Ingress:" in res and "Empfangen" in res
    assert "Auth/Provenance:" in res and "Valide" in res
    assert "Replay-Guard:" in res and "Eindeutig" in res
    assert "Eligibility:" in res and "Berechtigt" in res
    assert "Promotion:" in res and "Promoted" in res
    assert "Operator-Approval:" in res and "Akzeptiert" in res
    assert "Execution Routing:" in res and "Geroutet" in res
    assert "Lifecycle:" in res and "Offen" in res
    assert "order_created" in res
    assert "position_opened" in res
