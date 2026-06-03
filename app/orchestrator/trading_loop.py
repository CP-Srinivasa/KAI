"""Core trading loop and control-plane helper surfaces."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.domain.document import AnalysisResult
from app.core.enums import EntryMode, ExecutionMode, SentimentLabel
from app.core.settings import get_settings
from app.execution.models import PaperPortfolio
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.base import BaseMarketDataAdapter
from app.market_data.indicators import compute_atr
from app.market_data.service import create_market_data_adapter
from app.orchestrator.models import (
    CycleStatus,
    LoopCycle,
    LoopStatusSummary,
    PriorityGateSummary,
    RecentCyclesSummary,
    _new_cycle_id,
    _now_utc,
)
from app.risk.churn_killer import ChurnKillerConfig, ChurnVerdict, evaluate_churn_gate
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.risk.post_stop_cooldown import is_symbol_in_post_stop_cooldown
from app.security.kyt.models import KytAssessment
from app.signals.generator import SignalGenerator
from app.signals.models import SignalCandidate, SignalDirection
from app.storage.models.trading import PortfolioStateRecord, TradingCycleRecord
from app.trading.diversification import (
    DiversificationDecision,
    DiversificationGuard,
    exposures_from_paper_portfolio,
)
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
            return f"{s[: -len(quote)]}/{quote}"
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

        # Entry-Safety-Mode (Goal 2026-06-01). Highest-level kill-switch for the
        # AUTONOMOUS loop: in DISABLED mode no new positions are opened at all.
        # Cheapest possible reject — runs before market-data fetch / signal gen.
        # Scope: only this autonomous analysis-driven path. Operator-/bridge-/
        # premium-promoted entries (run_promoted_signal) are a different signal
        # source and are intentionally NOT gated here; they keep their own risk
        # gates + approval. Exits/risk-reductions are never gated by entry_mode.
        entry_mode = get_settings().execution.entry_mode
        shadow_only = False
        if not entry_mode.allows_autonomous_loop_entry:
            # Phase B: when shadow-diagnostics is ON we DON'T early-return; we run
            # the read-only pipeline below and record a hypothetical candidate
            # (no execution) so the disabled signal keeps producing learning
            # evidence. Flag OFF → original cheapest-possible reject.
            if not get_settings().execution.shadow_diagnostics:
                cycle = self._build_cycle(
                    cycle_id,
                    started_at,
                    symbol,
                    CycleStatus.ENTRY_MODE_BLOCKED,
                    notes=notes + [f"entry_mode_blocked:{entry_mode.value}"],
                )
                await self._write_db(cycle)
                return cycle
            shadow_only = True

        # D-182: priority-tier gate. Default min_priority=1 is a no-op; setting
        # it to 10 restricts paper fills to the high-conviction tier where
        # live hit-rate evidence is disjoint from standard tier (D-149).
        min_priority = get_settings().execution.paper_min_priority
        if min_priority > 1:
            observed = analysis.recommended_priority
            if observed is None or observed < min_priority:
                cycle = self._build_cycle(
                    cycle_id,
                    started_at,
                    symbol,
                    CycleStatus.PRIORITY_REJECTED,
                    notes=notes + [f"priority_gate_reject:{observed}|threshold:{min_priority}"],
                )
                await self._write_db(cycle)
                return cycle

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
                notes.append(f"validator:{vr.label}|agreed:{vr.agreed}|conf:{vr.confidence:.2f}")
            if not consensus.agreed:
                cycle = self._build_cycle(
                    cycle_id,
                    started_at,
                    symbol,
                    CycleStatus.CONSENSUS_REJECTED,
                    market_data_fetched=True,
                    signal_generated=True,
                    notes=notes
                    + [
                        f"consensus_reason:{consensus.reasoning}",
                    ],
                )
                await self._write_db(cycle)
                return cycle

        # P1.1: Dynamic ATR Geometry
        if signal.stop_loss_price is None:
            try:
                ohlcv_data = await self._market_data.get_ohlcv(symbol, limit=20)
                atr = compute_atr(ohlcv_data, period=14)
                if atr is not None:
                    notes.append(f"atr_calculated:{atr:.4f}")
                else:
                    notes.append("atr_calculated:None")

                sl, tp = self._risk.calculate_risk_geometry(
                    entry_price=signal.entry_price,
                    direction=signal.direction.value,
                    atr=atr,
                )

                signal = replace(
                    signal,
                    stop_loss_price=sl,
                    take_profit_price=tp,
                )
            except Exception as exc:
                notes.append(f"atr_geometry_error:{exc}")

        order_side = "buy" if signal.direction == SignalDirection.LONG else "sell"

        # Phase B shadow-only path: entry_mode is disabled but shadow-diagnostics
        # is ON. We have the raw signal + geometry the loop WOULD have entered;
        # record it as a hypothetical candidate (no entry-gates applied, so we
        # measure the SIGNAL not the gates) and stop before any execution. This
        # is captured BEFORE cooldown/churn/risk on purpose — those gates are
        # what we are trying to fix, so shadow evidence must be gate-independent.
        if shadow_only:
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.ENTRY_MODE_BLOCKED,
                market_data_fetched=True,
                signal_generated=True,
                decision_id=signal.decision_id,
                notes=notes
                + [f"entry_mode_blocked:{entry_mode.value}", "shadow_candidate_recorded"],
            )
            self._record_shadow_candidate(
                cycle=cycle,
                signal=signal,
                order_side=order_side,
                entry_mode_value=entry_mode.value,
                recommended_priority=analysis.recommended_priority,
            )
            await self._write_db(cycle)
            return cycle

        # NEO-V2: per-symbol post-stop cooldown. Cheapest reject — runs before the
        # risk gate so a symbol that just stopped out is not re-entered (and
        # re-charged ~1.2% round-trip fees) within the cooldown window. Additive:
        # placed before check_order, it does not touch any existing risk gate.
        if self._in_post_stop_cooldown(symbol):
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.COOLDOWN_REJECTED,
                market_data_fetched=True,
                signal_generated=True,
                decision_id=signal.decision_id,
                notes=notes + ["post_stop_cooldown"],
            )
            await self._write_db(cycle)
            return cycle

        # Sprint E (Goal §5): churn-killer. Generalises the cooldown above (any
        # risk-reducing close + loss-streak backoff) and adds global rate/turnover
        # limits. Entry-only — exits are never gated here.
        churn = self._evaluate_churn(symbol)
        if churn.blocked:
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                self._churn_cycle_status(churn),
                market_data_fetched=True,
                signal_generated=True,
                decision_id=signal.decision_id,
                notes=notes + [f"churn:{churn.reason}|{churn.detail}"],
            )
            await self._write_db(cycle)
            return cycle

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

        # Diversification / concentration guard (default-off, shadow-first).
        # Stamps the audit with the concentration recommendation; only blocks
        # the cycle when enforce mode is active and the action is `reject`.
        notional = size_result.position_size_units * signal.entry_price
        div_decision = self._evaluate_diversification(symbol=symbol, notional_usd=notional)
        if div_decision is not None:
            notes.extend(self._diversification_notes(div_decision))
            if div_decision.blocks:
                cycle = self._build_cycle(
                    cycle_id,
                    started_at,
                    symbol,
                    CycleStatus.DIVERSIFICATION_REJECTED,
                    market_data_fetched=True,
                    signal_generated=True,
                    risk_approved=True,
                    decision_id=signal.decision_id,
                    risk_check_id=risk_result.check_id,
                    notes=notes,
                )
                await self._write_db(cycle)
                return cycle

        # KYT (Know Your Transaction) pre-transaction check (default-off,
        # shadow-first). Screens symbol/venue + behavioural patterns, stamps the
        # cycle audit, and only blocks in enforce mode on a hold/block/
        # manual_review decision. Never crashes the loop. DS-20260529-V1.
        kyt_assessment = self._evaluate_kyt(
            cycle_id=cycle_id,
            symbol=symbol,
            side=order_side,
            quantity=size_result.position_size_units,
            entry_price=signal.entry_price,
            source=signal.provenance.source if signal.provenance else "",
            correlation_id=signal.decision_id,
        )
        if kyt_assessment is not None:
            notes.append(
                f"kyt:{kyt_assessment.decision.value}|risk:{kyt_assessment.risk_level.value}"
                f"|score:{kyt_assessment.score}"
            )
            from app.security.kyt.gate import enforce_blocks

            if enforce_blocks(kyt_assessment):
                cycle = self._build_cycle(
                    cycle_id,
                    started_at,
                    symbol,
                    CycleStatus.KYT_REJECTED,
                    market_data_fetched=True,
                    signal_generated=True,
                    risk_approved=True,
                    decision_id=signal.decision_id,
                    risk_check_id=risk_result.check_id,
                    notes=notes,
                )
                await self._write_db(cycle)
                return cycle

        order = None
        fill = None
        # NEO-P-20260603-001: attribute the autonomous fill to its signal source.
        # document_id traces the originating analysis (canary probes are
        # "loop_control_<asset>_<profile>"; the real generator carries the RSS/
        # news doc id). signal_source is a coarse bucket so edge_report can split
        # canary-probe vs real-generator fills. "" stays the unknown default for
        # any path that does not set source_document_id.
        attribution_doc_id = signal.source_document_id or analysis.document_id or ""
        if attribution_doc_id.startswith("loop_control_"):
            signal_source = "canary_probe"
        elif attribution_doc_id:
            signal_source = "autonomous_generator"
        else:
            signal_source = ""
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
                source=signal_source,
                document_id=attribution_doc_id,
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
        checked = 0
        no_market_data = 0
        triggered = 0
        closes: list[dict[str, float | str]] = []
        if not open_symbols:
            return {
                "checked": checked,
                "no_market_data": no_market_data,
                "triggered": triggered,
                "closes": closes,
            }

        prices: dict[str, float] = {}
        for symbol in open_symbols:
            try:
                md = await self._market_data.get_market_data_point(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[LOOP] monitor: market data error for %s: %s", symbol, exc)
                md = None
            if md is None or md.is_stale:
                no_market_data += 1
                continue
            prices[symbol] = md.price
            checked += 1

        fills = self._exec.monitor_positions(prices)
        for fill in fills:
            triggered += 1
            closes.append(
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

        return {
            "checked": checked,
            "no_market_data": no_market_data,
            "triggered": triggered,
            "closes": closes,
        }

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
            notes.append(f"provenance:{signal.provenance.source}|{signal.provenance.version}")

        symbol = _normalize_tv_symbol(signal.symbol)

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

        notes.append(f"market_data_source:{market_data.source}")

        if market_data.is_stale:
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.STALE_DATA,
                market_data_fetched=True,
                notes=notes + [f"stale_data_skip:{symbol}"],
            )
            await self._write_db(cycle)
            return cycle

        live_price = market_data.price

        if self._consensus is not None:
            consensus = await self._consensus.validate(signal, market_data)
            notes.append(f"consensus:{consensus.agreed}|conf:{consensus.confidence:.2f}")
            if not consensus.agreed:
                cycle = self._build_cycle(
                    cycle_id,
                    started_at,
                    symbol,
                    CycleStatus.CONSENSUS_REJECTED,
                    market_data_fetched=True,
                    signal_generated=True,
                    notes=notes + [f"consensus_reason:{consensus.reasoning}"],
                )
                await self._write_db(cycle)
                return cycle

        # P1.1: Dynamic ATR Geometry
        if signal.stop_loss_price is None:
            try:
                ohlcv_data = await self._market_data.get_ohlcv(symbol, limit=20)
                atr = compute_atr(ohlcv_data, period=14)
                if atr is not None:
                    notes.append(f"atr_calculated:{atr:.4f}")
                else:
                    notes.append("atr_calculated:None")

                sl, tp = self._risk.calculate_risk_geometry(
                    entry_price=live_price,
                    direction=signal.direction.value,
                    atr=atr,
                )

                signal = replace(
                    signal,
                    stop_loss_price=sl,
                    take_profit_price=tp,
                )
            except Exception as exc:
                notes.append(f"atr_geometry_error:{exc}")

        order_side = "buy" if signal.direction == SignalDirection.LONG else "sell"

        # NEO-V2: per-symbol post-stop cooldown (see run_cycle path for rationale).
        if self._in_post_stop_cooldown(symbol):
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                CycleStatus.COOLDOWN_REJECTED,
                market_data_fetched=True,
                signal_generated=True,
                decision_id=signal.decision_id,
                notes=notes + ["post_stop_cooldown"],
            )
            await self._write_db(cycle)
            return cycle

        # Sprint E (Goal §5): churn-killer (see run_cycle path for rationale).
        churn = self._evaluate_churn(symbol)
        if churn.blocked:
            cycle = self._build_cycle(
                cycle_id,
                started_at,
                symbol,
                self._churn_cycle_status(churn),
                market_data_fetched=True,
                signal_generated=True,
                decision_id=signal.decision_id,
                notes=notes + [f"churn:{churn.reason}|{churn.detail}"],
            )
            await self._write_db(cycle)
            return cycle

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
            entry_price=live_price,
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

        # Diversification / concentration guard (default-off, shadow-first).
        notional = size_result.position_size_units * live_price
        div_decision = self._evaluate_diversification(symbol=symbol, notional_usd=notional)
        if div_decision is not None:
            notes.extend(self._diversification_notes(div_decision))
            if div_decision.blocks:
                cycle = self._build_cycle(
                    cycle_id,
                    started_at,
                    symbol,
                    CycleStatus.DIVERSIFICATION_REJECTED,
                    market_data_fetched=True,
                    signal_generated=True,
                    risk_approved=True,
                    decision_id=signal.decision_id,
                    risk_check_id=risk_result.check_id,
                    notes=notes,
                )
                await self._write_db(cycle)
                return cycle

        # KYT (Know Your Transaction) pre-transaction check (default-off,
        # shadow-first). Screens symbol/venue + behavioural patterns, stamps the
        # cycle audit, and only blocks in enforce mode on a hold/block/
        # manual_review decision. Never crashes the loop. DS-20260529-V1.
        kyt_assessment = self._evaluate_kyt(
            cycle_id=cycle_id,
            symbol=symbol,
            side=order_side,
            quantity=size_result.position_size_units,
            entry_price=signal.entry_price,
            source=signal.provenance.source if signal.provenance else "",
            correlation_id=signal.decision_id,
        )
        if kyt_assessment is not None:
            notes.append(
                f"kyt:{kyt_assessment.decision.value}|risk:{kyt_assessment.risk_level.value}"
                f"|score:{kyt_assessment.score}"
            )
            from app.security.kyt.gate import enforce_blocks

            if enforce_blocks(kyt_assessment):
                cycle = self._build_cycle(
                    cycle_id,
                    started_at,
                    symbol,
                    CycleStatus.KYT_REJECTED,
                    market_data_fetched=True,
                    signal_generated=True,
                    risk_approved=True,
                    decision_id=signal.decision_id,
                    risk_check_id=risk_result.check_id,
                    notes=notes,
                )
                await self._write_db(cycle)
                return cycle

        order = None
        fill = None
        # NEO-P-20260603-001: promoted/bridge signals are their own source bucket.
        # Prefer the structured provenance.source; fall back to "tv_promoted".
        promoted_source = "tv_promoted"
        if signal.provenance and signal.provenance.source:
            promoted_source = signal.provenance.source
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
                source=promoted_source,
                document_id=signal.source_document_id or "",
            )
            fill = self._exec.fill_order(order, current_price=live_price)
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

    def _record_shadow_candidate(
        self,
        *,
        cycle: LoopCycle,
        signal: SignalCandidate,
        order_side: str,
        entry_mode_value: str,
        recommended_priority: int | None,
    ) -> None:
        """Phase B: persist a hypothetical entry candidate (no execution).

        Fully fail-soft — a shadow-ledger problem must never affect the cycle.
        The read-only ``check_order`` records the gate verdict the signal WOULD
        have hit, so the operator can later separate "signal bad" from "gate
        rejected" without re-running anything.
        """
        try:
            from app.observability.shadow_candidate_ledger import (
                ShadowCandidate,
                record_candidate,
            )

            side = "long" if signal.direction == SignalDirection.LONG else "short"
            started = cycle.started_at
            ts_utc = started if isinstance(started, str) else started.isoformat()
            regime_stamp = self._regime_stamp_for_audit(cycle)

            would_reject: bool | None = None
            reason_codes: list[str] = []
            try:
                rr = self._risk.check_order(
                    symbol=cycle.symbol,
                    side=order_side,
                    signal_confidence=signal.confidence_score,
                    signal_confluence_count=signal.confluence_count,
                    stop_loss_price=signal.stop_loss_price,
                    current_open_positions=len(self._exec.portfolio.positions),
                    entry_price=signal.entry_price,
                    take_profit_price=signal.take_profit_price,
                )
                would_reject = not rr.approved
                reason_codes = list(rr.reason_codes)
            except Exception as exc:  # noqa: BLE001 — gate eval is best-effort
                logger.debug("[LOOP] shadow gate-eval failed: %s", exc)

            candidate = ShadowCandidate.from_geometry(
                candidate_id=cycle.cycle_id,
                ts_utc=ts_utc,
                symbol=cycle.symbol,
                side=side,
                entry_price=signal.entry_price,
                stop_price=signal.stop_loss_price,
                take_price=signal.take_profit_price,
                regime=regime_stamp.get("regime"),
                regime_vol_class=regime_stamp.get("regime_vol_class"),
                signal_confidence=signal.confidence_score,
                recommended_priority=recommended_priority,
                gate_would_reject=would_reject,
                gate_reason_codes=reason_codes,
                entry_mode=entry_mode_value,
                source="autonomous_loop",
            )
            record_candidate(candidate)
        except Exception as exc:  # noqa: BLE001 — never break the loop
            logger.warning("[LOOP] shadow candidate record failed: %s", exc)

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
                **self._regime_stamp_for_audit(cycle),
            }
            with self._audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.error("[LOOP] Audit write failed: %s", exc)

    @staticmethod
    def _regime_stamp_for_audit(cycle: LoopCycle) -> dict[str, object]:
        """Stamp the cycle audit with the regime that was active when it ran.

        R3-Shadow read-only — the cycle is NOT filtered by regime here, the
        stamp is forensic context for ph5_feature_analysis ``by_regime``
        bucket and any later R4-Active-Filter decision.

        Failure-mode: any exception inside the lookup is swallowed and the
        audit record gets ``regime`` fields with reason="error". The cycle
        itself must complete regardless — this is a forensic side-channel,
        not a gate.
        """
        from app.regime.lookup import (
            DEFAULT_MAX_AGE_SECONDS,
            get_regime_at,
            symbol_to_regime_asset,
        )

        symbol = cycle.symbol or ""
        timestamp = cycle.completed_at or cycle.started_at
        if not timestamp:
            return {
                "regime": None,
                "regime_reason": "no_timestamp",
            }
        asset = symbol_to_regime_asset(symbol)
        is_proxy = asset != symbol.upper().split("/", 1)[0].split("-", 1)[0]
        try:
            result = get_regime_at(
                asset,
                timestamp,
                max_age_seconds=DEFAULT_MAX_AGE_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — never crash audit
            logger.warning("[LOOP] Regime lookup failed: %s", exc)
            return {
                "regime": None,
                "regime_reason": "error",
                "regime_symbol_asset": asset,
                "regime_symbol_is_proxy": is_proxy,
            }
        snap = result.snapshot
        # Surface infrastructure-level regime gaps (file missing, data empty)
        # so a silent pipeline failure shows up in the operator log instead
        # of as an unactionable audit reason. The "stale" / "all_future"
        # reasons are expected near classifier startup and stay info-only.
        if result.reason in {"no_snapshot_file", "no_snapshots_data"}:
            logger.warning(
                "[LOOP] Regime snapshot missing for %s (asset=%s reason=%s) — "
                "classifier never wrote or path drift suspected",
                symbol,
                asset,
                result.reason,
            )
        return {
            "regime": str(snap.regime) if snap is not None else None,
            "regime_vol_class": str(snap.vol_class) if snap is not None else None,
            "regime_confidence": snap.confidence if snap is not None else None,
            "regime_reason": result.reason,
            "regime_age_seconds": result.age_seconds,
            "regime_symbol_asset": asset,
            "regime_symbol_is_proxy": is_proxy,
        }

    def _evaluate_diversification(
        self,
        *,
        symbol: str,
        notional_usd: float | None,
    ) -> DiversificationDecision | None:
        """Shadow/enforce concentration check against the current paper book.

        Default-off: returns None when the guard is disabled (no behaviour
        change). Uses cost-basis exposure from the in-memory portfolio — no
        market calls, no N+1. Any failure is swallowed (forensic side-channel,
        never a gate by accident).
        """
        settings = get_settings().diversification
        if not settings.enabled:
            return None
        try:
            guard = DiversificationGuard(mode=settings.mode)
            portfolio = self._exec.portfolio
            exposures = exposures_from_paper_portfolio(portfolio)
            # Cost-basis equity (cash + position value), consistent with the
            # PortfolioStateRecord equity in _persist. Used as the concentration
            # cap denominator so caps mean "% of total capital", not "% of
            # already-deployed notional" — the latter dead-locks an empty book
            # (every first position projects to 100% on every dimension).
            equity = portfolio.cash + sum(
                p.quantity * p.avg_entry_price for p in portfolio.positions.values()
            )
            return guard.evaluate_candidate(
                exposures,
                candidate_symbol=symbol,
                notional_usd=notional_usd,
                portfolio_equity_usd=equity,
            )
        except Exception as exc:  # noqa: BLE001 — never crash the loop on the guard
            logger.warning("[LOOP] diversification check failed (non-fatal): %s", exc)
            return None

    def _in_post_stop_cooldown(self, symbol: str) -> bool:
        """NEO-V2: True iff `symbol` was stopped out within the cooldown window.

        Reads the paper engine's own audit stream (position_closed reason=stop).
        Disabled when post_stop_cooldown_min <= 0. Fail-open: any read problem
        yields False so a transient I/O hiccup never deadlocks the loop.
        """
        window = get_settings().risk.post_stop_cooldown_min
        if window <= 0:
            return False
        try:
            return is_symbol_in_post_stop_cooldown(
                symbol,
                cooldown_minutes=window,
                audit_path=self._exec.audit_path,
            )
        except Exception as exc:  # noqa: BLE001 — never crash the loop on the gate
            logger.warning("[LOOP] post-stop cooldown check failed (non-fatal): %s", exc)
            return False

    def _evaluate_churn(self, symbol: str) -> ChurnVerdict:
        """Sprint E (Goal §5): churn-killer verdict for a NEW entry on `symbol`.

        Generalises the post-stop cooldown: cooldown after ANY risk-reducing
        close, loss-streak backoff, per-symbol entries/hour and global notional
        turnover/hour. Only the entry path calls this — exits/SL/TP/reductions
        run through monitor_positions/close_position and are never gated here
        (hard invariant). PROBE entry_mode tightens the per-symbol entries/hour
        cap. Fail-open: any read/error problem yields not-blocked so a transient
        I/O hiccup never deadlocks the loop.
        """
        settings = get_settings()
        risk = settings.risk
        per_symbol = risk.churn_max_trades_per_symbol_per_hour
        # PROBE: apply the tighter probe cap when configured (> 0). Other modes
        # keep the normal cap. This is the Sprint A throttle hook landing here.
        if (
            settings.execution.entry_mode is EntryMode.PROBE
            and risk.churn_probe_trades_per_hour > 0
        ):
            per_symbol = risk.churn_probe_trades_per_hour
        config = ChurnKillerConfig(
            cooldown_minutes=risk.churn_cooldown_min,
            loss_streak_threshold=risk.churn_loss_streak_threshold,
            loss_streak_multiplier=risk.churn_loss_streak_multiplier,
            max_trades_per_symbol_per_hour=per_symbol,
            max_notional_turnover_per_hour=risk.churn_max_notional_turnover_per_hour,
        )
        try:
            return evaluate_churn_gate(symbol, config=config, audit_path=self._exec.audit_path)
        except Exception as exc:  # noqa: BLE001 — never crash the loop on the gate
            logger.warning("[LOOP] churn-killer check failed (non-fatal): %s", exc)
            return ChurnVerdict(blocked=False, reason=None, detail="")

    @staticmethod
    def _churn_cycle_status(verdict: ChurnVerdict) -> CycleStatus:
        """Map a churn verdict reason to the cycle status. `post_stop_cooldown`
        reuses the existing COOLDOWN_REJECTED status (operator semantics
        unchanged); rate/turnover limits use the new CHURN_REJECTED status."""
        if verdict.reason == "post_stop_cooldown":
            return CycleStatus.COOLDOWN_REJECTED
        return CycleStatus.CHURN_REJECTED

    @staticmethod
    def _diversification_notes(decision: DiversificationDecision) -> list[str]:
        notes = [
            f"diversification:{decision.action}|mode:{decision.mode}|enforced:{decision.enforced}"
        ]
        if decision.projected_btc_eth_pct is not None:
            notes.append(f"diversification_btc_eth_pct:{decision.projected_btc_eth_pct:.1f}")
        for reason in decision.reasons:
            notes.append(f"diversification_reason:{reason}")
        if decision.alternatives:
            alts = ",".join(a.symbol for a in decision.alternatives)
            notes.append(f"diversification_alternatives:{alts}")
        return notes

    def _evaluate_kyt(
        self,
        *,
        cycle_id: str,
        symbol: str,
        side: str,
        quantity: float | None,
        entry_price: float | None,
        source: str = "",
        correlation_id: str = "",
    ) -> KytAssessment | None:
        """KYT pre-transaction screen (default-off, shadow-first).

        Returns None when KYT is disabled or on any failure — the gate never
        blocks by accident and never crashes the loop. Venue is the execution
        venue (``paper``); symbol + behavioural patterns carry the signal.
        """
        try:
            from app.security.kyt.gate import screen_order

            return screen_order(
                tx_id=cycle_id,
                symbol=symbol,
                venue="paper",
                side=side,
                quantity=quantity,
                entry_price=entry_price,
                source=source,
                correlation_id=correlation_id,
            )
        except Exception as exc:  # noqa: BLE001 — never crash the loop on the gate
            logger.warning("[LOOP] KYT check failed (non-fatal): %s", exc)
            return None

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
        atr_multiplier=risk.atr_multiplier,
        tp_atr_multiplier=risk.tp_atr_multiplier,
        min_notional_usd=risk.min_notional_usd,
        max_position_size_pct=risk.max_position_size_pct,
        round_trip_fee_pct=risk.round_trip_fee_pct,
        min_sl_cost_multiple=risk.min_sl_cost_multiple,
        # Sprint 2026-06-02 reward/risk gates — all default-OFF in Settings.
        min_rr=risk.min_rr,
        min_avg_rr=risk.min_avg_rr,
        max_signal_risk_pct=risk.max_signal_risk_pct,
        max_leveraged_risk_pct=risk.max_leveraged_risk_pct,
        min_net_edge_bps=risk.min_net_edge_bps,
        min_target_distance_pct=risk.min_target_distance_pct,
        gates_mode=risk.gates_mode,
    )


def build_loop_trigger_analysis(
    *,
    symbol: str,
    analysis_profile: str = "conservative",
) -> AnalysisResult:
    """Build a controlled analysis payload for explicit run-once triggers."""
    profile = analysis_profile.strip().lower()
    asset = symbol.split("/")[0].upper()

    # NEO-P-PRIO-20260425-01: AnalysisResult.recommended_priority defaults to
    # None. The D-182 paper-priority-gate (run_cycle, lines ~117-130) hard-
    # rejects None when EXECUTION_PAPER_MIN_PRIORITY > 1. Without explicit
    # values here every cron-triggered cycle since 2026-04-22 silently
    # rejected (218 priority_rejected / zero regular fills). Conservative
    # stays low (=1) so it remains correctly blocked under the strict gate;
    # bullish/bearish probes are at the high-conviction tier (=10) so they
    # actually exercise the downstream paper engine they were meant to test.
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
            recommended_priority=1,
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
            recommended_priority=10,
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
            recommended_priority=10,
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
        getattr(settings, "providers", None),
        "openai_api_key",
        "",
    )
    if openai_key:
        configs.append(
            ValidatorConfig(
                api_key=openai_key,
                model=consensus_model,
                label="openai",
            )
        )

    gemini_key = getattr(
        getattr(settings, "providers", None),
        "gemini_api_key",
        "",
    )
    gemini_model = (
        getattr(
            getattr(settings, "providers", None),
            "gemini_model",
            "",
        )
        or "gemini-2.5-flash"
    )
    if gemini_key:
        configs.append(
            ValidatorConfig(
                api_key=gemini_key,
                model=gemini_model,
                label="gemini",
                base_url=GEMINI_OPENAI_BASE_URL,
                max_tokens=1024,
                timeout=30,
            )
        )

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
    # Phase 2D — Bayes + Adaptive-Learning Wiring.
    # Default-off contract: when both risk.bayes_confidence_enabled and
    # learning.adaptive_learning_enabled are False (the production default),
    # build_bayes_signal_kwargs returns {} and SignalGenerator runs with the
    # exact legacy kwargs it had before this wiring landed. The operator
    # opts in via settings — no silent activation.
    from app.signals.bayes_activation import build_bayes_signal_kwargs

    bayes_kwargs = build_bayes_signal_kwargs(
        settings.risk,
        learning_settings=settings.learning,
    )
    signal_generator = SignalGenerator(
        min_confidence=settings.risk.min_signal_confidence,
        min_confluence=settings.risk.min_signal_confluence_count,
        mode=normalized_mode.value,
        venue="paper",
        **bayes_kwargs,
    )
    consensus_validator = _build_consensus_validator(
        enable_consensus,
        consensus_model,
        settings,
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

    # AUDIT-A2: build_trading_loop rehydrates paper state by reading the loop
    # audit JSONL synchronously; offload so the rehydration read does not block
    # the event loop (proportional to audit-log size). Logic is unchanged.
    loop = await asyncio.to_thread(
        build_trading_loop,
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
    # AUDIT-A2: offload the synchronous build + audit-JSONL rehydration off the
    # event loop so the position-monitor tick cannot wedge FastAPI as the audit
    # log grows (esp. on the Pi's USB-SSD). Logic unchanged.
    loop = await asyncio.to_thread(
        build_trading_loop,
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


def build_priority_gate_summary(
    *,
    audit_path: str | Path = _AUDIT_LOG,
    window_hours: int = 24,
) -> PriorityGateSummary:
    """D-184: summarize D-182 priority-gate activity over a rolling window.

    Reads the trading-loop JSONL, filters by ``started_at`` ISO timestamp,
    and buckets cycles by status. Settings are re-read fresh each call so
    operator rotation of the threshold is visible without restart.
    """
    from datetime import UTC, datetime, timedelta

    from app.core.settings import get_settings

    now = datetime.now(UTC)
    window_start = now - timedelta(hours=max(1, window_hours))
    window_start_iso = window_start.isoformat()

    threshold = get_settings().execution.paper_min_priority
    gate_active = threshold > 1

    records = load_trading_loop_cycles(audit_path)
    total = 0
    priority_rejected = 0
    other_rejected = 0
    completed = 0
    for record in records:
        started_raw = record.get("started_at")
        if not isinstance(started_raw, str) or started_raw < window_start_iso:
            continue
        total += 1
        status = str(record.get("status", "unknown"))
        if status == CycleStatus.PRIORITY_REJECTED.value:
            priority_rejected += 1
        elif status == CycleStatus.COMPLETED.value:
            completed += 1
        elif status.endswith("_rejected") or status in {
            CycleStatus.ORDER_FAILED.value,
            CycleStatus.ERROR.value,
        }:
            other_rejected += 1

    return PriorityGateSummary(
        threshold=threshold,
        gate_active=gate_active,
        window_hours=window_hours,
        total_cycles=total,
        priority_rejected=priority_rejected,
        other_rejected=other_rejected,
        completed=completed,
        window_start_utc=window_start_iso,
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
