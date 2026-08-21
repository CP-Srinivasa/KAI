"""Pure finite-number gate and atomic mutation planning for paper fills."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass

from app.execution.models import PaperOrder, PaperPortfolio, PaperPosition, _now_utc

logger = logging.getLogger(__name__)


class PaperExecutionNumberError(ValueError):
    """Numeric-contract failure carrying audit-safe field metadata."""

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"{field} {reason}")


def require_finite_number(
    value: object,
    *,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_inclusive: bool = True,
    maximum_inclusive: bool = True,
) -> float:
    """Normalize a strict int/float while rejecting bool, nonfinite and bounds."""

    if isinstance(value, bool):
        raise PaperExecutionNumberError(field, value, "must not be bool")
    if not isinstance(value, (int, float)):
        raise PaperExecutionNumberError(field, value, "must be an int or float")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise PaperExecutionNumberError(field, value, "must be finite") from exc
    if not math.isfinite(normalized):
        raise PaperExecutionNumberError(field, value, "must be finite")
    if minimum is not None:
        below = normalized < minimum if minimum_inclusive else normalized <= minimum
        if below:
            relation = ">=" if minimum_inclusive else ">"
            raise PaperExecutionNumberError(field, value, f"must be {relation} {minimum}")
    if maximum is not None:
        above = normalized > maximum if maximum_inclusive else normalized >= maximum
        if above:
            relation = "<=" if maximum_inclusive else "<"
            raise PaperExecutionNumberError(field, value, f"must be {relation} {maximum}")
    return normalized


def safe_rejected_value_repr(value: object, *, max_length: int = 160) -> str:
    """Represent a bad value as bounded text; never return the raw object."""

    try:
        rendered = repr(value)
    except Exception:  # noqa: BLE001 - hostile repr must not break rejection audit
        rendered = f"<unrepresentable {type(value).__name__}>"
    return rendered[:max_length]


class PaperFiniteGateMixin:
    """Audit-aware numeric validator shared into the paper engine."""

    _portfolio: PaperPortfolio

    def _append_audit(self, event_type: str, data: Mapping[str, object]) -> None:
        raise NotImplementedError

    def _mutations_blocked_reason(self) -> str | None:
        raise NotImplementedError

    def _audit_execution_rejection(
        self,
        *,
        stage: str,
        error: PaperExecutionNumberError,
        order: PaperOrder | None = None,
        symbol: str = "",
        side: str = "",
        position_side: str = "",
    ) -> None:
        rejection = {
            "stage": stage,
            "field": error.field,
            "reason": error.reason,
            "value_type": type(error.value).__name__,
            "value_repr": safe_rejected_value_repr(error.value),
            "order_id": order.order_id if order else "",
            "idempotency_key": order.idempotency_key if order else "",
            "symbol": order.symbol if order else symbol,
            "side": order.side if order else side,
            "position_side": order.position_side if order else position_side,
            "rejected_at": _now_utc(),
        }
        self._append_audit("paper_execution_rejected", rejection)
        logger.error(
            "[PAPER] Numeric execution rejected: stage=%s field=%s reason=%s "
            "type=%s symbol=%s order_id=%s",
            stage,
            error.field,
            error.reason,
            type(error.value).__name__,
            rejection["symbol"],
            rejection["order_id"],
        )

    def _validated_execution_number(
        self,
        value: object,
        *,
        field: str,
        stage: str,
        minimum: float | None = None,
        maximum: float | None = None,
        minimum_inclusive: bool = True,
        maximum_inclusive: bool = True,
        order: PaperOrder | None = None,
        symbol: str = "",
        side: str = "",
        position_side: str = "",
    ) -> float:
        try:
            return require_finite_number(
                value,
                field=field,
                minimum=minimum,
                maximum=maximum,
                minimum_inclusive=minimum_inclusive,
                maximum_inclusive=maximum_inclusive,
            )
        except PaperExecutionNumberError as error:
            self._audit_execution_rejection(
                stage=stage,
                error=error,
                order=order,
                symbol=symbol,
                side=side,
                position_side=position_side,
            )
            raise

    def set_position_tp_tiers(
        self,
        symbol: str,
        tiers: list[tuple[float, float]],
    ) -> bool:
        """Validate an entire staged-exit ladder before mutating its position."""

        blocked = self._mutations_blocked_reason()
        if blocked is not None:
            logger.warning("[PAPER] set_position_tp_tiers refused (%s): %s", blocked, symbol)
            return False
        position = self._portfolio.positions.get(symbol)
        if position is None:
            return False
        try:
            position_quantity = require_finite_number(
                position.quantity,
                field="position_quantity",
                minimum=0.0,
                minimum_inclusive=False,
            )
            initial_quantity = require_finite_number(
                position.initial_quantity,
                field="initial_position_quantity",
                minimum=0.0,
            )
            normalized_tiers = [
                (
                    require_finite_number(
                        price,
                        field="take_profit_tier_price",
                        minimum=0.0,
                        minimum_inclusive=False,
                    ),
                    require_finite_number(
                        ratio,
                        field="take_profit_tier_ratio",
                        minimum=0.0,
                        maximum=1.0,
                        minimum_inclusive=False,
                    ),
                )
                for price, ratio in tiers
            ]
        except PaperExecutionNumberError as error:
            self._audit_execution_rejection(
                stage="set_position_tp_tiers",
                error=error,
                symbol=symbol,
                position_side=position.position_side,
            )
            raise
        sorted_tiers = sorted(
            normalized_tiers,
            key=lambda item: item[0],
            reverse=position.position_side == "short",
        )
        position.take_profit_tiers = sorted_tiers
        if initial_quantity == 0.0:
            position.initial_quantity = position_quantity
        self._append_audit(
            "position_tp_tiers_set",
            {
                "symbol": symbol,
                "tiers": [{"price": p, "qty_share": q} for p, q in sorted_tiers],
                "initial_quantity": position.initial_quantity,
            },
        )
        logger.info(
            "[PAPER] Tiers set: %s tiers=%s initial_qty=%.6f",
            symbol,
            sorted_tiers,
            position.initial_quantity,
        )
        return True


@dataclass(frozen=True)
class ValidatedOrderNumbers:
    quantity: float
    partial_fill_ratio: float
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    leverage: float | None


def validate_order_numbers(
    *,
    quantity: object,
    partial_fill_ratio: object,
    limit_price: object | None,
    stop_loss: object | None,
    take_profit: object | None,
    leverage: object | None,
) -> ValidatedOrderNumbers:
    """Normalize all numeric order fields before order state is created."""

    normalized: dict[str, float | None] = {}
    for field, value in (
        ("limit_price", limit_price),
        ("stop_loss", stop_loss),
        ("take_profit", take_profit),
        ("leverage", leverage),
    ):
        normalized[field] = (
            None
            if value is None
            else require_finite_number(
                value,
                field=field,
                minimum=0.0,
                minimum_inclusive=False,
            )
        )
    return ValidatedOrderNumbers(
        quantity=require_finite_number(
            quantity,
            field="quantity",
            minimum=0.0,
            minimum_inclusive=False,
        ),
        partial_fill_ratio=require_finite_number(
            partial_fill_ratio,
            field="partial_fill_ratio",
            minimum=0.0,
            maximum=1.0,
            minimum_inclusive=False,
        ),
        limit_price=normalized["limit_price"],
        stop_loss=normalized["stop_loss"],
        take_profit=normalized["take_profit"],
        leverage=normalized["leverage"],
    )


@dataclass(frozen=True)
class PaperFillMutationPlan:
    """Complete post-state for a fill, validated before any portfolio write."""

    cash_before: float
    cash_after: float
    cash_delta: float
    realized_pnl_after: float
    total_fees_after: float
    trade_pnl: float
    position_delta: float
    replacement_position: PaperPosition | None
    remove_position: bool


def _validated_position_state(
    position: PaperPosition,
) -> tuple[float, float, float, float]:
    quantity = require_finite_number(
        position.quantity,
        field="position_quantity",
        minimum=0.0,
        minimum_inclusive=False,
    )
    avg_entry_price = require_finite_number(
        position.avg_entry_price,
        field="avg_entry_price",
        minimum=0.0,
        minimum_inclusive=False,
    )
    realized_pnl = require_finite_number(
        position.realized_pnl_usd,
        field="position_realized_pnl_usd",
    )
    initial_quantity = require_finite_number(
        position.initial_quantity,
        field="initial_position_quantity",
        minimum=0.0,
    )
    for field, value in (
        ("position_stop_loss", position.stop_loss),
        ("position_take_profit", position.take_profit),
        ("position_leverage", position.leverage),
    ):
        if value is not None:
            require_finite_number(
                value,
                field=field,
                minimum=0.0,
                minimum_inclusive=False,
            )
    for price, ratio in position.take_profit_tiers:
        require_finite_number(
            price,
            field="take_profit_tier_price",
            minimum=0.0,
            minimum_inclusive=False,
        )
        require_finite_number(
            ratio,
            field="take_profit_tier_ratio",
            minimum=0.0,
            maximum=1.0,
            minimum_inclusive=False,
        )
    return quantity, avg_entry_price, realized_pnl, initial_quantity


def _opening_replacement(
    *,
    order: PaperOrder,
    fill_price: float,
    fill_quantity: float,
    existing: PaperPosition | None,
) -> PaperPosition:
    if existing is None:
        return PaperPosition(
            symbol=order.symbol,
            quantity=fill_quantity,
            avg_entry_price=fill_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            opened_at=_now_utc(),
            position_side=order.position_side,
            correlation_id=order.correlation_id,
            leverage=order.leverage,
            source=order.source,
            document_id=order.document_id,
            regime=order.regime,
        )

    pos_qty, pos_entry, pos_realized, pos_initial = _validated_position_state(existing)
    total_qty = require_finite_number(
        pos_qty + fill_quantity,
        field="position_quantity",
        minimum=0.0,
        minimum_inclusive=False,
    )
    cost_basis = require_finite_number(
        pos_entry * pos_qty + fill_price * fill_quantity,
        field="position_cost_basis_usd",
        minimum=0.0,
        minimum_inclusive=False,
    )
    avg_price = require_finite_number(
        cost_basis / total_qty,
        field="avg_entry_price",
        minimum=0.0,
        minimum_inclusive=False,
    )
    return PaperPosition(
        symbol=order.symbol,
        quantity=total_qty,
        avg_entry_price=avg_price,
        stop_loss=order.stop_loss or existing.stop_loss,
        take_profit=order.take_profit or existing.take_profit,
        opened_at=existing.opened_at,
        realized_pnl_usd=pos_realized,
        position_side=existing.position_side,
        take_profit_tiers=list(existing.take_profit_tiers),
        initial_quantity=pos_initial,
        correlation_id=existing.correlation_id,
        leverage=existing.leverage,
        source=existing.source,
        document_id=existing.document_id,
        regime=existing.regime,
    )


def _closing_replacement(
    *,
    order: PaperOrder,
    position: PaperPosition,
    remaining_quantity: float,
    trade_pnl: float,
    position_realized_before: float,
    initial_quantity: float,
) -> PaperPosition:
    position_realized = require_finite_number(
        position_realized_before + trade_pnl,
        field="position_realized_pnl_usd",
    )
    return PaperPosition(
        symbol=order.symbol,
        quantity=remaining_quantity,
        avg_entry_price=position.avg_entry_price,
        stop_loss=position.stop_loss,
        take_profit=position.take_profit,
        opened_at=position.opened_at,
        realized_pnl_usd=position_realized,
        position_side=position.position_side,
        take_profit_tiers=list(position.take_profit_tiers),
        initial_quantity=initial_quantity,
        correlation_id=position.correlation_id,
        leverage=position.leverage,
        source=position.source,
        document_id=position.document_id,
        regime=position.regime,
    )


def build_fill_mutation_plan(
    *,
    order: PaperOrder,
    portfolio: PaperPortfolio,
    requested_quantity: float,
    fill_quantity: float,
    fill_price: float,
    cost: float,
    fee: float,
) -> tuple[PaperFillMutationPlan | None, str | None]:
    """Compute all post-state or return a non-numeric business rejection."""

    cash_before = require_finite_number(
        portfolio.cash,
        field="portfolio_cash",
        minimum=0.0,
    )
    realized_before = require_finite_number(
        portfolio.realized_pnl_usd,
        field="realized_pnl_usd",
    )
    total_fees_before = require_finite_number(
        portfolio.total_fees_usd,
        field="total_fees_usd",
        minimum=0.0,
    )
    total_fees_after = require_finite_number(
        total_fees_before + fee,
        field="total_fees_usd",
        minimum=0.0,
    )

    cash_after = cash_before
    realized_after = realized_before
    cash_delta = 0.0
    position_delta = 0.0
    trade_pnl = 0.0
    replacement: PaperPosition | None = None
    remove_position = False
    position = portfolio.positions.get(order.symbol)

    if order.position_side == "long" and order.side == "buy":
        if position is not None and position.position_side != "long":
            return None, "long_short_side_conflict"
        cash_delta = require_finite_number(
            -(cost + fee),
            field="cash_delta_usd",
            maximum=0.0,
            maximum_inclusive=False,
        )
        if cash_before < -cash_delta:
            return None, "insufficient_cash"
        cash_after = require_finite_number(
            cash_before + cash_delta,
            field="portfolio_cash",
            minimum=0.0,
        )
        position_delta = require_finite_number(
            fill_quantity,
            field="position_delta",
            minimum=0.0,
            minimum_inclusive=False,
        )
        replacement = _opening_replacement(
            order=order,
            fill_price=fill_price,
            fill_quantity=position_delta,
            existing=position,
        )
    elif order.position_side == "short" and order.side == "sell":
        if position is not None and position.position_side != "short":
            return None, "short_long_side_conflict"
        cash_delta = require_finite_number(
            cost - fee,
            field="cash_delta_usd",
            minimum=0.0,
        )
        cash_after = require_finite_number(
            cash_before + cash_delta,
            field="portfolio_cash",
            minimum=0.0,
        )
        position_delta = require_finite_number(
            fill_quantity,
            field="position_delta",
            minimum=0.0,
            minimum_inclusive=False,
        )
        replacement = _opening_replacement(
            order=order,
            fill_price=fill_price,
            fill_quantity=position_delta,
            existing=position,
        )
    elif (
        order.position_side == "long"
        and order.side == "sell"
        or order.position_side == "short"
        and order.side == "buy"
    ):
        if position is None or position.position_side != order.position_side:
            return None, "insufficient_position"
        pos_qty, pos_entry, pos_realized, pos_initial = _validated_position_state(position)
        if pos_qty < requested_quantity:
            return None, "insufficient_position"
        is_long_close = order.position_side == "long"
        cash_change = cost - fee if is_long_close else -(cost + fee)
        cash_delta = require_finite_number(
            cash_change,
            field="cash_delta_usd",
            minimum=0.0 if is_long_close else None,
            maximum=0.0 if not is_long_close else None,
            maximum_inclusive=False,
        )
        if not is_long_close and cash_before < -cash_delta:
            return None, "insufficient_cash"
        cash_after = require_finite_number(
            cash_before + cash_delta,
            field="portfolio_cash",
            minimum=0.0,
        )
        pnl_expression = (
            (fill_price - pos_entry) * fill_quantity - fee
            if is_long_close
            else (pos_entry - fill_price) * fill_quantity - fee
        )
        trade_pnl = require_finite_number(
            pnl_expression,
            field="realized_pnl_delta_usd",
        )
        realized_after = require_finite_number(
            realized_before + trade_pnl,
            field="realized_pnl_usd",
        )
        position_delta = require_finite_number(
            -fill_quantity,
            field="position_delta",
            maximum=0.0,
            maximum_inclusive=False,
        )
        remaining = require_finite_number(
            pos_qty + position_delta,
            field="position_quantity",
            minimum=0.0,
        )
        if remaining <= 1e-8:
            remove_position = True
        else:
            replacement = _closing_replacement(
                order=order,
                position=position,
                remaining_quantity=remaining,
                trade_pnl=trade_pnl,
                position_realized_before=pos_realized,
                initial_quantity=pos_initial,
            )
    else:
        return None, "unsupported_side_position_side"

    expected_cash_outflow = cash_delta < 0.0
    cash_delta = require_finite_number(
        cash_after - cash_before,
        field="cash_delta_usd",
        minimum=0.0 if not expected_cash_outflow else None,
        maximum=0.0 if expected_cash_outflow else None,
        maximum_inclusive=not expected_cash_outflow,
    )
    return (
        PaperFillMutationPlan(
            cash_before=cash_before,
            cash_after=cash_after,
            cash_delta=cash_delta,
            realized_pnl_after=realized_after,
            total_fees_after=total_fees_after,
            trade_pnl=trade_pnl,
            position_delta=position_delta,
            replacement_position=replacement,
            remove_position=remove_position,
        ),
        None,
    )
