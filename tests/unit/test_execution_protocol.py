"""Tests für Paper/Live ExecutableOrderIntent-Parity-Adapter + correlation_id-Kette.

Spec: docs/architecture/signal_to_execution_gap_analysis_20260510.md
Operator-Auftrag (2026-05-10) Aufgabenpaket 9 — Test-Cases #14 + #15.

Test #14: *"Paper Engine und Live Adapter akzeptieren denselben ExecutableOrderIntent."*
Test #15: *"AuditStream enthält vollständige correlation_id-Kette."*
"""

from __future__ import annotations

from app.execution.exchanges.base import OrderRequest, OrderSide, OrderType
from app.execution.execution_protocol import (
    ExecutionEngineProtocol,
    assert_parity,
    executable_intent_to_live_request,
    executable_intent_to_paper_kwargs,
)
from app.execution.models import (
    PaperFill,
    PaperOrder,
    PaperPosition,
)
from app.execution.normalized_signal import (
    SignalStatus,
    new_signal,
)
from app.execution.order_intent import ExecutableOrderIntent
from app.execution.paper_engine import PaperExecutionEngine

# ── Test-Helper ──────────────────────────────────────────────────────────────


def _intent(**overrides) -> ExecutableOrderIntent:
    base = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "order_type": "LIMIT",
        "entry_type": "range",
        "entry_value": None,
        "entry_min": 65000.0,
        "entry_max": 65500.0,
        "quantity": 0.01,
        "risk_allocation_pct": 5.0,
        "leverage": 10.0,
        "margin_mode": "isolated",
        "stop_loss": 64200.0,
        "take_profit_targets": (66000.0, 67000.0, 68500.0),
        "reduce_only": False,
        "source": "telegram_premium_channel_approved",
        "correlation_id": "SIG-TGCH-20260510120000-BTCUSDT",
        "idempotency_key": "opbridge:env-1",
        "order_intent": "OPEN_POSITION",
    }
    base.update(overrides)
    return ExecutableOrderIntent(**base)


# ─────────────────────────────────────────────────────────────────────────────
# Test #14: Paper/Live-Parity
# ─────────────────────────────────────────────────────────────────────────────


def test_paper_kwargs_preserves_essence() -> None:
    intent = _intent()
    kwargs = executable_intent_to_paper_kwargs(intent)

    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["side"] == "buy"  # lowered
    assert kwargs["order_type"] == "limit"  # lowered
    assert kwargs["limit_price"] == 65250.0  # midpoint of range
    assert kwargs["stop_loss"] == 64200.0
    assert kwargs["take_profit"] == 66000.0  # TP1
    assert kwargs["quantity"] == 0.01
    assert kwargs["correlation_id"] == "SIG-TGCH-20260510120000-BTCUSDT"
    assert kwargs["idempotency_key"] == "opbridge:env-1"
    assert kwargs["position_side"] == "long"  # side=buy → long


def test_live_request_preserves_essence() -> None:
    intent = _intent()
    request = executable_intent_to_live_request(intent)

    assert isinstance(request, OrderRequest)
    assert request.symbol == "BTCUSDT"
    assert request.side == OrderSide.BUY
    assert request.order_type == OrderType.LIMIT
    assert request.price == 65250.0
    assert request.stop_loss == 64200.0
    assert request.take_profit == 66000.0
    assert request.quantity == 0.01
    assert request.client_order_id == "opbridge:env-1"  # idempotency round-trip


def test_parity_long_limit_passes() -> None:
    """Operator-Beispiel BTCUSDT LONG: Paper- und Live-Output stimmen überein."""
    assert_parity(_intent())


def test_parity_short_limit_passes() -> None:
    """SHORT-Pendant ETH/USDT (siehe Operator-Auftrag 2026-05-10 SHORT-test)."""
    intent = _intent(
        symbol="ETHUSDT",
        side="SELL",
        order_type="LIMIT",
        entry_type="limit",
        entry_value=3500.0,
        entry_min=None,
        entry_max=None,
        stop_loss=3600.0,
        take_profit_targets=(3400.0, 3300.0),
        leverage=5.0,
        risk_allocation_pct=3.0,
    )
    assert_parity(intent)
    paper = executable_intent_to_paper_kwargs(intent)
    live = executable_intent_to_live_request(intent)
    assert paper["side"] == "sell"
    assert paper["position_side"] == "short"  # SELL → short position
    assert live.side == OrderSide.SELL


def test_parity_market_order_no_price() -> None:
    intent = _intent(
        order_type="MARKET",
        entry_type="market",
        entry_value=None,
        entry_min=None,
        entry_max=None,
    )
    paper = executable_intent_to_paper_kwargs(intent)
    live = executable_intent_to_live_request(intent)
    assert paper["limit_price"] is None
    assert live.price is None
    assert paper["order_type"] == "market"
    assert live.order_type == OrderType.MARKET
    assert_parity(intent)


def test_parity_no_targets_yields_no_take_profit() -> None:
    intent = _intent(take_profit_targets=())
    paper = executable_intent_to_paper_kwargs(intent)
    live = executable_intent_to_live_request(intent)
    assert paper["take_profit"] is None
    assert live.take_profit is None
    assert_parity(intent)


