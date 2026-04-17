"""Replay paper-execution audit JSONL back into an in-memory portfolio.

Used by:
- portfolio_read (read-only snapshot projections)
- PaperExecutionEngine.rehydrate_from_audit (state recovery across processes)

The replay honors order_created → order_filled ordering so stop_loss and
take_profit values attached to the order are restored into the resulting
position (they are not repeated on the fill record).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.execution.models import PaperPosition


@dataclass(frozen=True)
class AuditReplayResult:
    positions: dict[str, PaperPosition]
    cash_usd: float
    realized_pnl_usd: float
    available: bool
    error: str | None = None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def replay_paper_audit(audit_path: Path) -> AuditReplayResult:
    """Replay the paper execution audit JSONL into an AuditReplayResult."""
    if not audit_path.exists():
        return AuditReplayResult(
            positions={},
            cash_usd=0.0,
            realized_pnl_usd=0.0,
            available=True,
            error=None,
        )

    positions: dict[str, PaperPosition] = {}
    order_meta: dict[str, tuple[float | None, float | None]] = {}
    cash_usd = 0.0
    realized_pnl_usd = 0.0

    for line_number, raw_line in enumerate(
        audit_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return AuditReplayResult(
                positions={},
                cash_usd=0.0,
                realized_pnl_usd=0.0,
                available=False,
                error=f"audit_json_decode_error_line_{line_number}",
            )

        if not isinstance(payload, dict):
            return AuditReplayResult(
                positions={},
                cash_usd=0.0,
                realized_pnl_usd=0.0,
                available=False,
                error=f"audit_payload_type_error_line_{line_number}",
            )

        event_type = _coerce_str(payload.get("event_type"))
        if event_type == "order_created":
            order_id = _coerce_str(payload.get("order_id"))
            if order_id is not None:
                order_meta[order_id] = (
                    _coerce_float(payload.get("stop_loss")),
                    _coerce_float(payload.get("take_profit")),
                )
            continue

        if event_type != "order_filled":
            continue

        symbol = _coerce_str(payload.get("symbol"))
        side = _coerce_str(payload.get("side"))
        quantity = _coerce_float(payload.get("quantity"))
        fill_price = _coerce_float(payload.get("fill_price"))
        order_id = _coerce_str(payload.get("order_id"))
        filled_at = _coerce_str(payload.get("filled_at")) or datetime.now(UTC).isoformat()

        if (
            symbol is None
            or side not in {"buy", "sell"}
            or quantity is None
            or fill_price is None
            or quantity <= 0.0
            or fill_price <= 0.0
        ):
            return AuditReplayResult(
                positions={},
                cash_usd=0.0,
                realized_pnl_usd=0.0,
                available=False,
                error=f"audit_fill_validation_error_line_{line_number}",
            )

        stop_loss: float | None = None
        take_profit: float | None = None
        if order_id is not None:
            stop_loss, take_profit = order_meta.get(order_id, (None, None))

        existing = positions.get(symbol)
        if side == "buy":
            if existing is None:
                positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=quantity,
                    avg_entry_price=fill_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    opened_at=filled_at,
                    realized_pnl_usd=0.0,
                )
            else:
                total_qty = existing.quantity + quantity
                avg_entry = (
                    (existing.avg_entry_price * existing.quantity) + (fill_price * quantity)
                ) / total_qty
                positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=total_qty,
                    avg_entry_price=avg_entry,
                    stop_loss=stop_loss if stop_loss is not None else existing.stop_loss,
                    take_profit=(take_profit if take_profit is not None else existing.take_profit),
                    opened_at=existing.opened_at,
                    realized_pnl_usd=existing.realized_pnl_usd,
                )
        else:
            if existing is None or existing.quantity + 1e-9 < quantity:
                return AuditReplayResult(
                    positions={},
                    cash_usd=0.0,
                    realized_pnl_usd=0.0,
                    available=False,
                    error=f"audit_sell_without_position_line_{line_number}",
                )
            remaining = existing.quantity - quantity
            if remaining <= 1e-8:
                del positions[symbol]
            else:
                positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=remaining,
                    avg_entry_price=existing.avg_entry_price,
                    stop_loss=existing.stop_loss,
                    take_profit=existing.take_profit,
                    opened_at=existing.opened_at,
                    realized_pnl_usd=existing.realized_pnl_usd,
                )

        portfolio_cash = _coerce_float(payload.get("portfolio_cash"))
        if portfolio_cash is not None:
            cash_usd = portfolio_cash
        realized = _coerce_float(payload.get("realized_pnl_usd"))
        if realized is not None:
            realized_pnl_usd = realized

    return AuditReplayResult(
        positions=positions,
        cash_usd=cash_usd,
        realized_pnl_usd=realized_pnl_usd,
        available=True,
        error=None,
    )
