"""Paper Execution Engine — simulated order execution with realistic fills.

Design invariants:
- live_enabled must be False (checked at init)
- All orders written to audit JSONL before execution
- All fills are idempotent (idempotency_key deduplication)
- Position sizing comes from Risk Engine — never self-calculated
- No mutation of immutable order records
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import portalocker

from app.execution.audit_replay import replay_paper_audit
from app.execution.execution_protocol import executable_intent_to_paper_kwargs
from app.execution.models import (
    OrderLifecycleState,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    _new_fill_id,
    _new_order_id,
    _now_utc,
    make_lifecycle_transition,
)
from app.execution.order_intent import ExecutableOrderIntent

logger = logging.getLogger(__name__)

_AUDIT_LOG = Path("artifacts/paper_execution_audit.jsonl")


# NEO-P-005: event-types the paper engine broadcasts to the dashboard SSE
# bus. We map internal audit-names to public event-names so the dashboard can
# stay stable if the audit schema ever gets a rename.
_SSE_EVENT_MAP = {
    "order_filled": "fill_settled",
    "position_closed": "position_closed",
}


def _publish_paper_event(event_type: str, record: dict[str, object]) -> None:
    sse_event = _SSE_EVENT_MAP.get(event_type)
    if sse_event is None:
        return
    try:
        from app.api.event_hub import get_default_event_hub

        get_default_event_hub().publish(sse_event, record)
    except Exception:  # noqa: BLE001 — publish must never fail the engine
        pass


class PaperExecutionEngine:
    """
    Simulated order execution engine for paper trading.

    Invariants:
    - live_enabled=False enforced at construction
    - Orders logged to JSONL before fill
    - Fills deduped by idempotency_key
    - Stop-loss and take-profit checked on price update
    """

    def __init__(
        self,
        *,
        initial_equity: float = 10000.0,
        fee_pct: float = 0.1,
        slippage_pct: float = 0.05,
        live_enabled: bool = False,
        audit_log_path: str | None = None,
    ) -> None:
        if live_enabled:
            raise ValueError(
                "PaperExecutionEngine: live_enabled=True is not allowed. "
                "Use a live execution adapter instead."
            )
        self._fee_pct = fee_pct / 100
        self._slippage_pct = slippage_pct / 100
        self._portfolio = PaperPortfolio(initial_equity=initial_equity, cash=initial_equity)
        self._filled_keys: set[str] = set()
        self._audit_path = Path(audit_log_path or _AUDIT_LOG)
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("[PAPER] Engine initialized. equity=%.2f", initial_equity)

    @property
    def portfolio(self) -> PaperPortfolio:
        return self._portfolio

    def rehydrate_from_audit(self, audit_path: str | Path | None = None) -> bool:
        """Replay the audit JSONL and replace in-memory portfolio state.

        Necessary for cross-process continuity (e.g. cron-driven runs) where
        a fresh engine must observe previously opened positions. Returns True
        on success, False on replay error (engine state left unchanged).
        """
        path = Path(audit_path) if audit_path is not None else self._audit_path
        result = replay_paper_audit(path)
        if not result.available:
            logger.warning("[PAPER] audit replay failed: %s", result.error)
            return False
        self._portfolio.positions = dict(result.positions)
        if result.cash_usd:
            self._portfolio.cash = result.cash_usd
        self._portfolio.realized_pnl_usd = result.realized_pnl_usd
        return True

    def execute_intent(
        self,
        intent: ExecutableOrderIntent,
        current_price: float,
        risk_check_id: str,
    ) -> tuple[PaperOrder, PaperFill | None]:
        """Parity interface: execute an ExecutableOrderIntent on paper."""
        kwargs = executable_intent_to_paper_kwargs(intent)
        kwargs["risk_check_id"] = risk_check_id

        order = self.create_order(**kwargs)
        # Limit or market? In paper, fill_order requires current_price.
        # We pass the current_price regardless of type.
        fill = self.fill_order(order, current_price)
        return order, fill

    def create_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        limit_price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        idempotency_key: str | None = None,
        risk_check_id: str = "",
        position_side: str = "long",
        venue: str = "paper",
        correlation_id: str = "",
    ) -> PaperOrder:
        """Create an order record (does not fill immediately).

        position_side defaults to "long". Use side="sell", position_side="short"
        to open/increase a simulated short, and side="buy", position_side="short"
        to close/reduce it.

        NEO-P-106 Phase 2: venue defaults to "paper" (= worst-case default fee).
        Callers that need constructor fee_pct compatibility must pass
        venue="legacy" explicitly.
        """
        if position_side not in {"long", "short"}:
            raise ValueError("position_side must be 'long' or 'short'")
        idem_key = idempotency_key or f"{symbol}_{side}_{_now_utc()}"
        order = PaperOrder(
            order_id=_new_order_id(),
            symbol=symbol,
            side=side.lower(),
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            created_at=_now_utc(),
            idempotency_key=idem_key,
            risk_check_id=risk_check_id,
            position_side=position_side,
            venue=venue,
            correlation_id=correlation_id,
        )
        self._append_audit("order_created", order.__dict__)
        if correlation_id:
            try:
                t = make_lifecycle_transition(
                    correlation_id=correlation_id,
                    from_state=OrderLifecycleState.ORDER_BUILDING,
                    to_state=OrderLifecycleState.ORDER_SUBMITTED,
                    reason="paper_order_created",
                )
                self._append_audit("lifecycle_transition", t.to_dict())
            except Exception as e:
                logger.error("[PAPER] Failed to emit lifecycle transition ORDER_SUBMITTED: %s", e)
        return order

    def fill_order(
        self, order: PaperOrder, current_price: float
    ) -> PaperFill | None:
        """
        Execute a paper fill for an order.
        Returns None if: duplicate (idempotency), insufficient cash, or invalid price.
        """
        if order.idempotency_key in self._filled_keys:
            logger.warning("[PAPER] Duplicate order rejected: key=%s", order.idempotency_key)
            return None

        if order.position_side not in {"long", "short"}:
            raise ValueError("position_side must be 'long' or 'short'")

        if current_price <= 0:
            logger.error(
                "[PAPER] Invalid price for fill: %s price=%.2f", order.symbol, current_price
            )
            return None

        # Defense-in-depth against inverted stops — the Risk Engine owns the
        # primary geometry gate, but if it is ever bypassed we still refuse
        # geometrically impossible open orders.
        if order.side == "buy" and order.position_side == "long":
            if order.stop_loss is not None and order.stop_loss >= current_price:
                logger.error(
                    "[PAPER] Rejected fill — long SL at or above current price: "
                    "%s sl=%.4f price=%.4f",
                    order.symbol,
                    order.stop_loss,
                    current_price,
                )
                self._append_audit(
                    "order_rejected_invalid_sl",
                    {
                        "order_id": order.order_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "stop_loss": order.stop_loss,
                        "current_price": current_price,
                        "reason": "long_sl_at_or_above_price",
                    },
                )
                return None
            if order.take_profit is not None and order.take_profit <= current_price:
                logger.error(
                    "[PAPER] Rejected fill — long TP at or below current price: "
                    "%s tp=%.4f price=%.4f",
                    order.symbol,
                    order.take_profit,
                    current_price,
                )
                self._append_audit(
                    "order_rejected_invalid_tp",
                    {
                        "order_id": order.order_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "take_profit": order.take_profit,
                        "current_price": current_price,
                        "reason": "long_tp_at_or_below_price",
                    },
                )
                return None
        if order.side == "sell" and order.position_side == "short":
            if order.stop_loss is not None and order.stop_loss <= current_price:
                logger.error(
                    "[PAPER] Rejected fill — short SL at or below current price: "
                    "%s sl=%.4f price=%.4f",
                    order.symbol,
                    order.stop_loss,
                    current_price,
                )
                self._append_audit(
                    "order_rejected_invalid_sl",
                    {
                        "order_id": order.order_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "position_side": order.position_side,
                        "stop_loss": order.stop_loss,
                        "current_price": current_price,
                        "reason": "short_sl_at_or_below_price",
                    },
                )
                return None
            if order.take_profit is not None and order.take_profit >= current_price:
                logger.error(
                    "[PAPER] Rejected fill — short TP at or above current price: "
                    "%s tp=%.4f price=%.4f",
                    order.symbol,
                    order.take_profit,
                    current_price,
                )
                self._append_audit(
                    "order_rejected_invalid_tp",
                    {
                        "order_id": order.order_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "position_side": order.position_side,
                        "take_profit": order.take_profit,
                        "current_price": current_price,
                        "reason": "short_tp_at_or_above_price",
                    },
                )
                return None

        # Apply slippage (adverse for buyer, favorable for seller)
        if order.side == "buy":
            fill_price = current_price * (1 + self._slippage_pct)
        else:
            fill_price = current_price * (1 - self._slippage_pct)

        cost = fill_price * order.quantity
        # NEO-P-106: venue-spezifische Maker/Taker-Fee aus config/venue_fees.yaml.
        # Market = taker; Limit mit limit_price = maker. Fallback bei unknown/paper
        # venue = role-spezifischer worst-case Default aus YAML.
        # Constructor `fee_pct` wird ignoriert, sobald venue!="legacy"; legacy-Path
        # bleibt als explizite Opt-out fuer Property-Tests.
        from app.execution.fees import lookup_order_fee

        if order.venue == "legacy":
            fee_pct_eff = self._fee_pct
            fee_meta = ("legacy", "taker", self._fee_pct * 10000.0, "constructor")
        else:
            fee_record = lookup_order_fee(
                order.venue,
                order_type=order.order_type,
                limit_price=order.limit_price,
            )
            fee_pct_eff = fee_record.bps_applied / 10000.0
            fee_meta = (
                fee_record.venue,
                fee_record.role,
                fee_record.bps_applied,
                fee_record.table_version,
            )
        fee = cost * fee_pct_eff
        pnl = 0.0  # NEO-P-101-r2: per-trade pnl; sell-branch overwrites with netto

        if order.position_side == "long" and order.side == "buy":
            if self._portfolio.cash < cost + fee:
                logger.warning(
                    "[PAPER] Insufficient cash for order %s: need=%.2f have=%.2f",
                    order.order_id,
                    cost + fee,
                    self._portfolio.cash,
                )
                return None
            self._portfolio.cash -= cost + fee

            pos = self._portfolio.positions.get(order.symbol)
            if pos:
                # Average into existing position. V25-C: tier ladder + initial
                # quantity carry forward — averaging in does NOT reset the
                # staged-exit plan that the bridge already attached.
                total_qty = pos.quantity + order.quantity
                avg_price = (
                    pos.avg_entry_price * pos.quantity + fill_price * order.quantity
                ) / total_qty
                self._portfolio.positions[order.symbol] = PaperPosition(
                    symbol=order.symbol,
                    quantity=total_qty,
                    avg_entry_price=avg_price,
                    stop_loss=order.stop_loss or pos.stop_loss,
                    take_profit=order.take_profit or pos.take_profit,
                    opened_at=pos.opened_at,
                    realized_pnl_usd=pos.realized_pnl_usd,
                    position_side=pos.position_side,
                    take_profit_tiers=list(pos.take_profit_tiers),
                    initial_quantity=pos.initial_quantity,
                    correlation_id=pos.correlation_id,
                )
            else:
                self._portfolio.positions[order.symbol] = PaperPosition(
                    symbol=order.symbol,
                    quantity=order.quantity,
                    avg_entry_price=fill_price,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    opened_at=_now_utc(),
                    position_side=order.position_side,
                    correlation_id=order.correlation_id,
                )
        elif order.position_side == "long" and order.side == "sell":
            pos = self._portfolio.positions.get(order.symbol)
            if not pos or pos.position_side != "long" or pos.quantity < order.quantity:
                logger.warning("[PAPER] Cannot sell %s — insufficient position", order.symbol)
                return None
            proceeds = fill_price * order.quantity - fee
            pnl = (fill_price - pos.avg_entry_price) * order.quantity - fee
            self._portfolio.cash += proceeds
            self._portfolio.realized_pnl_usd += pnl

            remaining_qty = pos.quantity - order.quantity
            if remaining_qty <= 1e-8:
                del self._portfolio.positions[order.symbol]
            else:
                # V25-C: partial sell preserves the tier ladder + initial
                # quantity so the next monitor tick can fire the next tier.
                self._portfolio.positions[order.symbol] = PaperPosition(
                    symbol=order.symbol,
                    quantity=remaining_qty,
                    avg_entry_price=pos.avg_entry_price,
                    stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit,
                    opened_at=pos.opened_at,
                    realized_pnl_usd=pos.realized_pnl_usd + pnl,
                    position_side=pos.position_side,
                    take_profit_tiers=list(pos.take_profit_tiers),
                    initial_quantity=pos.initial_quantity,
                    correlation_id=pos.correlation_id,
                )
        elif order.position_side == "short" and order.side == "sell":
            pos = self._portfolio.positions.get(order.symbol)
            if pos:
                if pos.position_side != "short":
                    logger.warning("[PAPER] Cannot short %s — long position exists", order.symbol)
                    return None
            proceeds = fill_price * order.quantity - fee
            self._portfolio.cash += proceeds

            if pos:
                total_qty = pos.quantity + order.quantity
                avg_price = (
                    pos.avg_entry_price * pos.quantity + fill_price * order.quantity
                ) / total_qty
                self._portfolio.positions[order.symbol] = PaperPosition(
                    symbol=order.symbol,
                    quantity=total_qty,
                    avg_entry_price=avg_price,
                    stop_loss=order.stop_loss or pos.stop_loss,
                    take_profit=order.take_profit or pos.take_profit,
                    opened_at=pos.opened_at,
                    realized_pnl_usd=pos.realized_pnl_usd,
                    position_side=pos.position_side,
                    take_profit_tiers=list(pos.take_profit_tiers),
                    initial_quantity=pos.initial_quantity,
                    correlation_id=pos.correlation_id,
                )
            else:
                self._portfolio.positions[order.symbol] = PaperPosition(
                    symbol=order.symbol,
                    quantity=order.quantity,
                    avg_entry_price=fill_price,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    opened_at=_now_utc(),
                    position_side=order.position_side,
                    correlation_id=order.correlation_id,
                )
        elif order.position_side == "short" and order.side == "buy":
            pos = self._portfolio.positions.get(order.symbol)
            if not pos or pos.position_side != "short" or pos.quantity < order.quantity:
                logger.warning("[PAPER] Cannot buy-cover %s — insufficient short", order.symbol)
                return None
            cover_cost = fill_price * order.quantity + fee
            if self._portfolio.cash < cover_cost:
                logger.warning(
                    "[PAPER] Insufficient cash to cover short %s: need=%.2f have=%.2f",
                    order.order_id,
                    cover_cost,
                    self._portfolio.cash,
                )
                return None
            pnl = (pos.avg_entry_price - fill_price) * order.quantity - fee
            self._portfolio.cash -= cover_cost
            self._portfolio.realized_pnl_usd += pnl

            remaining_qty = pos.quantity - order.quantity
            if remaining_qty <= 1e-8:
                del self._portfolio.positions[order.symbol]
            else:
                self._portfolio.positions[order.symbol] = PaperPosition(
                    symbol=order.symbol,
                    quantity=remaining_qty,
                    avg_entry_price=pos.avg_entry_price,
                    stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit,
                    opened_at=pos.opened_at,
                    realized_pnl_usd=pos.realized_pnl_usd + pnl,
                    position_side=pos.position_side,
                    take_profit_tiers=list(pos.take_profit_tiers),
                    initial_quantity=pos.initial_quantity,
                    correlation_id=pos.correlation_id,
                )
        else:
            logger.warning(
                "[PAPER] Unsupported side/position_side combo: side=%s position_side=%s",
                order.side,
                order.position_side,
            )
            return None

        self._portfolio.total_fees_usd += fee
        self._portfolio.trade_count += 1
        self._filled_keys.add(order.idempotency_key)

        is_closing_fill = (
            (order.position_side == "long" and order.side == "sell")
            or (order.position_side == "short" and order.side == "buy")
        )
        trade_pnl_for_fill = pnl if is_closing_fill else 0.0

        fill = PaperFill(
            fill_id=_new_fill_id(),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            fill_price=fill_price,
            fee_usd=fee,
            filled_at=_now_utc(),
            slippage_pct=self._slippage_pct * 100,
            pnl_usd=trade_pnl_for_fill,
            position_side=order.position_side,
            fee_venue=fee_meta[0],
            fee_role=fee_meta[1],
            fee_bps_applied=fee_meta[2],
            fee_table_version=fee_meta[3],
            correlation_id=order.correlation_id,
        )

        self._append_audit(
            "order_filled",
            {
                **fill.__dict__,
                "portfolio_cash": self._portfolio.cash,
                "realized_pnl_usd": self._portfolio.realized_pnl_usd,
            },
        )
        logger.info(
            "[PAPER] Fill: %s %s %.4f @ %.2f (fee=%.4f pnl_impact=%.2f)",
            order.side,
            order.symbol,
            order.quantity,
            fill_price,
            fee,
            self._portfolio.realized_pnl_usd,
        )
        if order.correlation_id:
            try:
                t1 = make_lifecycle_transition(
                    correlation_id=order.correlation_id,
                    from_state=OrderLifecycleState.ORDER_SUBMITTED,
                    to_state=OrderLifecycleState.ORDER_ACCEPTED,
                    reason="paper_order_accepted",
                )
                self._append_audit("lifecycle_transition", t1.to_dict())
                # If opening/increasing position
                is_opening = (
                    (order.position_side == "long" and order.side == "buy")
                    or (order.position_side == "short" and order.side == "sell")
                )
                if is_opening:
                    t2 = make_lifecycle_transition(
                        correlation_id=order.correlation_id,
                        from_state=OrderLifecycleState.ORDER_ACCEPTED,
                        to_state=OrderLifecycleState.POSITION_OPEN,
                        reason="paper_position_opened",
                    )
                    self._append_audit("lifecycle_transition", t2.to_dict())
            except Exception as e:
                logger.error("[PAPER] Failed to emit lifecycle transition for fill: %s", e)
        return fill

    def check_stop_take(self, symbol: str, current_price: float) -> str | None:
        """Check stop-loss and take-profit triggers. Returns 'stop' | 'take' | None."""
        pos = self._portfolio.positions.get(symbol)
        if not pos:
            return None
        if pos.position_side == "short":
            if pos.stop_loss and current_price >= pos.stop_loss:
                logger.warning(
                    "[PAPER] Short stop-loss triggered: %s price=%.2f sl=%.2f",
                    symbol,
                    current_price,
                    pos.stop_loss,
                )
                return "stop"
            if pos.take_profit and current_price <= pos.take_profit:
                logger.info(
                    "[PAPER] Short take-profit triggered: %s price=%.2f tp=%.2f",
                    symbol,
                    current_price,
                    pos.take_profit,
                )
                return "take"
            return None
        if pos.stop_loss and current_price <= pos.stop_loss:
            logger.warning(
                "[PAPER] Stop-loss triggered: %s price=%.2f sl=%.2f",
                symbol,
                current_price,
                pos.stop_loss,
            )
            return "stop"
        if pos.take_profit and current_price >= pos.take_profit:
            logger.info(
                "[PAPER] Take-profit triggered: %s price=%.2f tp=%.2f",
                symbol,
                current_price,
                pos.take_profit,
            )
            return "take"
        return None

    # V25-C (2026-05-04): Multi-tier take-profit (staged exits).
    #
    # The bridge calls set_position_tp_tiers(symbol, tiers) right after fill
    # with [(price, qty_share)] pairs derived from the channel's targets.
    # On each subsequent monitor tick we look at the FIRST remaining tier;
    # if current_price has reached it, we close that fraction of the
    # original position (consume_first_tier), audit it as
    # position_partial_closed, and shrink the tiers list. SL still applies
    # to the residual position. When tiers is empty and SL never fired the
    # operator can manually close or wait for SL.

    def set_position_tp_tiers(
        self,
        symbol: str,
        tiers: list[tuple[float, float]],
    ) -> bool:
        """Attach a multi-tier take-profit ladder to an open position.

        ``tiers`` is a list of ``(price, qty_share)`` pairs, where qty_share
        is the fraction of the position's current quantity to close when that
        price is hit. Tiers are sorted ascending by price so the lowest target
        fires first. Returns True if applied, False if the symbol has no open
        position. Setting an empty list reverts to legacy single-TP behaviour.
        Audit event: ``position_tp_tiers_set``.
        """
        pos = self._portfolio.positions.get(symbol)
        if pos is None:
            return False
        sorted_tiers = sorted(
            (
                (float(price), float(qty_share))
                for price, qty_share in tiers
                if qty_share > 0 and price > 0
            ),
            key=lambda item: item[0],
            reverse=pos.position_side == "short",
        )
        pos.take_profit_tiers = sorted_tiers
        if pos.initial_quantity <= 0:
            pos.initial_quantity = pos.quantity
        self._append_audit(
            "position_tp_tiers_set",
            {
                "symbol": symbol,
                "tiers": [
                    {"price": p, "qty_share": q} for p, q in sorted_tiers
                ],
                "initial_quantity": pos.initial_quantity,
            },
        )
        logger.info(
            "[PAPER] Tiers set: %s tiers=%s initial_qty=%.6f",
            symbol,
            sorted_tiers,
            pos.initial_quantity,
        )
        return True

    def _consume_first_tier(
        self,
        symbol: str,
        current_price: float,
    ) -> tuple[PaperOrder, PaperFill | None]:
        """Close the tier-share of the position at current_price, advance tiers.

        Internal helper used by monitor_positions when the first tier's price
        has been hit. Audit event ``position_partial_closed`` is emitted in
        addition to the standard order_created/order_filled pair.
        """
        pos = self._portfolio.positions.get(symbol)
        if pos is None or not pos.take_profit_tiers:
            return None
        if current_price <= 0:
            return None

        tier_price, qty_share = pos.take_profit_tiers[0]
        # Quantity to close = share * initial_quantity. Last tier closes the
        # exact remainder so floating-point drift cannot leave dust behind.
        is_last = len(pos.take_profit_tiers) == 1
        if is_last:
            close_qty = pos.quantity
        else:
            close_qty = min(pos.initial_quantity * qty_share, pos.quantity)
        if close_qty <= 0:
            # Empty tier — drop and try again next tick.
            pos.take_profit_tiers = pos.take_profit_tiers[1:]
            return None

        idem_key = f"tp_tier_{symbol}_{pos.opened_at}_{tier_price}"
        if idem_key in self._filled_keys:
            # Already executed (e.g. duplicate monitor tick) — drop tier safely.
            pos.take_profit_tiers = pos.take_profit_tiers[1:]
            return None

        entry_price = pos.avg_entry_price
        close_side = "buy" if pos.position_side == "short" else "sell"
        order = self.create_order(
            symbol=symbol,
            side=close_side,
            quantity=close_qty,
            idempotency_key=idem_key,
            risk_check_id=f"tp_tier:{tier_price}",
            position_side=pos.position_side,
            correlation_id=pos.correlation_id,
        )
        fill = self.fill_order(order, current_price)
        if fill is None:
            return None

        # Re-read position because fill_order may have removed it on full close.
        residual = self._portfolio.positions.get(symbol)
        residual_tiers = pos.take_profit_tiers[1:]
        if residual is not None:
            residual.take_profit_tiers = residual_tiers
        # else: residual is None → position exited fully, tiers drop with it.

        self._append_audit(
            "position_partial_closed",
            {
                "symbol": symbol,
                "reason": "tp_tier",
                "tier_price": tier_price,
                "tier_qty_share": qty_share,
                "quantity_closed": close_qty,
                "entry_price": entry_price,
                "exit_price": fill.fill_price,
                "fill_id": fill.fill_id,
                "order_id": fill.order_id,
                "remaining_quantity": residual.quantity if residual else 0.0,
                "remaining_tiers": [
                    {"price": p, "qty_share": q} for p, q in residual_tiers
                ],
                "trade_pnl_usd": fill.pnl_usd,
                "fee_usd": fill.fee_usd,
                "realized_pnl_usd": self._portfolio.realized_pnl_usd,
            },
        )
        logger.info(
            "[PAPER] Tier-close: %s qty=%.6f at tier_price=%.4f exit=%.4f "
            "remaining=%.6f tiers_left=%d pnl=%.2f",
            symbol,
            close_qty,
            tier_price,
            fill.fill_price,
            residual.quantity if residual else 0.0,
            len(residual_tiers),
            self._portfolio.realized_pnl_usd,
        )
        if pos.correlation_id:
            try:
                t3 = make_lifecycle_transition(
                    correlation_id=pos.correlation_id,
                    from_state=OrderLifecycleState.POSITION_OPEN,
                    to_state=OrderLifecycleState.PARTIAL_TP_HIT,
                    reason="paper_tier_closed",
                )
                self._append_audit("lifecycle_transition", t3.to_dict())
            except Exception as e:
                logger.error("[PAPER] Failed to emit lifecycle transition PARTIAL_TP_HIT: %s", e)
        return fill

    def adjust_position(
        self,
        symbol: str,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reason: str = "manual",
    ) -> bool:
        """Update SL/TP of an open position without changing quantity or entry.

        Use when an averaged-down merge has left the position with a stop that
        no longer makes geometric sense (e.g. SL above avg_entry for a long).
        Appends a ``position_adjusted`` audit event so replay reconstructs
        state correctly. Returns True iff a position existed and was updated.
        """
        pos = self._portfolio.positions.get(symbol)
        if pos is None:
            return False
        new_sl = stop_loss if stop_loss is not None else pos.stop_loss
        new_tp = take_profit if take_profit is not None else pos.take_profit
        self._portfolio.positions[symbol] = PaperPosition(
            symbol=pos.symbol,
            quantity=pos.quantity,
            avg_entry_price=pos.avg_entry_price,
            stop_loss=new_sl,
            take_profit=new_tp,
            opened_at=pos.opened_at,
            realized_pnl_usd=pos.realized_pnl_usd,
            position_side=pos.position_side,
            take_profit_tiers=list(pos.take_profit_tiers),
            initial_quantity=pos.initial_quantity,
            correlation_id=pos.correlation_id,
        )
        self._append_audit(
            "position_adjusted",
            {
                "symbol": symbol,
                "stop_loss": new_sl,
                "take_profit": new_tp,
                "reason": reason,
            },
        )
        logger.info(
            "[PAPER] Adjusted %s sl=%s tp=%s reason=%s",
            symbol,
            new_sl,
            new_tp,
            reason,
        )
        return True

    def close_position(
        self,
        symbol: str,
        current_price: float,
        reason: str = "manual",
    ) -> tuple[PaperOrder, PaperFill | None]:
        """Close a full open position at current_price.

        Emits the standard order_created + order_filled pair (via create_order /
        fill_order), followed by a dedicated position_closed audit event that
        distinguishes exits from entry-side sells (short entries).

        Returns the fill, or None if there is no open position / price invalid
        / idempotency dedup. Idempotency key is derived from the position's
        open timestamp + reason so repeated calls within the same trigger do
        not double-close.
        """
        pos = self._portfolio.positions.get(symbol)
        if not pos:
            return None
        if current_price <= 0:
            return None

        idem_key = f"close_{symbol}_{pos.opened_at}_{reason}"
        if idem_key in self._filled_keys:
            return None

        entry_price = pos.avg_entry_price
        quantity = pos.quantity
        close_side = "buy" if pos.position_side == "short" else "sell"
        order = self.create_order(
            symbol=symbol,
            side=close_side,
            quantity=quantity,
            idempotency_key=idem_key,
            risk_check_id=f"auto_close:{reason}",
            position_side=pos.position_side,
            correlation_id=pos.correlation_id,
        )
        fill = self.fill_order(order, current_price)
        if fill is None:
            return None

        self._append_audit(
            "position_closed",
            {
                "symbol": symbol,
                "reason": reason,
                "quantity": quantity,
                "entry_price": entry_price,
                "exit_price": fill.fill_price,
                "fill_id": fill.fill_id,
                "order_id": fill.order_id,
                # NEO-P-101-r2: KEEP realized_pnl_usd KUMULATIV (legacy alias).
                # New consumers must read trade_pnl_usd for per-trade NETTO PnL.
                "realized_pnl_usd": self._portfolio.realized_pnl_usd,
                "trade_pnl_usd": fill.pnl_usd,
                "fee_usd": fill.fee_usd,
                "position_side": fill.position_side,
            },
        )
        logger.info(
            "[PAPER] Close: %s qty=%.4f entry=%.2f exit=%.2f reason=%s pnl=%.2f",
            symbol,
            quantity,
            entry_price,
            fill.fill_price,
            reason,
            self._portfolio.realized_pnl_usd,
        )
        if pos.correlation_id:
            try:
                if reason == "sl":
                    target_state = OrderLifecycleState.SL_HIT
                elif reason == "take" or reason == "tp_hit":
                    target_state = OrderLifecycleState.TP_HIT
                else:
                    target_state = OrderLifecycleState.CANCELLED

                t4 = make_lifecycle_transition(
                    correlation_id=pos.correlation_id,
                    from_state=OrderLifecycleState.POSITION_OPEN,
                    to_state=target_state,
                    reason=reason,
                )
                self._append_audit("lifecycle_transition", t4.to_dict())
            except Exception as e:
                logger.error("[PAPER] Failed to emit lifecycle transition for close: %s", e)
        return fill

    def monitor_positions(
        self,
        prices_by_symbol: dict[str, float],
    ) -> list[PaperFill]:
        """Check SL/TP for all open positions, close those triggered.

        Takes a price map so callers fetch market data once and drive exits
        deterministically. Returns the list of fills produced (empty when no
        trigger fires). Positions whose symbol is missing from the price map
        are skipped — this is intentional, so a partial price feed cannot
        force a close at a zero or stale price.

        V25-C (2026-05-04) staged exits: when a position carries
        ``take_profit_tiers`` we evaluate the first tier first. If
        current_price has reached it the tier is consumed (partial close)
        and we keep looping the same symbol until either no more tiers fire
        in this tick or the position is closed. SL still wins over any tier
        — a stop hit closes the full residual position regardless of tiers.
        """
        fills: list[PaperFill] = []
        for symbol in list(self._portfolio.positions.keys()):
            price = prices_by_symbol.get(symbol)
            if price is None or price <= 0:
                continue
            # Stop-loss has priority over tiers — it kills the whole residual.
            pos = self._portfolio.positions.get(symbol)
            if pos and pos.position_side == "short" and pos.stop_loss and price >= pos.stop_loss:
                fill = self.close_position(symbol, price, reason="stop")
                if fill:
                    fills.append(fill)
                continue
            if pos and pos.position_side != "short" and pos.stop_loss and price <= pos.stop_loss:
                fill = self.close_position(symbol, price, reason="stop")
                if fill:
                    fills.append(fill)
                continue
            # Multi-tier path: consume as many tiers as the price triggers
            # in this single tick (e.g. one wide candle can clear TP1+TP2).
            consumed_any = False
            while True:
                pos = self._portfolio.positions.get(symbol)
                if pos is None or not pos.take_profit_tiers:
                    break
                tier_price = pos.take_profit_tiers[0][0]
                if pos.position_side == "short":
                    if price > tier_price:
                        break
                elif price < tier_price:
                    break
                fill = self._consume_first_tier(symbol, price)
                if fill is None:
                    break
                fills.append(fill)
                consumed_any = True
            if consumed_any:
                continue
            # Legacy single-TP path: only when no tier ladder is set.
            pos = self._portfolio.positions.get(symbol)
            if pos is None or pos.take_profit_tiers:
                continue
            trigger = self.check_stop_take(symbol, price)
            if trigger is None:
                continue
            fill = self.close_position(symbol, price, reason=trigger)
            if fill:
                fills.append(fill)
        return fills

    def _append_audit(self, event_type: str, data: dict[str, object]) -> None:
        # NEO-P-101-r2: every NEW audit row carries schema_version="v2".
        # Legacy v1 rows (pre-NEO-P-101-r2) lack the field - consumers must
        # default to "v1" via dict.get("schema_version", "v1").
        record = {
            "schema_version": "v2",
            "event_type": event_type,
            "timestamp_utc": _now_utc(),
            **data,
        }
        try:
            # NEO-P-101-r2: portalocker advisory file-lock (cross-platform).
            # Required because Pi runs kai-server + kai-agent-worker as
            # parallel writers on the same audit file. Lock auto-releases on
            # file close (context-manager exit).
            with self._audit_path.open("a", encoding="utf-8") as fh:
                portalocker.lock(fh, portalocker.LOCK_EX)
                fh.write(json.dumps(record) + "\n")
        except OSError as e:
            logger.error("[PAPER] Audit log write failed: %s", e)
        _publish_paper_event(event_type, record)
