"""Paper/Live ExecutionEngine Parity-Adapter — converts ``OrderIntent``
(Aufgabenpaket-6 Pflicht-Vertrag) to engine-specific call shapes.

Spec: docs/architecture/signal_to_execution_gap_analysis_20260510.md
      Aufgabenpaket 6 + 9 Test-Case 14 (Operator-Auftrag 2026-05-10).
Pre-Sprint C: docs/security/phase0_pre_sprints.md (light version — adapter
functions, vollständiges Protocol kommt mit live_engine.py).

Why
---
Aufgabenpaket-9 Test #14: *"Paper Engine und Live Adapter akzeptieren
denselben OrderIntent."* Today both sides have **incompatible** input
shapes:

- ``PaperExecutionEngine.create_order(symbol, side, quantity, ...)`` —
  kwargs-based.
- ``BaseExchangeAdapter.place_order(OrderRequest(...))`` — frozen
  dataclass with separate enum types (``OrderSide``, ``OrderType``).

This module bridges via two pure adapter-functions that take a single
``OrderIntent`` and emit the engine-specific shape. Tests can then
verify *parity* — same intent → consistent symbol/side/quantity/SL/TP
on both engines.

Full ``ExecutionEngineProtocol`` (Pre-Sprint C, both engines implement
identical methods) is left for the cycle that introduces ``live_engine.py``.
"""

from __future__ import annotations

from typing import Any

from app.execution.exchanges.base import OrderRequest, OrderSide, OrderType
from app.execution.models import OrderIntent


def order_intent_to_paper_kwargs(intent: OrderIntent) -> dict[str, Any]:
    """Translate ``OrderIntent`` → kwargs for ``PaperExecutionEngine.create_order``.

    Mapping
    -------
    - ``symbol`` → ``symbol`` (1:1)
    - ``side`` (BUY/SELL) → ``side`` (lowercase "buy"/"sell")
    - ``order_type`` (LIMIT/MARKET) → ``order_type`` (lowercase "limit"/"market")
    - ``entry_value`` (or midpoint of range) → ``limit_price`` (None for market)
    - ``stop_loss`` → ``stop_loss``
    - ``take_profit_targets[0]`` → ``take_profit`` (TP1; staged exits via tiers)
    - ``idempotency_key`` → ``idempotency_key``
    - ``correlation_id`` → ``correlation_id``
    - ``quantity`` → ``quantity`` (Risk-Engine sizing happens upstream — this
      adapter respects whatever quantity the OrderIntent already carries)
    - ``side="sell"`` + native short → ``position_side="short"``
    """
    side_lower = str(intent.side).lower()
    order_type_lower = str(intent.order_type).lower()

    # Determine limit_price: explicit entry for limit orders, None for market.
    if order_type_lower == "market":
        limit_price: float | None = None
    elif intent.entry_value is not None:
        limit_price = intent.entry_value
    elif intent.entry_min is not None and intent.entry_max is not None:
        # Range entry → midpoint as paper-engine limit-target.
        limit_price = (intent.entry_min + intent.entry_max) / 2.0
    else:
        limit_price = None

    # TP1 from staged targets (paper-engine native single-TP; tier-ladder is
    # set separately via engine.set_position_tp_tiers).
    tp_first = (
        intent.take_profit_targets[0]
        if intent.take_profit_targets
        else None
    )

    # SHORT-positions on paper require both side=sell + position_side=short.
    # Operator's signal-direction is encoded in OrderIntent.side already.
    position_side = "short" if side_lower == "sell" else "long"

    return {
        "symbol": intent.symbol,
        "side": side_lower,
        "quantity": intent.quantity if intent.quantity is not None else 0.0,
        "order_type": order_type_lower,
        "limit_price": limit_price,
        "stop_loss": intent.stop_loss,
        "take_profit": tp_first,
        "idempotency_key": intent.idempotency_key,
        "correlation_id": intent.correlation_id,
        "position_side": position_side,
    }


