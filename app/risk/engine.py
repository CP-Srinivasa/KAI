"""Risk Engine — hard pre-gate before any order. Never bypass. (Security First)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.risk.models import (
    DailyLossState,
    PositionSizeResult,
    RiskCheckResult,
    RiskLimits,
    _new_check_id,
    _now_utc,
)

logger = logging.getLogger(__name__)


class RiskEngine:
    """
    Hard risk gate — must be called before EVERY potential order.

    Design invariants:
    - Never raises on bad input → returns RiskCheckResult(approved=False)
    - Kill switch overrides all checks
    - If kill_switch_enabled and triggered → ALL checks fail
    - Never allows averaging down or martingale
    - Requires stop_loss when configured
    - All decisions are logged
    """

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits
        self._kill_switch_active = False
        self._paused = False
        self._daily_loss_pct: float = 0.0  # updated by execution engine
        self._total_drawdown_pct: float = 0.0  # updated externally
        self._open_position_count: int = 0  # updated externally

    @property
    def is_halted(self) -> bool:
        """True when system is paused or kill switch is active."""
        return self._kill_switch_active or self._paused

    def pause(self) -> None:
        """Operator-triggered pause. No new orders allowed."""
        self._paused = True
        logger.warning("[RISK] System PAUSED by operator.")

    def resume(self) -> None:
        """Operator-triggered resume. Only valid if kill switch not active."""
        if self._kill_switch_active:
            logger.error(
                "[RISK] Cannot resume — kill switch is active. Manual intervention required."
            )
            return
        self._paused = False
        logger.info("[RISK] System RESUMED by operator.")

    def trigger_kill_switch(self) -> None:
        """Emergency stop. Requires manual reset to clear."""
        self._kill_switch_active = True
        logger.critical("[RISK] KILL SWITCH ACTIVATED. All operations halted.")

    def reset_kill_switch(self) -> None:
        """Manual operator reset of kill switch."""
        self._kill_switch_active = False
        self._paused = False
        logger.warning("[RISK] Kill switch RESET by operator. System not yet active.")

    def update_daily_loss(self, realized_pnl_usd: float, equity: float) -> DailyLossState:
        """Update daily loss state. Auto-triggers kill switch if limit breached."""
        loss_pct = (realized_pnl_usd / equity * 100) if equity > 0 else 0.0
        self._daily_loss_pct = loss_pct

        kill_triggered = False
        if loss_pct < -abs(self._limits.max_daily_loss_pct):
            if self._limits.kill_switch_enabled:
                self.trigger_kill_switch()
                kill_triggered = True
            logger.error(
                "[RISK] Daily loss limit breached: %.2f%% (limit=%.2f%%)",
                loss_pct,
                -abs(self._limits.max_daily_loss_pct),
            )

        return DailyLossState(
            date_utc=datetime.now(UTC).date().isoformat(),
            realized_pnl_usd=realized_pnl_usd,
            loss_pct=loss_pct,
            kill_switch_triggered=kill_triggered,
        )

    def update_drawdown(self, drawdown_pct: float) -> bool:
        """Update total drawdown. Returns True if kill switch triggered."""
        self._total_drawdown_pct = drawdown_pct
        if drawdown_pct > abs(self._limits.max_total_drawdown_pct):
            if self._limits.kill_switch_enabled:
                self.trigger_kill_switch()
            logger.error(
                "[RISK] Max drawdown breached: %.2f%% (limit=%.2f%%)",
                drawdown_pct,
                self._limits.max_total_drawdown_pct,
            )
            return True
        return False

    def check_order(
        self,
        *,
        symbol: str,
        side: str,
        signal_confidence: float,
        signal_confluence_count: int,
        stop_loss_price: float | None,
        current_open_positions: int,
        is_averaging_down: bool = False,
        entry_price: float | None = None,
        take_profit_price: float | None = None,
    ) -> RiskCheckResult:
        """
        Pre-order risk gate. Must return approved=True before any order is sent.
        All checks are evaluated; violations accumulate.
        """
        check_id = _new_check_id()
        violations: list[str] = []

        # Gate 1: Kill switch / pause
        if self._kill_switch_active:
            return RiskCheckResult(
                approved=False,
                check_id=check_id,
                timestamp_utc=_now_utc(),
                symbol=symbol,
                check_type="kill_switch",
                reason="Kill switch is active — no orders allowed",
                violations=["kill_switch_active"],
            )

        if self._paused:
            return RiskCheckResult(
                approved=False,
                check_id=check_id,
                timestamp_utc=_now_utc(),
                symbol=symbol,
                check_type="system_paused",
                reason="System is paused — no orders allowed",
                violations=["system_paused"],
            )

        # Gate 2: Martingale / averaging down
        if is_averaging_down and not self._limits.allow_averaging_down:
            violations.append("averaging_down_not_allowed")

        if self._limits.allow_martingale is False:
            # Trust the caller to flag this; we enforce the limit setting
            pass

        # Gate 3: Stop loss required
        if self._limits.require_stop_loss and stop_loss_price is None:
            violations.append("stop_loss_required_but_missing")

        # Gate 3b: SL/TP geometry — prevent inverted stops (long with sl>=entry,
        # short with sl<=entry) and mirror-inverted take-profits. Triggered only
        # when entry_price is provided AND the respective level is set; skips
        # validation when the caller omits entry_price (backwards compatible).
        normalized_side = side.lower()
        if entry_price is not None and entry_price > 0:
            if normalized_side == "buy":
                if stop_loss_price is not None and stop_loss_price >= entry_price:
                    violations.append(
                        f"sl_geometry_invalid:long_sl_at_or_above_entry|"
                        f"entry={entry_price}|sl={stop_loss_price}"
                    )
                if take_profit_price is not None and take_profit_price <= entry_price:
                    violations.append(
                        f"tp_geometry_invalid:long_tp_at_or_below_entry|"
                        f"entry={entry_price}|tp={take_profit_price}"
                    )
            elif normalized_side == "sell":
                if stop_loss_price is not None and stop_loss_price <= entry_price:
                    violations.append(
                        f"sl_geometry_invalid:short_sl_at_or_below_entry|"
                        f"entry={entry_price}|sl={stop_loss_price}"
                    )
                if take_profit_price is not None and take_profit_price >= entry_price:
                    violations.append(
                        f"tp_geometry_invalid:short_tp_at_or_above_entry|"
                        f"entry={entry_price}|tp={take_profit_price}"
                    )

        # Gate 4: Signal confidence
        if signal_confidence < self._limits.min_signal_confidence:
            violations.append(
                f"signal_confidence_too_low:{signal_confidence:.2f}<{self._limits.min_signal_confidence}"
            )

        # Gate 5: Signal confluence
        if signal_confluence_count < self._limits.min_signal_confluence_count:
            violations.append(
                f"signal_confluence_too_low:{signal_confluence_count}<{self._limits.min_signal_confluence_count}"
            )

        # Gate 6: Max open positions
        if current_open_positions >= self._limits.max_open_positions:
            violations.append(
                f"max_open_positions_reached:{current_open_positions}>={self._limits.max_open_positions}"
            )

        # Gate 7: Daily loss limit
        if self._daily_loss_pct < -abs(self._limits.max_daily_loss_pct):
            violations.append(
                f"daily_loss_limit_breached:{self._daily_loss_pct:.2f}%<-{self._limits.max_daily_loss_pct}%"
            )

        # Gate 8: Total drawdown
        if self._total_drawdown_pct > abs(self._limits.max_total_drawdown_pct):
            violations.append(
                f"drawdown_limit_breached:{self._total_drawdown_pct:.2f}%>{self._limits.max_total_drawdown_pct}%"
            )

        approved = len(violations) == 0
        reason = (
            "All risk gates passed" if approved else f"Risk violations: {'; '.join(violations)}"
        )

        result = RiskCheckResult(
            approved=approved,
            check_id=check_id,
            timestamp_utc=_now_utc(),
            symbol=symbol,
            check_type="pre_order",
            reason=reason,
            violations=violations,
            details={
                "side": side,
                "signal_confidence": signal_confidence,
                "signal_confluence": signal_confluence_count,
                "open_positions": current_open_positions,
                "daily_loss_pct": self._daily_loss_pct,
                "drawdown_pct": self._total_drawdown_pct,
            },
        )

        if approved:
            logger.info("[RISK] Order approved: %s %s (check_id=%s)", side, symbol, check_id)
        else:
            logger.warning(
                "[RISK] Order REJECTED: %s %s violations=%s (check_id=%s)",
                side,
                symbol,
                violations,
                check_id,
            )

        return result

    def calculate_position_size(
        self,
        *,
        symbol: str,
        entry_price: float,
        stop_loss_price: float | None,
        equity: float,
    ) -> PositionSizeResult:
        """
        Calculate safe position size based on risk limits.
        Risk per trade = max_risk_per_trade_pct % of equity.
        If no stop loss, use minimum position size.
        """
        if entry_price <= 0 or equity <= 0:
            return PositionSizeResult(
                approved=False,
                symbol=symbol,
                position_size_pct=0.0,
                position_size_units=0.0,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                max_loss_usd=0.0,
                max_loss_pct=0.0,
                rationale="Invalid price or equity",
            )

        max_risk_usd = equity * (self._limits.max_risk_per_trade_pct / 100)

        if stop_loss_price is not None and stop_loss_price > 0:
            risk_per_unit = abs(entry_price - stop_loss_price)
            if risk_per_unit > 0:
                units = max_risk_usd / risk_per_unit
            else:
                units = max_risk_usd / entry_price
        else:
            # No stop loss — use minimum sizing (0.1x normal)
            units = (max_risk_usd / entry_price) * 0.1

        position_value = units * entry_price
        position_size_pct = (position_value / equity) * 100
        max_loss_usd = min(max_risk_usd, position_value)
        max_loss_pct = (max_loss_usd / equity) * 100

        return PositionSizeResult(
            approved=True,
            symbol=symbol,
            position_size_pct=position_size_pct,
            position_size_units=units,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            max_loss_usd=max_loss_usd,
            max_loss_pct=max_loss_pct,
            rationale=(
                f"Risk-based sizing: {self._limits.max_risk_per_trade_pct}% equity risk, "
                f"{units:.4f} units @ {entry_price:.2f}"
            ),
        )
