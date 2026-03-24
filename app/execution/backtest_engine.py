"""KAI Backtest Engine — Signal→Risk→Paper simulation loop.

Sprint 35. Invariants: I-231–I-240.

Design principles:
- Paper-only. live_enabled is never exposed. (I-231)
- Every signal must pass through all RiskEngine gates. (I-232)
- BacktestResult is frozen and immutable. (I-233)
- Market data must arrive via dict[str, float] (pre-fetched). (I-234)
- Signal→Order mapping is deterministic for given inputs. (I-235)
- direction_hint=="neutral" signals are always skipped. (I-236)
- Kill switch halts all further fills immediately. (I-237)
- BacktestResult exposes kill_switch_triggered flag. (I-238)
- to_json_dict() omits internal paths, no sensitive data. (I-239)
- Audit is written append-only to artifacts/backtest_audit.jsonl. (I-240)

Assumptions documented in ASSUMPTIONS.md (A-012–A-015).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.signals import SignalCandidate
from app.execution.models import PaperFill
from app.execution.paper_engine import PaperExecutionEngine
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits

logger = logging.getLogger(__name__)

_BACKTEST_AUDIT_LOG = "artifacts/backtest_audit.jsonl"
_DIRECTION_NEUTRAL = "neutral"
_DIRECTION_BULLISH = "bullish"
_DIRECTION_BEARISH = "bearish"


@dataclass(frozen=True)
class BacktestConfig:
    """Immutable backtest configuration. All defaults are conservative."""

    initial_equity: float = 10_000.0
    fee_pct: float = 0.1  # % charged per fill
    slippage_pct: float = 0.05  # % adverse slippage per fill
    stop_loss_pct: float = 2.0  # SL distance = entry * stop_loss_pct/100
    take_profit_multiplier: float = 2.0  # TP = SL_distance * multiplier
    min_signal_confidence: float = 0.7
    min_signal_confluence_count: int = 1  # A-015: single-signal backtest count
    require_stop_loss: bool = True
    max_open_positions: int = 5
    max_risk_per_trade_pct: float = 2.0
    max_daily_loss_pct: float = 5.0
    max_total_drawdown_pct: float = 15.0
    allow_averaging_down: bool = False
    allow_martingale: bool = False
    kill_switch_enabled: bool = True
    long_only: bool = True  # A-012: bearish signals skipped by default
    audit_log_path: str = _BACKTEST_AUDIT_LOG


@dataclass(frozen=True)
class SignalExecutionRecord:
    """Immutable record of one signal's disposition in the backtest."""

    signal_id: str
    target_asset: str
    direction_hint: str
    confidence: float
    # filled | risk_rejected | skipped_neutral | skipped_bearish
    # | no_price | no_quantity | kill_switch_halted
    outcome: str
    risk_violations: tuple[str, ...]
    order_id: str | None = None
    fill_price: float | None = None
    quantity: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_check_id: str | None = None


@dataclass(frozen=True)
class BacktestResult:
    """Immutable backtest result. (I-233)"""

    config_initial_equity: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    realized_pnl_usd: float
    total_fees_usd: float
    trade_count: int
    signals_received: int
    signals_executed: int
    signals_skipped: int
    kill_switch_triggered: bool
    execution_records: tuple[SignalExecutionRecord, ...]
    completed_at: str

    def to_json_dict(self) -> dict[str, object]:
        """Serialize result. No internal paths or sensitive data. (I-239)"""
        return {
            "config_initial_equity": self.config_initial_equity,
            "final_equity": round(self.final_equity, 4),
            "total_return_pct": self.total_return_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "realized_pnl_usd": self.realized_pnl_usd,
            "total_fees_usd": self.total_fees_usd,
            "trade_count": self.trade_count,
            "signals_received": self.signals_received,
            "signals_executed": self.signals_executed,
            "signals_skipped": self.signals_skipped,
            "kill_switch_triggered": self.kill_switch_triggered,
            "completed_at": self.completed_at,
            "execution_records": [
                {
                    "signal_id": r.signal_id,
                    "target_asset": r.target_asset,
                    "direction_hint": r.direction_hint,
                    "confidence": r.confidence,
                    "outcome": r.outcome,
                    "risk_violations": list(r.risk_violations),
                    "order_id": r.order_id,
                    "fill_price": r.fill_price,
                    "quantity": r.quantity,
                    "stop_loss": r.stop_loss,
                    "take_profit": r.take_profit,
                    "risk_check_id": r.risk_check_id,
                }
                for r in self.execution_records
            ],
        }