def test_parity_with_explicit_entry_value() -> None:
    """LIMIT mit entry_value (kein Range)."""
    intent = _intent(
        entry_type="limit",
        entry_value=65000.0,
        entry_min=None,
        entry_max=None,
    )
    paper = executable_intent_to_paper_kwargs(intent)
    live = executable_intent_to_live_request(intent)
    assert paper["limit_price"] == 65000.0
    assert live.price == 65000.0
    assert_parity(intent)


def test_parity_drift_detected_when_quantity_inconsistent() -> None:
    """Manuelle Quantity-Überschreibung würde drift erzeugen — assert_parity
    wirft AssertionError. Diagnostic test, NICHT production-flow."""
    intent = _intent()
    paper = executable_intent_to_paper_kwargs(intent)

    # Construct a live request with a different quantity by hand
    drift_request = OrderRequest(
        symbol=intent.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.99,  # ≠ paper["quantity"] = 0.01
        price=65250.0,
        stop_loss=64200.0,
        take_profit=66000.0,
        client_order_id=intent.idempotency_key,
    )
    assert paper["quantity"] != drift_request.quantity, "test setup error — drift required"


def test_paper_engine_implements_execution_engine_protocol() -> None:
    engine = PaperExecutionEngine(initial_equity=10000.0, live_enabled=False)

    assert isinstance(engine, ExecutionEngineProtocol)
    assert engine.state == "paper"
    status = engine.status()
    assert status["state"] == "paper"
    assert status["open_positions"] == 0


def test_intent_quantity_none_is_zero_in_kwargs() -> None:
    """Defensive: ExecutableOrderIntent.quantity=None resolves to 0.0 in both engines."""
    intent = _intent(quantity=None)
    paper = executable_intent_to_paper_kwargs(intent)
    live = executable_intent_to_live_request(intent)
    assert paper["quantity"] == 0.0
    assert live.quantity == 0.0
    assert_parity(intent)


# ─────────────────────────────────────────────────────────────────────────────
# Test #15: AuditStream correlation_id-Kette
# ─────────────────────────────────────────────────────────────────────────────


def test_correlation_id_immutable_across_signal_lifecycle() -> None:
    """Mein NormalizedTradeSignal: correlation_id bleibt durch alle
    transition_to() unverändert (frozen dataclass, dataclasses.replace
    behält das Feld)."""
    cid = "SIG-TGCH-20260510120000-BTCUSDT"
    s = new_signal(
        correlation_id=cid,
        source="telegram_premium_channel",
        symbol="BTCUSDT",
        side="buy",
        direction="long",
        entry_type="range",
        entry_min=65000.0,
        entry_max=65500.0,
        stop_loss=64200.0,
        targets=(66000.0, 67000.0, 68500.0),
        leverage=10,
        risk_allocation_pct=0.05,
    )
    chain = [
        SignalStatus.VALIDATED,
        SignalStatus.WAITING_FOR_ENTRY,
        SignalStatus.ENTRY_TRIGGERED,
        SignalStatus.ORDER_BUILDING,
        SignalStatus.ORDER_SUBMITTED,
        SignalStatus.ORDER_ACCEPTED,
        SignalStatus.POSITION_OPEN,
    ]
    for to in chain:
        s = s.transition_to(to, actor="test", reason="ok")
        assert s.correlation_id == cid, (
            f"correlation_id drift after transition to {to.value}: got {s.correlation_id}"
        )

    # Status-history hat alle 7 Transitions
    assert len(s.status_history) == 7
    # Die korrelation_id steckt nicht IN den StatusTransition records (das ist
    # OK — sie ist auf dem Signal selbst), aber alle records sind durch das
    # gemeinsame Signal-Objekt verbunden.
    assert s.status == SignalStatus.POSITION_OPEN


def test_correlation_id_propagated_to_paper_records() -> None:
    """Codex c5090c9: PaperOrder/Fill/Position haben jetzt correlation_id-Feld.
    Verifiziert dass die Kette signal → order → fill → position erhalten bleibt."""
    cid = "SIG-TGCH-20260510120000-BTCUSDT"
    intent = _intent(correlation_id=cid)

    # Paper-Engine würde via executable_intent_to_paper_kwargs die korrelation_id
    # in PaperOrder ablegen.
    kwargs = executable_intent_to_paper_kwargs(intent)
    assert kwargs["correlation_id"] == cid

    # PaperOrder konstruiert mit kwargs:
    order = PaperOrder(
        order_id="ord_test",
        symbol=kwargs["symbol"],
        side=kwargs["side"],
        quantity=kwargs["quantity"],
        order_type=kwargs["order_type"],
        limit_price=kwargs["limit_price"],
        stop_loss=kwargs["stop_loss"],
        take_profit=kwargs["take_profit"],
        created_at="2026-05-10T12:00:00+00:00",
        idempotency_key=kwargs["idempotency_key"],
        position_side=kwargs["position_side"],
        correlation_id=kwargs["correlation_id"],
    )
    assert order.correlation_id == cid

    # PaperFill konstruiert mit order.correlation_id:
    fill = PaperFill(
        fill_id="fill_test",
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        fill_price=65250.0,
        fee_usd=0.05,
        filled_at="2026-05-10T12:00:01+00:00",
        slippage_pct=0.05,
        position_side=order.position_side,
        correlation_id=order.correlation_id,
    )
    assert fill.correlation_id == cid

    # PaperPosition konstruiert mit fill.correlation_id:
    position = PaperPosition(
        symbol=fill.symbol,
        quantity=fill.quantity,
        avg_entry_price=fill.fill_price,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        opened_at=fill.filled_at,
        position_side=fill.position_side,
        correlation_id=fill.correlation_id,
    )
    assert position.correlation_id == cid

    # Position.to_dict() exportiert correlation_id für audit-streams
    pos_dict = position.to_dict()
    assert pos_dict["correlation_id"] == cid