def order_intent_to_live_request(intent: OrderIntent) -> OrderRequest:
    """Translate ``OrderIntent`` → ``OrderRequest`` for ``BaseExchangeAdapter.place_order``.

    Mapping
    -------
    - ``symbol`` → ``symbol``
    - ``side`` (lower str) → ``OrderSide`` (BUY/SELL enum)
    - ``order_type`` (lower str) → ``OrderType`` (MARKET/LIMIT enum)
    - ``entry_value`` (or range-mid) → ``price`` (None for market)
    - ``stop_loss`` → ``stop_loss``
    - ``take_profit_targets[0]`` → ``take_profit`` (TP1)
    - ``idempotency_key`` → ``client_order_id`` (Live-Exchange-Idempotenz)
    - ``quantity`` → ``quantity``

    The ``correlation_id`` is NOT a field of ``OrderRequest`` (it is a
    KAI-internal trace concept). It is preserved on the ``OrderResult``
    audit trail via the ``client_order_id`` round-trip.
    """
    side_norm = str(intent.side).lower()
    order_type_norm = str(intent.order_type).lower()

    side_enum = OrderSide.BUY if side_norm == "buy" else OrderSide.SELL

    if order_type_norm == "market":
        type_enum = OrderType.MARKET
        price: float | None = None
    elif order_type_norm == "limit":
        type_enum = OrderType.LIMIT
        if intent.entry_value is not None:
            price = intent.entry_value
        elif intent.entry_min is not None and intent.entry_max is not None:
            price = (intent.entry_min + intent.entry_max) / 2.0
        else:
            price = None
    else:
        # stop_market or other types — map conservatively to LIMIT/MARKET.
        # Phase-0 spec restricts to spot LIMIT + OCO; other types come later.
        type_enum = OrderType.LIMIT
        price = intent.entry_value

    tp_first = (
        intent.take_profit_targets[0]
        if intent.take_profit_targets
        else None
    )

    return OrderRequest(
        symbol=intent.symbol,
        side=side_enum,
        order_type=type_enum,
        quantity=intent.quantity if intent.quantity is not None else 0.0,
        price=price,
        stop_loss=intent.stop_loss,
        take_profit=tp_first,
        client_order_id=intent.idempotency_key,
    )


def assert_parity(intent: OrderIntent) -> None:
    """Assert that paper-kwargs and live-request agree on the trade-essence.

    Used by test_paper_live_parity (Aufgabenpaket-9 Test #14). Raises
    ``AssertionError`` if the same OrderIntent would result in materially
    different fills on paper vs live.

    Compared fields: symbol, side (case-normalized), quantity, limit/price,
    stop_loss, take_profit (TP1).
    """
    paper = order_intent_to_paper_kwargs(intent)
    live = order_intent_to_live_request(intent)

    assert paper["symbol"] == live.symbol, (
        f"symbol drift: paper={paper['symbol']!r} live={live.symbol!r}"
    )
    assert paper["side"] == str(live.side).lower(), (
        f"side drift: paper={paper['side']!r} live={live.side!r}"
    )
    assert paper["quantity"] == live.quantity, (
        f"quantity drift: paper={paper['quantity']} live={live.quantity}"
    )
    assert paper["limit_price"] == live.price, (
        f"limit_price drift: paper={paper['limit_price']} live={live.price}"
    )
    assert paper["stop_loss"] == live.stop_loss, (
        f"stop_loss drift: paper={paper['stop_loss']} live={live.stop_loss}"
    )
    assert paper["take_profit"] == live.take_profit, (
        f"take_profit drift: paper={paper['take_profit']} live={live.take_profit}"
    )
    assert paper["idempotency_key"] == live.client_order_id, (
        f"idempotency drift: paper={paper['idempotency_key']!r} "
        f"live={live.client_order_id!r}"
    )


__all__ = [
    "assert_parity",
    "order_intent_to_live_request",
    "order_intent_to_paper_kwargs",
]
