from __future__ import annotations

import pytest

from app.execution.models import (
    IllegalLifecycleTransition,
    OrderIntent,
    OrderLifecycleState,
    make_lifecycle_transition,
    validate_lifecycle_transition,
)


def test_waiting_for_entry_can_trigger_entry() -> None:
    validate_lifecycle_transition(
        OrderLifecycleState.WAITING_FOR_ENTRY,
        OrderLifecycleState.ENTRY_TRIGGERED,
    )


def test_position_open_cannot_go_back_to_waiting_for_entry() -> None:
    with pytest.raises(IllegalLifecycleTransition):
        validate_lifecycle_transition(
            OrderLifecycleState.POSITION_OPEN,
            OrderLifecycleState.WAITING_FOR_ENTRY,
        )


def test_transition_record_is_auditable() -> None:
    transition = make_lifecycle_transition(
        correlation_id="env-123",
        from_state=OrderLifecycleState.VALIDATED,
        to_state=OrderLifecycleState.WAITING_FOR_ENTRY,
        reason="entry_not_reached",
    )

    assert transition.to_dict() == {
        "correlation_id": "env-123",
        "from_state": "VALIDATED",
        "to_state": "WAITING_FOR_ENTRY",
        "reason": "entry_not_reached",
        "timestamp_utc": transition.timestamp_utc,
    }


def test_order_intent_contract_serializes_for_paper_and_live() -> None:
    intent = OrderIntent(
        symbol="BTC/USDT",
        side="BUY",
        order_type="limit",
        entry_type="range",
        entry_value=65250.0,
        entry_min=65000.0,
        entry_max=65500.0,
        quantity=0.1,
        risk_allocation_pct=5.0,
        leverage=10.0,
        margin_mode="isolated",
        stop_loss=64200.0,
        take_profit_targets=(66000.0, 67000.0, 68500.0),
        reduce_only=False,
        source="telegram_premium_channel_approved",
        correlation_id="env-origin",
        idempotency_key="opbridge:env-approved",
    )

    payload = intent.to_dict()

    assert payload["symbol"] == "BTC/USDT"
    assert payload["side"] == "BUY"
    assert payload["order_intent"] == "OPEN_POSITION"
    assert payload["entry_min"] == 65000.0
    assert payload["entry_max"] == 65500.0
    assert payload["leverage"] == 10.0
    assert payload["risk_allocation_pct"] == 5.0
    assert payload["stop_loss"] == 64200.0
    assert payload["take_profit_targets"] == [66000.0, 67000.0, 68500.0]
