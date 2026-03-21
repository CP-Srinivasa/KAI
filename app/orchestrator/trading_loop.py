"""Core Trading Loop — orchestrates signal → risk → paper execution. (Security First)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.domain.document import AnalysisResult
from app.execution.models import PaperPortfolio
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.base import BaseMarketDataAdapter
from app.orchestrator.models import CycleStatus, LoopCycle, _new_cycle_id, _now_utc
from app.risk.engine import RiskEngine
from app.signals.generator import SignalGenerator
from app.signals.models import SignalDirection

logger = logging.getLogger(__name__)

_AUDIT_LOG = Path("artifacts/trading_loop_audit.jsonl")


class TradingLoop:
    """
    Paper trading loop — coordinates the full decision pipeline.

    Pipeline per cycle:
    1. Fetch market data (BaseMarketDataAdapter)
    2. Generate signal candidate (SignalGenerator)
    3. Risk gate check (RiskEngine.check_order)
    4. Position sizing (RiskEngine.calculate_position_size)
    5. Create + fill paper order (PaperExecutionEngine)
    6. Update daily loss state in RiskEngine
    7. Write LoopCycle to JSONL audit

    Design invariants:
    - Never raises (all errors captured in LoopCycle.notes)
    - One cycle = one decision opportunity per symbol per analysis
    - All cycles written to JSONL audit (including non-trades)
    - Position state lives in PaperExecutionEngine.portfolio
    - Risk state lives in RiskEngine (updated after each fill)
    """

    def __init__(
        self,
        *,
        risk_engine: RiskEngine,
        execution_engine: PaperExecutionEngine,
        market_data_adapter: BaseMarketDataAdapter,
        signal_generator: SignalGenerator,
        audit_log_path: str | None = None,
    ) -> None:
        self._risk = risk_engine
        self._exec = execution_engine
        self._market_data = market_data_adapter
        self._signals = signal_generator
        self._audit_path = Path(audit_log_path or _AUDIT_LOG)
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def portfolio(self) -> PaperPortfolio:
        """Expose portfolio for external inspection."""
        return self._exec.portfolio

    async def run_cycle(
        self,
        analysis: AnalysisResult,
        symbol: str,
    ) -> LoopCycle:
        """
        Execute one trading decision cycle for the given symbol.

        Returns a LoopCycle capturing the full outcome.
        Never raises — errors are captured in notes.
        """
        cycle_id = _new_cycle_id()
        started_at = _now_utc()
        notes: list[str] = []

        # ── Step 1: Market data ───────────────────────────────────────────────
        market_data = None
        try:
            market_data = await self._market_data.get_market_data_point(symbol)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"market_data_error:{exc}")

        if market_data is None:
            return self._build_cycle(
                cycle_id, started_at, symbol,
                CycleStatus.NO_MARKET_DATA,
                notes=notes + [f"no_market_data:{symbol}"],
            )

        # ── Step 2: Signal generation ─────────────────────────────────────────
        signal = None
        try:
            signal = self._signals.generate(analysis, market_data, symbol)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"signal_error:{exc}")

        if signal is None:
            return self._build_cycle(
                cycle_id, started_at, symbol,
                CycleStatus.NO_SIGNAL,
                market_data_fetched=True,
                notes=notes + ["signal_filtered_or_not_generated"],
            )

        # ── Step 3: Risk gate ─────────────────────────────────────────────────
        # Map direction (long/short) to order side (buy/sell)
        order_side = "buy" if signal.direction == SignalDirection.LONG else "sell"
        current_positions = len(self._exec.portfolio.positions)
        risk_result = self._risk.check_order(
            symbol=symbol,
            side=order_side,
            signal_confidence=signal.confidence_score,
            signal_confluence_count=signal.confluence_count,
            stop_loss_price=signal.stop_loss_price,
            current_open_positions=current_positions,
        )

        if not risk_result.approved:
            return self._build_cycle(
                cycle_id, started_at, symbol,
                CycleStatus.RISK_REJECTED,
                market_data_fetched=True,
                signal_generated=True,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                notes=notes + risk_result.violations,
            )

        # ── Step 4: Position sizing ───────────────────────────────────────────
        equity = self._exec.portfolio.cash
        size_result = self._risk.calculate_position_size(
            symbol=symbol,
            entry_price=signal.entry_price,
            stop_loss_price=signal.stop_loss_price,
            equity=equity,
        )

        if not size_result.approved or size_result.position_size_units <= 0:
            return self._build_cycle(
                cycle_id, started_at, symbol,
                CycleStatus.SIZE_REJECTED,
                market_data_fetched=True,
                signal_generated=True,
                risk_approved=True,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                notes=notes + [size_result.rationale],
            )

        # ── Step 5: Create + fill paper order ────────────────────────────────
        order = None
        fill = None
        try:
            order = self._exec.create_order(
                symbol=symbol,
                side=order_side,
                quantity=size_result.position_size_units,
                order_type="market",
                stop_loss=signal.stop_loss_price,
                take_profit=signal.take_profit_price,
                idempotency_key=signal.decision_id,
                risk_check_id=risk_result.check_id,
            )
            fill = self._exec.fill_order(order, current_price=signal.entry_price)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"execution_error:{exc}")
            return self._build_cycle(
                cycle_id, started_at, symbol,
                CycleStatus.ORDER_FAILED,
                market_data_fetched=True,
                signal_generated=True,
                risk_approved=True,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                order_id=order.order_id if order else None,
                notes=notes,
            )

        # ── Step 6: Update risk state ─────────────────────────────────────────
        self._risk.update_daily_loss(
            realized_pnl_usd=self._exec.portfolio.realized_pnl_usd,
            equity=equity,
        )

        # ── Step 7: Build and audit cycle ─────────────────────────────────────
        cycle = self._build_cycle(
            cycle_id, started_at, symbol,
            CycleStatus.COMPLETED,
            market_data_fetched=True,
            signal_generated=True,
            risk_approved=True,
            order_created=order is not None,
            fill_simulated=fill is not None,
            decision_id=signal.decision_id,
            risk_check_id=risk_result.check_id,
            order_id=order.order_id if order else None,
            notes=notes,
        )
        self._write_audit(cycle)
        return cycle

    # ─── Private helpers ───────────────────────────────────────────────────────

    def _build_cycle(
        self,
        cycle_id: str,
        started_at: str,
        symbol: str,
        status: CycleStatus,
        *,
        market_data_fetched: bool = False,
        signal_generated: bool = False,
        risk_approved: bool = False,
        order_created: bool = False,
        fill_simulated: bool = False,
        decision_id: str | None = None,
        risk_check_id: str | None = None,
        order_id: str | None = None,
        notes: list[str] | None = None,
    ) -> LoopCycle:
        cycle = LoopCycle(
            cycle_id=cycle_id,
            started_at=started_at,
            completed_at=_now_utc(),
            symbol=symbol,
            status=status,
            market_data_fetched=market_data_fetched,
            signal_generated=signal_generated,
            risk_approved=risk_approved,
            order_created=order_created,
            fill_simulated=fill_simulated,
            decision_id=decision_id,
            risk_check_id=risk_check_id,
            order_id=order_id,
            notes=tuple(notes or []),
        )
        # Always audit non-completed cycles immediately
        if status != CycleStatus.COMPLETED:
            self._write_audit(cycle)
        return cycle

    def _write_audit(self, cycle: LoopCycle) -> None:
        try:
            record = {
                "cycle_id": cycle.cycle_id,
                "started_at": cycle.started_at,
                "completed_at": cycle.completed_at,
                "symbol": cycle.symbol,
                "status": cycle.status.value,
                "market_data_fetched": cycle.market_data_fetched,
                "signal_generated": cycle.signal_generated,
                "risk_approved": cycle.risk_approved,
                "order_created": cycle.order_created,
                "fill_simulated": cycle.fill_simulated,
                "decision_id": cycle.decision_id,
                "risk_check_id": cycle.risk_check_id,
                "order_id": cycle.order_id,
                "notes": list(cycle.notes),
            }
            with self._audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.error("[LOOP] Audit write failed: %s", exc)
