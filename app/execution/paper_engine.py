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

from app.execution.audit_replay import replay_paper_audit
from app.execution.models import (
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    _new_fill_id,
    _new_order_id,
    _now_utc,
)

logger = logging.getLogger(__name__)

_AUDIT_LOG = Path("artifacts/paper_execution_audit.jsonl")


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
    ) -> PaperOrder:
        """Create an order record (does not fill immediately)."""
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
        )
        self._append_audit("order_created", order.__dict__)
        return order

    def fill_order(self, order: PaperOrder, current_price: float) -> PaperFill | None:
        """
        Execute a paper fill for an order.
        Returns None if: duplicate (idempotency), insufficient cash, or invalid price.
        """
        if order.idempotency_key in self._filled_keys:
            logger.warning("[PAPER] Duplicate order rejected: key=%s", order.idempotency_key)
            return None

        if current_price <= 0:
            logger.error(
                "[PAPER] Invalid price for fill: %s price=%.2f", order.symbol, current_price
            )
            return None

        # Defense-in-depth against inverted stops — the Risk Engine owns the
        # primary geometry gate, but if it is ever bypassed we still refuse to
        # fill a long with SL above or TP below current price. Short/close
        # orders carry no SL/TP so the check is scoped to buys.
        if order.side == "buy":
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

        # Apply slippage (adverse for buyer, favorable for seller)
        if order.side == "buy":
            fill_price = current_price * (1 + self._slippage_pct)
        else:
            fill_price = current_price * (1 - self._slippage_pct)

        cost = fill_price * order.quantity
        fee = cost * self._fee_pct

        if order.side == "buy":
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
                # Average into existing position
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
                )
            else:
                self._portfolio.positions[order.symbol] = PaperPosition(
                    symbol=order.symbol,
                    quantity=order.quantity,
                    avg_entry_price=fill_price,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    opened_at=_now_utc(),
                )
        else:  # sell
            pos = self._portfolio.positions.get(order.symbol)
            if not pos or pos.quantity < order.quantity:
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
                self._portfolio.positions[order.symbol] = PaperPosition(
                    symbol=order.symbol,
                    quantity=remaining_qty,
                    avg_entry_price=pos.avg_entry_price,
                    stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit,
                    opened_at=pos.opened_at,
                    realized_pnl_usd=pos.realized_pnl_usd + pnl,
                )

        self._portfolio.total_fees_usd += fee
        self._portfolio.trade_count += 1
        self._filled_keys.add(order.idempotency_key)

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
        return fill

    def check_stop_take(self, symbol: str, current_price: float) -> str | None:
        """Check stop-loss and take-profit triggers. Returns 'stop' | 'take' | None."""
        pos = self._portfolio.positions.get(symbol)
        if not pos:
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

    def close_position(
        self,
        symbol: str,
        current_price: float,
        reason: str = "manual",
    ) -> PaperFill | None:
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
        order = self.create_order(
            symbol=symbol,
            side="sell",
            quantity=quantity,
            idempotency_key=idem_key,
            risk_check_id=f"auto_close:{reason}",
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
                "realized_pnl_usd": self._portfolio.realized_pnl_usd,
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
        """
        fills: list[PaperFill] = []
        for symbol in list(self._portfolio.positions.keys()):
            price = prices_by_symbol.get(symbol)
            if price is None or price <= 0:
                continue
            trigger = self.check_stop_take(symbol, price)
            if trigger is None:
                continue
            fill = self.close_position(symbol, price, reason=trigger)
            if fill:
                fills.append(fill)
        return fills

    def _append_audit(self, event_type: str, data: dict[str, object]) -> None:
        record = {"event_type": event_type, "timestamp_utc": _now_utc(), **data}
        try:
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as e:
            logger.error("[PAPER] Audit log write failed: %s", e)
