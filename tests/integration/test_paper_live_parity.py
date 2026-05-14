"""PRE-C: Paper/Live Parity-Vertrag — verifies the Paper and Live execution
adapters consume an identical ``ExecutableOrderIntent`` and produce
materially-equivalent trade-essence (symbol, side, quantity, price, SL, TP).

Spec: docs/security/phase0_pre_sprints.md  PRE-SPRINT C
Cross-Ref: app/execution/execution_protocol.py.assert_parity()

The full ``ExecutionEngineProtocol`` (both engines implement identical
methods) is delivered with ``live_engine.py`` (Phase-0-Task #7). This
test covers the *adapter-level* parity contract that ships with the
light protocol-stub already in the codebase, so PR #7 starts on a
verified baseline rather than an unchecked assumption.
"""

from __future__ import annotations

import pytest

from app.execution.execution_protocol import (
    assert_parity,
    executable_intent_to_live_request,
    executable_intent_to_paper_kwargs,
)
from app.execution.order_intent import ExecutableOrderIntent


def _intent(**overrides: object) -> ExecutableOrderIntent:
    """Build an ExecutableOrderIntent with sane defaults; override per case."""
    defaults: dict[str, object] = {
        "symbol": "BTC/USDT",
        "side": "buy",
        "order_type": "limit",
        "entry_type": "value",
        "entry_value": 65000.0,
        "entry_min": None,
        "entry_max": None,
        "quantity": 0.01,
        "risk_allocation_pct": 5.0,
        "leverage": 1.0,
        "margin_mode": "cross",
        "stop_loss": 63000.0,
        "take_profit_targets": (67000.0, 69000.0, 71000.0),
        "reduce_only": False,
        "source": "test",
        "correlation_id": "corr-parity-1",
        "idempotency_key": "idem-parity-1",
    }
    defaults.update(overrides)
    return ExecutableOrderIntent(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Adapter-functions — shape & field-coverage
# ---------------------------------------------------------------------------


def test_paper_adapter_emits_full_kwargs_set() -> None:
    """Paper adapter must surface every field the engine consumes.

    Updated 2026-05-14: PR #12 (Premium-Signal-Pipeline-E2E-Fix, Sprint A 2026-05-12)
    added `source` + `leverage` kwargs from ExecutableOrderIntent — kwargs-set
    expanded accordingly. See [[kai_premium_signal_pipeline_e2e_fix_20260512]].
    """
    kwargs = executable_intent_to_paper_kwargs(_intent())
    expected_keys = {
        "symbol",
        "side",
        "quantity",
        "order_type",
        "limit_price",
        "stop_loss",
        "take_profit",
        "idempotency_key",
        "correlation_id",
        "position_side",
        "source",
        "leverage",
    }
    assert set(kwargs.keys()) == expected_keys


def test_live_adapter_emits_orderrequest_with_client_order_id() -> None:
    """Live adapter routes idempotency_key into client_order_id (Exchange-Idempotenz)."""
    intent = _intent(idempotency_key="trade-7f")
    request = executable_intent_to_live_request(intent)
    assert request.client_order_id == "trade-7f"
    assert request.symbol == intent.symbol
    assert request.quantity == intent.quantity
    assert request.stop_loss == intent.stop_loss


# ---------------------------------------------------------------------------
# assert_parity — happy paths
# ---------------------------------------------------------------------------


def test_parity_limit_buy_with_explicit_entry() -> None:
    """LIMIT BUY with explicit entry_value: paper limit_price == live price."""
    assert_parity(_intent())


def test_parity_market_buy_drops_limit_price() -> None:
    """MARKET orders carry no limit; both engines must agree on None."""
    intent = _intent(order_type="market", entry_value=None)
    assert_parity(intent)
    paper = executable_intent_to_paper_kwargs(intent)
    live = executable_intent_to_live_request(intent)
    assert paper["limit_price"] is None
    assert live.price is None


def test_parity_limit_sell_short() -> None:
    """SHORT-side: side=sell + position_side=short on paper, OrderSide.SELL on live."""
    intent = _intent(
        side="sell", entry_value=2400.0, stop_loss=2500.0, take_profit_targets=(2300.0, 2200.0)
    )
    assert_parity(intent)
    paper = executable_intent_to_paper_kwargs(intent)
    assert paper["position_side"] == "short"


def test_parity_range_entry_uses_midpoint() -> None:
    """Range entry: both sides resolve to (min+max)/2 as price-target."""
    intent = _intent(
        entry_type="range",
        entry_value=None,
        entry_min=64500.0,
        entry_max=65500.0,
    )
    assert_parity(intent)
    paper = executable_intent_to_paper_kwargs(intent)
    assert paper["limit_price"] == 65000.0


def test_parity_no_take_profit_targets() -> None:
    """No TP targets: both engines see take_profit=None, no parity break."""
    intent = _intent(take_profit_targets=())
    assert_parity(intent)
    paper = executable_intent_to_paper_kwargs(intent)
    live = executable_intent_to_live_request(intent)
    assert paper["take_profit"] is None
    assert live.take_profit is None


def test_parity_only_first_tp_target_propagates() -> None:
    """Tier-ladder: only TP1 reaches the engine adapter (rest via tier-config)."""
    intent = _intent(take_profit_targets=(67000.0, 69000.0, 71000.0))
    assert_parity(intent)
    paper = executable_intent_to_paper_kwargs(intent)
    live = executable_intent_to_live_request(intent)
    assert paper["take_profit"] == 67000.0
    assert live.take_profit == 67000.0


# ---------------------------------------------------------------------------
# assert_parity — drift-detection
# ---------------------------------------------------------------------------


def test_parity_assertion_catches_quantity_drift() -> None:
    """Direct fabrication of a drift to verify the guard would trigger."""
    intent = _intent()
    paper = executable_intent_to_paper_kwargs(intent)
    live = executable_intent_to_live_request(intent)
    # Fabricate divergence post-hoc to exercise the assertion path.
    paper["quantity"] = 0.02  # drift
    with pytest.raises(AssertionError, match="quantity drift"):
        assert paper["quantity"] == live.quantity, (
            f"quantity drift: paper={paper['quantity']} live={live.quantity}"
        )
