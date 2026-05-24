from __future__ import annotations

import json
from pathlib import Path

from app.execution.audit_replay import replay_paper_audit


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_replay_reconstructs_lifecycle_history_by_correlation_id(tmp_path: Path) -> None:
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit_path,
        [
            {
                "event_type": "lifecycle_transition",
                "timestamp_utc": "2026-05-24T09:00:00+00:00",
                "correlation_id": "env-123",
                "from_state": "ORDER_BUILDING",
                "to_state": "ORDER_SUBMITTED",
                "reason": "paper_order_created",
            },
            {
                "event_type": "lifecycle_transition",
                "timestamp_utc": "2026-05-24T09:00:01+00:00",
                "correlation_id": "env-123",
                "from_state": "ORDER_SUBMITTED",
                "to_state": "ORDER_ACCEPTED",
                "reason": "paper_order_accepted",
            },
            {
                "event_type": "lifecycle_transition",
                "timestamp_utc": "2026-05-24T09:00:02+00:00",
                "correlation_id": "env-123",
                "from_state": "ORDER_ACCEPTED",
                "to_state": "POSITION_OPEN",
                "reason": "paper_position_opened",
            },
        ],
    )

    result = replay_paper_audit(audit_path)

    assert result.available
    assert result.lifecycle_replay_errors == ()
    history = result.lifecycle_history["env-123"]
    assert [transition.to_state.value for transition in history] == [
        "ORDER_SUBMITTED",
        "ORDER_ACCEPTED",
        "POSITION_OPEN",
    ]
    assert history[0].timestamp_utc == "2026-05-24T09:00:00+00:00"


def test_replay_flags_lifecycle_discontinuity(tmp_path: Path) -> None:
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit_path,
        [
            {
                "event_type": "lifecycle_transition",
                "timestamp_utc": "2026-05-24T09:00:00+00:00",
                "correlation_id": "env-gap",
                "from_state": "ORDER_BUILDING",
                "to_state": "ORDER_SUBMITTED",
                "reason": "paper_order_created",
            },
            {
                "event_type": "lifecycle_transition",
                "timestamp_utc": "2026-05-24T09:00:01+00:00",
                "correlation_id": "env-gap",
                "from_state": "ORDER_ACCEPTED",
                "to_state": "POSITION_OPEN",
                "reason": "paper_position_opened_without_accept",
            },
        ],
    )

    result = replay_paper_audit(audit_path)

    assert result.available
    assert [transition.to_state.value for transition in result.lifecycle_history["env-gap"]] == [
        "ORDER_SUBMITTED",
    ]
    assert result.lifecycle_replay_errors == (
        "audit_lifecycle_validation_error_line_2: discontinuous "
        "ORDER_SUBMITTED -> ORDER_ACCEPTED",
    )


def test_lifecycle_replay_errors_do_not_block_position_recovery(tmp_path: Path) -> None:
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit_path,
        [
            {
                "event_type": "lifecycle_transition",
                "timestamp_utc": "2026-05-24T09:00:00+00:00",
                "correlation_id": "env-bad",
                "from_state": "ORDER_SUBMITTED",
                "to_state": "WAITING_FOR_ENTRY",
                "reason": "bad_legacy_row",
            },
            {
                "event_type": "order_created",
                "timestamp_utc": "2026-05-24T09:00:01+00:00",
                "order_id": "ord_btc",
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": 0.1,
                "order_type": "market",
                "limit_price": None,
                "stop_loss": 66000.0,
                "take_profit": 70000.0,
                "created_at": "2026-05-24T09:00:01+00:00",
                "idempotency_key": "idem-btc",
                "status": "pending",
                "risk_check_id": "risk-btc",
                "position_side": "long",
            },
            {
                "event_type": "order_filled",
                "timestamp_utc": "2026-05-24T09:00:02+00:00",
                "fill_id": "fill_btc",
                "order_id": "ord_btc",
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": 0.1,
                "fill_price": 68000.0,
                "fee_usd": 6.8,
                "filled_at": "2026-05-24T09:00:02+00:00",
                "slippage_pct": 0.05,
                "position_side": "long",
                "portfolio_cash": 3200.0,
                "realized_pnl_usd": 0.0,
            },
        ],
    )

    result = replay_paper_audit(audit_path)

    assert result.available
    assert result.error is None
    assert result.positions["BTC/USDT"].quantity == 0.1
    assert result.lifecycle_replay_errors == (
        "audit_lifecycle_validation_error_line_1: illegal ORDER_SUBMITTED -> WAITING_FOR_ENTRY",
    )
