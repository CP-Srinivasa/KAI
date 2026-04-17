"""Core trading loop and control-plane helper surfaces."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.domain.document import AnalysisResult
from app.core.enums import ExecutionMode, SentimentLabel
from app.core.settings import get_settings
from app.execution.models import PaperPortfolio
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.base import BaseMarketDataAdapter
from app.market_data.service import create_market_data_adapter
from app.orchestrator.models import (
    CycleStatus,
    LoopCycle,
    LoopStatusSummary,
    RecentCyclesSummary,
    _new_cycle_id,
    _now_utc,
)
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.signals.generator import SignalGenerator
from app.signals.models import SignalCandidate, SignalDirection
from app.storage.models.trading import PortfolioStateRecord, TradingCycleRecord
from app.trading.signal_consensus import (
    GEMINI_OPENAI_BASE_URL,
    SignalConsensusValidator,
    ValidatorConfig,
)

logger = logging.getLogger(__name__)

_AUDIT_LOG = Path("artifacts/trading_loop_audit.jsonl")
_PAPER_EXECUTION_AUDIT_LOG = Path("artifacts/paper_execution_audit.jsonl")
_ALLOWED_CONTROL_MODES = frozenset({ExecutionMode.PAPER, ExecutionMode.SHADOW})

_TV_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "USD", "EUR", "BTC", "ETH")


def _normalize_tv_symbol(raw: str) -> str:
    """Convert TV-style ticker (BTCUSDT) to KAI canonical (BTC/USDT)."""
    s = raw.strip().upper()
    if "/" in s:
        return s
    for quote in _TV_QUOTE_SUFFIXES:
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}/{quote}"
    return f"{s}/USDT"


class TradingLoop:
    """
    Paper trading loop that coordinates market data, signal, risk, and paper execution.

    Design invariants:
    - One call to run_cycle = one explicit cycle only (no scheduler/autopilot)
    - Non-fatal errors are captured in cycle notes
    - Every cycle writes exactly one audit row
    - No live broker execution path exists in this module
    """

    def __init__(
        self,
        *,
        risk_engine: RiskEngine,
        execution_engine: PaperExecutionEngine,
        market_data_adapter: BaseMarketDataAdapter,
        signal_generator: SignalGenerator,
        consensus_validator: SignalConsensusValidator | None = None,
        audit_log_path: str | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._risk = risk_engine
        self._exec = execution_engine
        self._market_data = market_data_adapter
        self._signals = signal_generator
        self._consensus = consensus_validator
        self._audit_path = Path(audit_log_path or _AUDIT_LOG)
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._session_factory = session_factory

    @property
    def portfolio(self) -> PaperPortfolio:
        """Expose portfolio for read-only inspection."""
        return self._exec.portfolio

    @property
    def audit_path(self) -> Path:
        """Return the cycle-audit path for explicit control-plane read surfaces."""
        return self._audit_path

    async def run_cycle(
        self,
        analysis: AnalysisResult,
        symbol: str,
    ) -> LoopCycle:
        """
        Execute one cycle for a symbol and return the immutable cycle audit record.

        This method does not run loops in background and does not manage scheduling.
        """
        cycle_id = _new_cycle_id()
        started_at = _now_utc()
        notes: list[str] = []

        market_data = None
        try:
            market_data = await self._market_data.get_market_data_point(symbol)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"market_data_error:{exc}")

        if market_data is None:
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.NO_MARKET_DATA,
                notes=notes + [f"no_market_data:{symbol}"],
            )
            await self._write_db(cycle)
            return cycle

        adapter_note = f"market_data_source:{market_data.source}"
        notes.append(adapter_note)

        # Freshness enforcement: stale data → skip cycle explicitly (never silently)
        if market_data.is_stale:
            logger.warning(
                "[LOOP] SKIP cycle %s: market data stale (source=%s, freshness=%.1fs)",
                symbol,
                market_data.source,
                market_data.freshness_seconds,
            )
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.STALE_DATA,
                market_data_fetched=True,
                notes=notes
                + [
                    f"stale_data_skip:{symbol}",
                    f"freshness_seconds:{market_data.freshness_seconds:.1f}",
                ],
            )
            await self._write_db(cycle)
            return cycle

        signal = None
        try:
            signal = self._signals.generate(analysis, market_data, symbol)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"signal_error:{exc}")

        if signal is None:
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.NO_SIGNAL,
                market_data_fetched=True,
                notes=notes + ["signal_filtered_or_not_generated"],
            )
            await self._write_db(cycle)
            return cycle

        # Consensus gate — all validator LLMs must agree.
        if self._consensus is not None:
            consensus = await self._consensus.validate(signal, market_data)
            notes.append(
                f"consensus:{consensus.agreed}|"
                f"conf:{consensus.confidence:.2f}|"
                f"models:{consensus.validator_model}"
            )
            for vr in consensus.validator_results:
                notes.append(
                    f"validator:{vr.label}|"
                    f"agreed:{vr.agreed}|"
                    f"conf:{vr.confidence:.2f}"
                )
            if not consensus.agreed:
                cycle = self._build_cycle(
                    cycle_id,
                    started_at,
                    symbol,
                    CycleStatus.CONSENSUS_REJECTED,
                    market_data_fetched=True,
                    signal_generated=True,
                    notes=notes + [
                        f"consensus_reason:{consensus.reasoning}",
                    ],
                )
                await self._write_db(cycle)
                return cycle

        order_side = "buy" if signal.direction == SignalDirection.LONG else "sell"
        current_positions = len(self._exec.portfolio.positions)
        risk_result = self._risk.check_order(
            symbol=symbol,
            side=order_side,
            signal_confidence=signal.confidence_score,
            signal_confluence_count=signal.confluence_count,
            stop_loss_price=signal.stop_loss_price,
            current_open_positions=current_positions,
            entry_price=signal.entry_price,
            take_profit_price=signal.take_profit_price,
        )

        if not risk_result.approved:
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.RISK_REJECTED,
                market_data_fetched=True,
                signal_generated=True,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                notes=notes + risk_result.violations,
            )
            await self._write_db(cycle)
            return cycle

        equity = self._exec.portfolio.cash
        size_result = self._risk.calculate_position_size(
            symbol=symbol,
            entry_price=signal.entry_price,
            stop_loss_price=signal.stop_loss_price,
            equity=equity,
        )

        if not size_result.approved or size_result.position_size_units <= 0:
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.SIZE_REJECTED,
                market_data_fetched=True,
                signal_generated=True,
                risk_approved=True,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                notes=notes + [size_result.rationale],
            )
            await self._write_db(cycle)
            return cycle

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
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.ORDER_FAILED,
                market_data_fetched=True,
                signal_generated=True,
                risk_approved=True,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                order_id=order.order_id if order else None,
                notes=notes,
            )
            await self._write_db(cycle)
            return cycle

        # Fail-closed: an unfilled order must not be reported as completed.
        if fill is None:
            notes.append("fill_not_simulated")
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.ORDER_FAILED,
                market_data_fetched=True,
                signal_generated=True,
                risk_approved=True,
                order_created=order is not None,
                fill_simulated=False,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                order_id=order.order_id if order else None,
                notes=notes,
            )
            await self._write_db(cycle)
            return cycle

        self._risk.update_daily_loss(
            realized_pnl_usd=self._exec.portfolio.realized_pnl_usd,
            equity=equity,
        )

        cycle = self._build_cycle(
            cycle_id,
            started_at,
            symbol,
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
        await self._write_db(cycle)
        return cycle

    async def run_position_monitor(self) -> dict[str, object]:
        """Check SL/TP on every open position and close those that triggered.

        Fetches fresh market data once per open-position symbol, passes the
        price map to the paper engine, and returns a small summary dict
        suitable for logging / cron output.
        """
        portfolio = self._exec.portfolio
        open_symbols = list(portfolio.positions.keys())
        summary: dict[str, object] = {
            "checked": 0,
            "no_market_data": 0,
            "triggered": 0,
            "closes": [],
        }
        if not open_symbols:
            return summary

        prices: dict[str, float] = {}
        for symbol in open_symbols:
            try:
                md = await self._market_data.get_market_data_point(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[LOOP] monitor: market data error for %s: %s", symbol, exc)
                md = None
            if md is None or md.is_stale:
                summary["no_market_data"] = int(summary["no_market_data"]) + 1
                continue
            prices[symbol] = md.price
            summary["checked"] = int(summary["checked"]) + 1

        fills = self._exec.monitor_positions(prices)
        for fill in fills:
            summary["triggered"] = int(summary["triggered"]) + 1
            assert isinstance(summary["closes"], list)
            summary["closes"].append(
                {
                    "symbol": fill.symbol,
                    "quantity": fill.quantity,
                    "fill_price": fill.fill_price,
                    "realized_pnl_usd": self._exec.portfolio.realized_pnl_usd,
                }
            )

        if fills:
            self._risk.update_daily_loss(
                realized_pnl_usd=self._exec.portfolio.realized_pnl_usd,
                equity=self._exec.portfolio.cash,
            )

        return summary

    async def run_promoted_signal(
        self,
        signal: SignalCandidate,
    ) -> LoopCycle:
        """Execute one cycle for a pre-approved promoted TV signal.

        Skips SignalGenerator (signal already exists) but still fetches
        fresh market data, runs risk check, position sizing, and paper
        execution.  Entry price is updated to the live market price.
        """
        cycle_id = _new_cycle_id()
        started_at = _now_utc()
        notes: list[str] = [
            f"source:tv_promoted|decision_id:{signal.decision_id}",
        ]
        if signal.provenance:
            notes.append(
                f"provenance:{signal.provenance.source}|{signal.provenance.version}"
            )

        symbol = _normalize_tv_symbol(signal.symbol)

        market_data = None
        try:
            market_data = await self._market_data.get_market_data_point(symbol)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"market_data_error:{exc}")

        if market_data is None:
            cycle = self._build_cycle(
                cycle_id, started_at, symbol, CycleStatus.NO_MARKET_DATA,
                notes=notes + [f"no_market_data:{symbol}"],
            )
            await self._write_db(cycle)
            return cycle

        notes.append(f"market_data_source:{market_data.source}")

        if market_data.is_stale:
            cycle = self._build_cycle(
                cycle_id, started_at, symbol, CycleStatus.STALE_DATA,
                market_data_fetched=True,
                notes=notes + [f"stale_data_skip:{symbol}"],
            )
            await self._write_db(cycle)
            return cycle

        live_price = market_data.price

        if self._consensus is not None:
            consensus = await self._consensus.validate(signal, market_data)
            notes.append(
                f"consensus:{consensus.agreed}|conf:{consensus.confidence:.2f}"
            )
            if not consensus.agreed:
                cycle = self._build_cycle(
                    cycle_id, started_at, symbol,
                    CycleStatus.CONSENSUS_REJECTED,
                    market_data_fetched=True, signal_generated=True,
                    notes=notes + [f"consensus_reason:{consensus.reasoning}"],
                )
                await self._write_db(cycle)
                return cycle

        order_side = "buy" if signal.direction == SignalDirection.LONG else "sell"
        current_positions = len(self._exec.portfolio.positions)
        risk_result = self._risk.check_order(
            symbol=symbol,
            side=order_side,
            signal_confidence=signal.confidence_score,
            signal_confluence_count=signal.confluence_count,
            stop_loss_price=signal.stop_loss_price,
            current_open_positions=current_positions,
            entry_price=signal.entry_price,
            take_profit_price=signal.take_profit_price,
        )

        if not risk_result.approved:
            cycle = self._build_cycle(
                cycle_id, started_at, symbol, CycleStatus.RISK_REJECTED,
                market_data_fetched=True, signal_generated=True,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                notes=notes + risk_result.violations,
            )
            await self._write_db(cycle)
            return cycle

        equity = self._exec.portfolio.cash
        size_result = self._risk.calculate_position_size(
            symbol=symbol,
            entry_price=live_price,
            stop_loss_price=signal.stop_loss_price,
            equity=equity,
        )

        if not size_result.approved or size_result.position_size_units <= 0:
            cycle = self._build_cycle(
                cycle_id, started_at, symbol, CycleStatus.SIZE_REJECTED,
                market_data_fetched=True, signal_generated=True,
                risk_approved=True,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                notes=notes + [size_result.rationale],
            )
            await self._write_db(cycle)
            return cycle

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
            fill = self._exec.fill_order(order, current_price=live_price)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"execution_error:{exc}")
            cycle = self._build_cycle(
                cycle_id, started_at, symbol, CycleStatus.ORDER_FAILED,
                market_data_fetched=True, signal_generated=True,
                risk_approved=True,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                order_id=order.order_id if order else None,
                notes=notes,
            )
            await self._write_db(cycle)
            return cycle

        if fill is None:
            notes.append("fill_not_simulated")
            cycle = self._build_cycle(
                cycle_id, started_at, symbol, CycleStatus.ORDER_FAILED,
                market_data_fetched=True, signal_generated=True,
                risk_approved=True, order_created=order is not None,
                fill_simulated=False,
                decision_id=signal.decision_id,
                risk_check_id=risk_result.check_id,
                order_id=order.order_id if order else None,
                notes=notes,
            )
            await self._write_db(cycle)
            return cycle

        self._risk.update_daily_loss(
            realized_pnl_usd=self._exec.portfolio.realized_pnl_usd,
            equity=equity,
        )

        cycle = self._build_cycle(
            cycle_id, started_at, symbol, CycleStatus.COMPLETED,
            market_data_fetched=True, signal_generated=True,
            risk_approved=True, order_created=order is not None,
            fill_simulated=fill is not None,
            decision_id=signal.decision_id,
            risk_check_id=risk_result.check_id,
            order_id=order.order_id if order else None,
            notes=notes,
        )
        self._write_audit(cycle)
        await self._write_db(cycle)
        return cycle

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
            with self._audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.error("[LOOP] Audit write failed: %s", exc)

    async def _write_db(self, cycle: LoopCycle) -> None:
        """Dual-write cycle to DB (session-per-cycle). Non-fatal: DB errors never stop the loop."""
        if self._session_factory is None:
            return
        try:
            async with self._session_factory() as session:
                db_record = TradingCycleRecord(
                    cycle_id=cycle.cycle_id,
                    symbol=cycle.symbol,
                    mode="paper",
                    provider="coingecko",
                    analysis_profile="conservative",
                    status=cycle.status.value,
                    market_data_fetched=cycle.market_data_fetched,
                    signal_generated=cycle.signal_generated,
                    risk_approved=cycle.risk_approved,
                    order_created=cycle.order_created,
                    fill_simulated=cycle.fill_simulated,
                    decision_id=cycle.decision_id,
                    risk_check_id=cycle.risk_check_id,
                    order_id=cycle.order_id,
                    started_at=cycle.started_at,
                    completed_at=cycle.completed_at,
                    notes=list(cycle.notes),
                    created_at=datetime.now(UTC),
                )
                session.add(db_record)

                if cycle.fill_simulated:
                    portfolio = self._exec.portfolio
                    exposure = sum(
                        p.quantity * p.avg_entry_price for p in portfolio.positions.values()
                    )
                    state_record = PortfolioStateRecord(
                        cycle_id=cycle.cycle_id,
                        symbol=cycle.symbol,
                        equity_usd=portfolio.cash + exposure,
                        position_count=len(portfolio.positions),
                        gross_exposure_usd=exposure,
                        positions_json=portfolio.to_dict(),
                        snapshot_mode="paper",
                        created_at=datetime.now(UTC),
                    )
                    session.add(state_record)

                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("[LOOP] DB dual-write failed (non-fatal): %s", exc)


def _normalize_loop_mode(mode: str | ExecutionMode) -> ExecutionMode:
    if isinstance(mode, ExecutionMode):
        return mode
    normalized = mode.strip().lower()
    try:
        return ExecutionMode(normalized)
    except ValueError as exc:
        raise ValueError(f"unsupported_loop_mode:{mode}") from exc


def _run_once_guard(mode: ExecutionMode) -> tuple[bool, str | None]:
    if mode in _ALLOWED_CONTROL_MODES:
        return True, None
    return (
        False,
        (
            "trading_loop_run_once blocked: "
            f"mode={mode.value} is not allowed (allowed: paper, shadow)"
        ),
    )


def _build_risk_limits_from_settings() -> RiskLimits:
    settings = get_settings()
    risk = settings.risk
    return RiskLimits(
        initial_equity=risk.initial_equity,
        max_risk_per_trade_pct=risk.max_risk_per_trade_pct,
        max_daily_loss_pct=risk.max_daily_loss_pct,
        max_total_drawdown_pct=risk.max_total_drawdown_pct,
        max_open_positions=risk.max_open_positions,
        max_leverage=risk.max_leverage,
        require_stop_loss=risk.require_stop_loss,
        allow_averaging_down=risk.allow_averaging_down,
        allow_martingale=risk.allow_martingale,
        kill_switch_enabled=risk.kill_switch_enabled,
        min_signal_confidence=risk.min_signal_confidence,
        min_signal_confluence_count=risk.min_signal_confluence_count,
    )


def build_loop_trigger_analysis(
    *,
    symbol: str,
    analysis_profile: str = "conservative",
) -> AnalysisResult:
    """Build a controlled analysis payload for explicit run-once triggers."""
    profile = analysis_profile.strip().lower()
    asset = symbol.split("/")[0].upper()

    if profile == "conservative":
        return AnalysisResult(
            document_id=f"loop_control_{asset.lower()}_conservative",
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=0.0,
            relevance_score=0.4,
            impact_score=0.2,
            confidence_score=0.5,
            novelty_score=0.2,
            market_scope=None,
            affected_assets=[],
            affected_sectors=[],
            event_type="control_plane_health_check",
            explanation_short="Conservative control-plane trigger (no actionable signal).",
            explanation_long=(
                "This run-once trigger is a safe operational cycle check and intentionally "
                "does not carry actionable trading intent."
            ),
            actionable=False,
            tags=["control_plane", "conservative", "run_once"],
            spam_probability=0.0,
        )

    if profile == "bullish":
        return AnalysisResult(
            document_id=f"loop_control_{asset.lower()}_bullish",
            sentiment_label=SentimentLabel.BULLISH,
            sentiment_score=0.8,
            relevance_score=0.85,
            impact_score=0.8,
            confidence_score=0.85,
            novelty_score=0.7,
            market_scope=None,
            affected_assets=[asset, symbol],
            affected_sectors=[],
            event_type="control_plane_bullish_probe",
            explanation_short="Bullish control-plane probe for paper/shadow testing.",
            explanation_long=(
                "This profile is used only for controlled paper/shadow cycle checks."
            ),
            actionable=True,
            tags=["control_plane", "bullish", "run_once"],
            spam_probability=0.0,
        )

    if profile == "bearish":
        return AnalysisResult(
            document_id=f"loop_control_{asset.lower()}_bearish",
            sentiment_label=SentimentLabel.BEARISH,
            sentiment_score=-0.8,
            relevance_score=0.85,
            impact_score=0.8,
            confidence_score=0.85,
            novelty_score=0.7,
            market_scope=None,
            affected_assets=[asset, symbol],
            affected_sectors=[],
            event_type="control_plane_bearish_probe",
            explanation_short="Bearish control-plane probe for paper/shadow testing.",
            explanation_long=(
                "This profile is used only for controlled paper/shadow cycle checks."
            ),
            actionable=True,
            tags=["control_plane", "bearish", "run_once"],
            spam_probability=0.0,
        )

    raise ValueError(
        f"unsupported_analysis_profile:{analysis_profile} (allowed: conservative, bullish, bearish)"
    )


def _build_consensus_validator(
    enable: bool,
    consensus_model: str,
    settings: object,
) -> SignalConsensusValidator | None:
    """Build consensus validator with all available LLM backends."""
    if not enable:
        return None

    configs: list[ValidatorConfig] = []

    openai_key = getattr(
        getattr(settings, "providers", None), "openai_api_key", "",
    )
    if openai_key:
        configs.append(ValidatorConfig(
            api_key=openai_key,
            model=consensus_model,
            label="openai",
        ))

    gemini_key = getattr(
        getattr(settings, "providers", None), "gemini_api_key", "",
    )
    gemini_model = getattr(
        getattr(settings, "providers", None), "gemini_model", "",
    ) or "gemini-2.5-flash"
    if gemini_key:
        configs.append(ValidatorConfig(
            api_key=gemini_key,
            model=gemini_model,
            label="gemini",
            base_url=GEMINI_OPENAI_BASE_URL,
            max_tokens=1024,
            timeout=30,
        ))

    if not configs:
        return None

    return SignalConsensusValidator(configs=configs)


def build_trading_loop(
    *,
    mode: str | ExecutionMode = ExecutionMode.PAPER,
    provider: str | None = None,
    loop_audit_path: str | Path = _AUDIT_LOG,
    execution_audit_path: str | Path = _PAPER_EXECUTION_AUDIT_LOG,
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    enable_consensus: bool = False,
    consensus_model: str = "gpt-4o-mini",
    rehydrate_from_audit: bool = True,
) -> TradingLoop:
    """Build the canonical trading loop for explicit paper/shadow run-once execution.

    provider: market data provider name. If None, reads from APP_MARKET_DATA_PROVIDER
    (default: "coingecko"). Pass "mock" explicitly in tests.

    rehydrate_from_audit: when True (default), replay the execution audit JSONL
    into the fresh engine so previously opened positions are observable across
    process invocations (required for cron-driven SL/TP monitoring).
    """
    normalized_mode = _normalize_loop_mode(mode)
    allowed, reason = _run_once_guard(normalized_mode)
    if not allowed:
        raise ValueError(reason or "trading_loop_run_once blocked")

    settings = get_settings()
    resolved_provider = provider if provider is not None else settings.market_data_provider
    risk_engine = RiskEngine(_build_risk_limits_from_settings())
    execution_engine = PaperExecutionEngine(
        initial_equity=settings.execution.paper_initial_equity,
        fee_pct=settings.execution.paper_fee_pct,
        slippage_pct=settings.execution.paper_slippage_pct,
        live_enabled=False,
        audit_log_path=str(execution_audit_path),
    )
    if rehydrate_from_audit:
        execution_engine.rehydrate_from_audit()
    market_data_adapter = create_market_data_adapter(
        provider=resolved_provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    signal_generator = SignalGenerator(
        min_confidence=settings.risk.min_signal_confidence,
        min_confluence=settings.risk.min_signal_confluence_count,
        mode=normalized_mode.value,
        venue="paper",
    )
    consensus_validator = _build_consensus_validator(
        enable_consensus, consensus_model, settings,
    )

    from app.storage.db.session import build_session_factory

    session_factory = build_session_factory(settings.db)

    return TradingLoop(
        risk_engine=risk_engine,
        execution_engine=execution_engine,
        market_data_adapter=market_data_adapter,
        signal_generator=signal_generator,
        consensus_validator=consensus_validator,
        audit_log_path=str(loop_audit_path),
        session_factory=session_factory,
    )


async def run_trading_loop_once(
    *,
    symbol: str = "BTC/USDT",
    mode: str | ExecutionMode = ExecutionMode.PAPER,
    provider: str | None = None,
    analysis_profile: str = "conservative",
    analysis_result: AnalysisResult | None = None,
    loop_audit_path: str | Path = _AUDIT_LOG,
    execution_audit_path: str | Path = _PAPER_EXECUTION_AUDIT_LOG,
    enable_consensus: bool = False,
    consensus_model: str = "gpt-4o-mini",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> LoopCycle:
    """Run exactly one explicit paper/shadow cycle with fail-closed mode guard.

    If *analysis_result* is provided (e.g. from the D-119 alert bridge), it is
    used directly instead of building a synthetic trigger analysis.  This allows
    real LLM-generated analyses to drive paper-trade fills.
    """
    normalized_mode = _normalize_loop_mode(mode)
    allowed, reason = _run_once_guard(normalized_mode)
    if not allowed:
        raise ValueError(reason or "trading_loop_run_once blocked")

    loop = build_trading_loop(
        mode=normalized_mode,
        provider=provider,
        loop_audit_path=loop_audit_path,
        execution_audit_path=execution_audit_path,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
        enable_consensus=enable_consensus,
        consensus_model=consensus_model,
    )
    analysis = analysis_result or build_loop_trigger_analysis(
        symbol=symbol,
        analysis_profile=analysis_profile,
    )
    return await loop.run_cycle(analysis, symbol)


async def run_position_monitor_once(
    *,
    provider: str | None = None,
    loop_audit_path: str | Path = _AUDIT_LOG,
    execution_audit_path: str | Path = _PAPER_EXECUTION_AUDIT_LOG,
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Build a paper-mode loop (rehydrated from audit) and run one SL/TP monitor pass.

    Intended for cron invocation: fetches live prices for every open position,
    closes any position whose SL/TP fired, returns a summary dict.
    """
    loop = build_trading_loop(
        mode=ExecutionMode.PAPER,
        provider=provider,
        loop_audit_path=loop_audit_path,
        execution_audit_path=execution_audit_path,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
        enable_consensus=False,
    )
    return await loop.run_position_monitor()


