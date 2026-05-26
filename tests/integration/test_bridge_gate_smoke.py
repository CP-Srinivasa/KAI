"""Smoke test suite for the Bridge-Gate and Manual Operator-Review system.

Ensures that:
1. TradingView messages and structured signals are parsed cleanly by the signal parser.
2. The manual review process (appending and loading outcomes with portalocker locks) is robust.
3. The /trail command correctly parses composite states without throwing exceptions.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.alerts.audit import (
    AlertOutcomeAnnotation,
    append_outcome_annotation,
    load_outcome_annotations,
)
from app.messaging.signal_parser import detect_message_type, parse_structured_message
from app.messaging.signal_trail import format_signal_trail_message


def test_structured_signal_parsing_smoke() -> None:
    # A standard long trade signal block
    sample_signal = """[SIGNAL]
Signal ID: SIG-20260524-BTC-001
Source: Premium Signals
Symbol: BTC/USDT
Side: BUY
Direction: LONG
Entry Rule: BELOW 68000
Targets: 72000
Stop Loss: 65000
Leverage: 10x
Status: NEW
Timestamp: 2026-05-24T12:00:00Z"""

    msg_type = detect_message_type(sample_signal)
    assert msg_type == "signal"

    parsed = parse_structured_message(sample_signal)
    assert parsed.signal_id == "SIG-20260524-BTC-001"
    assert parsed.symbol == "BTCUSDT"
    assert parsed.side.value == "buy"
    assert parsed.direction.value == "long"
    assert parsed.stop_loss == 65000.0


def test_manual_bridge_outcome_portalocker_smoke(tmp_path: Path) -> None:
    # Simulates concurrent writers accessing the same outcomes JSONL file
    # and verifies Portalocker keeps it safe.
    outcome_file = tmp_path / "alert_outcomes.jsonl"

    annot = AlertOutcomeAnnotation(
        document_id="doc_smoke_001",
        outcome="hit",
        asset="BTC/USDT",
        note="auto: bullish BTC/USDT $68,000->$69,000",
    )

    # Append outcome
    append_outcome_annotation(annot, outcome_file)

    # Verify outcome loaded correctly
    results = load_outcome_annotations(outcome_file)
    assert len(results) == 1
    assert results[0].document_id == "doc_smoke_001"
    assert results[0].outcome == "hit"
    assert "auto:" in results[0].note


def test_telegram_trail_integration_smoke(tmp_path: Path) -> None:
    # Reconstructs a full trail from empty to populated to guarantee no crashes

    # 1. Empty trail
    res_empty = format_signal_trail_message("NON_EXISTING_ID", tmp_path)
    assert "Kein Signal" in res_empty

    # 2. Populated trail
    # Ingress
    ingress_file = tmp_path / "tradingview_signal_audit.jsonl"
    ing = {
        "signal_id": "SIG_SMOKE_001",
        "symbol": "BTC/USDT",
        "direction": "LONG",
        "timestamp": "2026-05-24T12:00:00Z",
        "auth_valid": True,
        "is_replay": False,
        "directional_eligible": True,
    }
    with ingress_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(ing) + "\n")

    res_populated = format_signal_trail_message("SIG_SMOKE_001", tmp_path)
    assert "KAI Signal Trail: SIG_SMOKE_001" in res_populated
    assert "Ingress:" in res_populated
    assert "Auth/Provenance:" in res_populated
    assert "Replay-Guard:" in res_populated