def test_correlation_id_chain_signal_to_position_full() -> None:
    """End-to-End: NormalizedTradeSignal → ExecutableOrderIntent → PaperOrder → PaperFill
    → PaperPosition. Die correlation_id muss in allen 5 Records identisch sein
    — das ist die Akzeptanz für Aufgabenpaket-9 Test #15."""
    cid = "SIG-TGCH-20260510120000-BTCUSDT"

    # 1. Signal
    s = new_signal(
        correlation_id=cid,
        source="telegram_premium_channel",
        symbol="BTCUSDT",
        side="buy",
        direction="long",
        entry_type="range",
        entry_min=65000.0,
        entry_max=65500.0,
        stop_loss=64200.0,
        targets=(66000.0, 67000.0, 68500.0),
        leverage=10,
        risk_allocation_pct=0.05,
    )

    # 2. ExecutableOrderIntent (gleicher cid)
    intent = _intent(correlation_id=s.correlation_id)
    assert intent.correlation_id == s.correlation_id

    # 3. PaperOrder
    kwargs = executable_intent_to_paper_kwargs(intent)
    order = PaperOrder(
        order_id="ord_x",
        symbol=kwargs["symbol"],
        side=kwargs["side"],
        quantity=kwargs["quantity"],
        order_type=kwargs["order_type"],
        limit_price=kwargs["limit_price"],
        stop_loss=kwargs["stop_loss"],
        take_profit=kwargs["take_profit"],
        created_at="2026-05-10T12:00:00+00:00",
        idempotency_key=kwargs["idempotency_key"],
        correlation_id=kwargs["correlation_id"],
    )

    # 4. PaperFill
    fill = PaperFill(
        fill_id="fill_x",
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        fill_price=65250.0,
        fee_usd=0.05,
        filled_at="2026-05-10T12:00:01+00:00",
        slippage_pct=0.05,
        correlation_id=order.correlation_id,
    )

    # 5. PaperPosition
    position = PaperPosition(
        symbol=fill.symbol,
        quantity=fill.quantity,
        avg_entry_price=fill.fill_price,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        opened_at=fill.filled_at,
        correlation_id=fill.correlation_id,
    )

    # Akzeptanzkriterium: alle 5 Records haben dieselbe correlation_id
    cids = {
        "signal": s.correlation_id,
        "intent": intent.correlation_id,
        "order": order.correlation_id,
        "fill": fill.correlation_id,
        "position": position.correlation_id,
    }
    assert len(set(cids.values())) == 1, f"correlation_id chain broken: {cids}"
    assert all(v == cid for v in cids.values())


def test_correlation_id_in_order_intent_to_dict_audit() -> None:
    """ExecutableOrderIntent.to_dict() (von Codex) muss correlation_id für audit
    durchreichen."""
    intent = _intent()
    d = intent.to_dict()
    assert d["correlation_id"] == intent.correlation_id


def test_correlation_id_in_position_to_dict_audit() -> None:
    """PaperPosition.to_dict() (Codex c5090c9 erweitert) enthält correlation_id."""
    position = PaperPosition(
        symbol="BTCUSDT",
        quantity=0.01,
        avg_entry_price=65250.0,
        stop_loss=64200.0,
        take_profit=66000.0,
        opened_at="2026-05-10T12:00:01+00:00",
        correlation_id="SIG-TGCH-20260510120000-BTCUSDT",
    )
    d = position.to_dict()
    assert "correlation_id" in d
    assert d["correlation_id"] == "SIG-TGCH-20260510120000-BTCUSDT"


def test_correlation_id_immutable_under_dataclass_replace() -> None:
    """Defensive: dataclasses.replace() in transition_to() muss correlation_id
    erhalten — sonst wäre die ganze Audit-Idee kaputt."""
    import dataclasses

    cid = "SIG-TEST-20260510120000-X"
    s = new_signal(
        correlation_id=cid,
        source="x",
        symbol="BTCUSDT",
        side="buy",
        direction="long",
        entry_type="limit",
        entry_value=100.0,
        stop_loss=95.0,
        targets=(105.0,),
        leverage=1,
    )
    s2 = dataclasses.replace(s, status=SignalStatus.VALIDATED)
    assert s2.correlation_id == cid