class BacktestEngine:
    """KAI Backtest Engine — paper-only signal execution loop.

    Usage::

        cfg = BacktestConfig(initial_equity=50_000.0, stop_loss_pct=1.5)
        engine = BacktestEngine(cfg)
        result = engine.run(signals, prices={"BTC/USDT": 65000.0, ...})

    Prices dict keys must match SignalCandidate.target_asset or
    target_asset + "/USDT" (A-012).
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self._config = config or BacktestConfig()

    def _build_risk_limits(self) -> RiskLimits:
        cfg = self._config
        return RiskLimits(
            initial_equity=cfg.initial_equity,
            max_risk_per_trade_pct=cfg.max_risk_per_trade_pct,
            max_daily_loss_pct=cfg.max_daily_loss_pct,
            max_total_drawdown_pct=cfg.max_total_drawdown_pct,
            max_open_positions=cfg.max_open_positions,
            max_leverage=1.0,  # always 1x for paper/backtest (A-013)
            require_stop_loss=cfg.require_stop_loss,
            allow_averaging_down=cfg.allow_averaging_down,
            allow_martingale=cfg.allow_martingale,
            kill_switch_enabled=cfg.kill_switch_enabled,
            min_signal_confidence=cfg.min_signal_confidence,
            min_signal_confluence_count=cfg.min_signal_confluence_count,
        )

    def _resolve_price(self, target_asset: str, prices: dict[str, float]) -> float | None:
        """Try target_asset directly, then target_asset/USDT (A-012)."""
        if target_asset in prices:
            return prices[target_asset]
        return prices.get(f"{target_asset}/USDT")

    def _compute_sl_tp(self, entry: float, side: str) -> tuple[float, float]:
        """Compute stop-loss and take-profit from config. (A-014)"""
        sl_dist = entry * (self._config.stop_loss_pct / 100)
        tp_dist = sl_dist * self._config.take_profit_multiplier
        if side == "buy":
            return round(entry - sl_dist, 4), round(entry + tp_dist, 4)
        return round(entry + sl_dist, 4), round(entry - tp_dist, 4)

    def run(
        self,
        signals: list[SignalCandidate],
        prices: dict[str, float],
    ) -> BacktestResult:
        """Run backtest synchronously.

        Args:
            signals: ordered list of signal candidates to process.
            prices: {symbol -> current_price} dict; keys may be "BTC",
                    "BTC/USDT", etc.  (I-234)

        Returns:
            Immutable BacktestResult. (I-233)
        """
        cfg = self._config
        risk_engine = RiskEngine(self._build_risk_limits())
        paper_engine = PaperExecutionEngine(
            initial_equity=cfg.initial_equity,
            fee_pct=cfg.fee_pct,
            slippage_pct=cfg.slippage_pct,
            audit_log_path=cfg.audit_log_path,
        )

        records: list[SignalExecutionRecord] = []
        kill_switch_triggered = False

        for signal in signals:
            # I-237: Kill switch check before processing
            if risk_engine.is_halted:
                kill_switch_triggered = True
                records.append(
                    SignalExecutionRecord(
                        signal_id=signal.signal_id,
                        target_asset=signal.target_asset,
                        direction_hint=signal.direction_hint,
                        confidence=signal.confidence,
                        outcome="kill_switch_halted",
                        risk_violations=("kill_switch_active",),
                    )
                )
                continue

            direction = signal.direction_hint.lower()

            # I-236: skip neutral signals unconditionally
            if direction == _DIRECTION_NEUTRAL:
                records.append(
                    SignalExecutionRecord(
                        signal_id=signal.signal_id,
                        target_asset=signal.target_asset,
                        direction_hint=signal.direction_hint,
                        confidence=signal.confidence,
                        outcome="skipped_neutral",
                        risk_violations=(),
                    )
                )
                continue

            # I-236: skip bearish when long_only=True (A-012)
            if cfg.long_only and direction == _DIRECTION_BEARISH:
                records.append(
                    SignalExecutionRecord(
                        signal_id=signal.signal_id,
                        target_asset=signal.target_asset,
                        direction_hint=signal.direction_hint,
                        confidence=signal.confidence,
                        outcome="skipped_bearish",
                        risk_violations=(),
                    )
                )
                continue

            side = "buy" if direction == _DIRECTION_BULLISH else "sell"

            # I-234: market data must come through prices dict
            price = self._resolve_price(signal.target_asset, prices)
            if price is None or price <= 0:
                records.append(
                    SignalExecutionRecord(
                        signal_id=signal.signal_id,
                        target_asset=signal.target_asset,
                        direction_hint=signal.direction_hint,
                        confidence=signal.confidence,
                        outcome="no_price",
                        risk_violations=(),
                    )
                )
                continue

            stop_loss, take_profit = self._compute_sl_tp(price, side)

            # Position sizing
            portfolio = paper_engine.portfolio
            equity = portfolio.total_equity(
                {signal.target_asset: price, f"{signal.target_asset}/USDT": price}
            )
            size_result = risk_engine.calculate_position_size(
                symbol=signal.target_asset,
                entry_price=price,
                stop_loss_price=stop_loss,
                equity=equity,
            )
            if not size_result.approved or size_result.position_size_units <= 0:
                records.append(
                    SignalExecutionRecord(
                        signal_id=signal.signal_id,
                        target_asset=signal.target_asset,
                        direction_hint=signal.direction_hint,
                        confidence=signal.confidence,
                        outcome="no_quantity",
                        risk_violations=("position_size_zero",),
                    )
                )
                continue

            # Cap units so that fill_price * qty * (1 + fee_pct) <= cash
            # Prevents fill rejection due to slippage/fee pushing cost above equity
            slip_factor = 1.0 + (cfg.slippage_pct + cfg.fee_pct) / 100
            max_safe_units = equity / (price * slip_factor) if price > 0 else 0.0
            safe_units = min(size_result.position_size_units, max_safe_units * 0.95)

            # I-232: all signals must pass RiskEngine gates
            is_averaging = (
                signal.target_asset in portfolio.positions
                or f"{signal.target_asset}/USDT" in portfolio.positions
            )
            risk_result = risk_engine.check_order(
                symbol=signal.target_asset,
                side=side,
                signal_confidence=signal.confidence,
                signal_confluence_count=1,  # A-015
                stop_loss_price=stop_loss,
                current_open_positions=len(portfolio.positions),
                is_averaging_down=is_averaging,
            )

            if not risk_result.approved:
                if risk_engine.is_halted:
                    kill_switch_triggered = True
                records.append(
                    SignalExecutionRecord(
                        signal_id=signal.signal_id,
                        target_asset=signal.target_asset,
                        direction_hint=signal.direction_hint,
                        confidence=signal.confidence,
                        outcome="risk_rejected",
                        risk_violations=tuple(risk_result.violations),
                        risk_check_id=risk_result.check_id,
                    )
                )
                continue

            # I-235: deterministic order creation
            order = paper_engine.create_order(
                symbol=signal.target_asset,
                side=side,
                quantity=safe_units,
                stop_loss=stop_loss,
                take_profit=take_profit,
                idempotency_key=f"bt_{signal.signal_id}",
                risk_check_id=risk_result.check_id,
            )
            fill: PaperFill | None = paper_engine.fill_order(order, current_price=price)

            if fill is None:
                records.append(
                    SignalExecutionRecord(
                        signal_id=signal.signal_id,
                        target_asset=signal.target_asset,
                        direction_hint=signal.direction_hint,
                        confidence=signal.confidence,
                        outcome="no_quantity",
                        risk_violations=("fill_rejected",),
                        order_id=order.order_id,
                        risk_check_id=risk_result.check_id,
                    )
                )
                continue

            # Update drawdown after fill (may trigger kill switch)
            drawdown = portfolio.drawdown_pct(
                {signal.target_asset: price, f"{signal.target_asset}/USDT": price}
            )
            if risk_engine.update_drawdown(drawdown):
                kill_switch_triggered = True

            records.append(
                SignalExecutionRecord(
                    signal_id=signal.signal_id,
                    target_asset=signal.target_asset,
                    direction_hint=signal.direction_hint,
                    confidence=signal.confidence,
                    outcome="filled",
                    risk_violations=(),
                    order_id=order.order_id,
                    fill_price=fill.fill_price,
                    quantity=fill.quantity,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_check_id=risk_result.check_id,
                )
            )

        # Final portfolio metrics
        portfolio_final = paper_engine.portfolio
        final_equity = portfolio_final.total_equity(prices)
        total_return_pct = ((final_equity - cfg.initial_equity) / cfg.initial_equity) * 100
        max_drawdown_pct = portfolio_final.drawdown_pct(prices)

        executed = [r for r in records if r.outcome == "filled"]
        skipped = [r for r in records if r.outcome != "filled"]

        result = BacktestResult(
            config_initial_equity=cfg.initial_equity,
            final_equity=final_equity,
            total_return_pct=round(total_return_pct, 4),
            max_drawdown_pct=round(max_drawdown_pct, 4),
            realized_pnl_usd=round(portfolio_final.realized_pnl_usd, 4),
            total_fees_usd=round(portfolio_final.total_fees_usd, 4),
            trade_count=portfolio_final.trade_count,
            signals_received=len(signals),
            signals_executed=len(executed),
            signals_skipped=len(skipped),
            kill_switch_triggered=kill_switch_triggered,
            execution_records=tuple(records),
            completed_at=datetime.now(UTC).isoformat(),
        )

        # I-240: append-only audit
        _append_backtest_audit(result, Path(cfg.audit_log_path))
        return result


def _append_backtest_audit(result: BacktestResult, path: Path) -> None:
    """Append one audit row per backtest run. (I-240)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "completed_at": result.completed_at,
        "config_initial_equity": result.config_initial_equity,
        "final_equity": round(result.final_equity, 4),
        "total_return_pct": result.total_return_pct,
        "trade_count": result.trade_count,
        "signals_received": result.signals_received,
        "signals_executed": result.signals_executed,
        "signals_skipped": result.signals_skipped,
        "kill_switch_triggered": result.kill_switch_triggered,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