def load_trading_loop_cycles(audit_path: str | Path = _AUDIT_LOG) -> list[dict[str, object]]:
    """Load loop cycle audit rows from JSONL, skipping malformed lines."""
    path = Path(audit_path)
    if not path.exists():
        return []

    records: list[dict[str, object]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def build_recent_cycles_summary(
    *,
    audit_path: str | Path = _AUDIT_LOG,
    last_n: int = 20,
) -> RecentCyclesSummary:
    """Build read-only recent-cycle summary from the canonical trading-loop audit."""
    normalized_last_n = max(1, last_n)
    records = load_trading_loop_cycles(audit_path)

    status_counts: dict[str, int] = {}
    for record in records:
        status = record.get("status", "unknown")
        status_name = str(status)
        status_counts[status_name] = status_counts.get(status_name, 0) + 1

    recent = tuple(dict(row) for row in records[-normalized_last_n:])

    return RecentCyclesSummary(
        total_cycles=len(records),
        status_counts=status_counts,
        recent_cycles=recent,
        last_n=normalized_last_n,
        audit_path=str(Path(audit_path)),
    )


def build_loop_status_summary(
    *,
    audit_path: str | Path = _AUDIT_LOG,
    mode: str | ExecutionMode = ExecutionMode.PAPER,
) -> LoopStatusSummary:
    """Build read-only loop status summary with explicit run-once mode guard."""
    normalized_mode = _normalize_loop_mode(mode)
    run_once_allowed, block_reason = _run_once_guard(normalized_mode)

    records = load_trading_loop_cycles(audit_path)
    last_record = records[-1] if records else None

    def _extract_optional_str(field_name: str) -> str | None:
        if not isinstance(last_record, dict):
            return None
        value = last_record.get(field_name)
        if value is None:
            return None
        return str(value)

    last_cycle_id = _extract_optional_str("cycle_id")
    last_cycle_status = _extract_optional_str("status")
    last_cycle_symbol = _extract_optional_str("symbol")
    last_cycle_completed_at = (
        str(last_record.get("completed_at"))
        if isinstance(last_record, dict) and last_record.get("completed_at") is not None
        else None
    )

    return LoopStatusSummary(
        mode=normalized_mode.value,
        run_once_allowed=run_once_allowed,
        run_once_block_reason=block_reason,
        total_cycles=len(records),
        last_cycle_id=last_cycle_id,
        last_cycle_status=last_cycle_status,
        last_cycle_symbol=last_cycle_symbol,
        last_cycle_completed_at=last_cycle_completed_at,
        audit_path=str(Path(audit_path)),
    )


async def run_promoted_signals_once(
    *,
    provider: str | None = None,
    loop_audit_path: str | Path = _AUDIT_LOG,
    execution_audit_path: str | Path = _PAPER_EXECUTION_AUDIT_LOG,
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    enable_consensus: bool = False,
    consensus_model: str = "gpt-4o-mini",
) -> list[LoopCycle]:
    """Load all pending promoted TV signals and run each through the paper loop.

    Returns one LoopCycle per signal. Consumed IDs are marked after each
    successful processing (regardless of cycle outcome — consumed means
    the signal was attempted, not that it filled).
    """
    from app.signals.tv_consumer import load_pending_promoted, mark_consumed

    candidates = load_pending_promoted()
    if not candidates:
        logger.info("[TV-4] No pending promoted signals to process.")
        return []

    loop = build_trading_loop(
        mode=ExecutionMode.PAPER,
        provider=provider,
        loop_audit_path=loop_audit_path,
        execution_audit_path=execution_audit_path,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
        enable_consensus=enable_consensus,
        consensus_model=consensus_model,
    )

    cycles: list[LoopCycle] = []
    for candidate in candidates:
        cycle = await loop.run_promoted_signal(candidate)
        mark_consumed(candidate.decision_id)
        cycles.append(cycle)
        logger.info(
            "[TV-4] Processed %s → %s (%s)",
            candidate.decision_id,
            cycle.status.value,
            _normalize_tv_symbol(candidate.symbol),
        )

    return cycles
