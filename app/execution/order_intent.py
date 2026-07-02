"""ExecutableOrderIntent — parity contract for Paper and Live execution.

This contract replaces the implicit kwargs previously used for order creation
and ensures that both engines (PaperExecutionEngine and live adapters)
consume exactly the same validated intent structure.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutableOrderIntent:
    """Paper/live parity contract for one executable trade intent."""

    symbol: str
    side: str
    order_type: str
    entry_type: str
    entry_value: float | None
    entry_min: float | None
    entry_max: float | None
    quantity: float | None
    risk_allocation_pct: float | None
    leverage: float
    margin_mode: str
    stop_loss: float
    take_profit_targets: tuple[float, ...]
    reduce_only: bool
    source: str
    correlation_id: str
    idempotency_key: str
    order_intent: str = "OPEN_POSITION"
    # Stable originating-signal identity (premium: payload.signal_id) so audit
    # events stay joinable to the business signal; "" when the source has none.
    document_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "entry_type": self.entry_type,
            "entry_value": self.entry_value,
            "entry_min": self.entry_min,
            "entry_max": self.entry_max,
            "quantity": self.quantity,
            "risk_allocation_pct": self.risk_allocation_pct,
            "leverage": self.leverage,
            "margin_mode": self.margin_mode,
            "stop_loss": self.stop_loss,
            "take_profit_targets": list(self.take_profit_targets),
            "reduce_only": self.reduce_only,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "order_intent": self.order_intent,
            "document_id": self.document_id,
        }
