"""Read-only paper portfolio projections for CLI/MCP/Telegram surfaces."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.execution.audit_replay import AuditReplayResult, replay_paper_audit
from app.learning.bayes_quarantine import is_corrupt_close
from app.market_data.base import MarketDataSnapshot
from app.market_data.service import get_market_data_snapshot
from app.storage.models.trading import PortfolioStateRecord

logger = logging.getLogger(__name__)

# Global cap for the parallel mark-to-market fan-out. Prevents a stuck
# provider from blocking the caller (e.g. daily briefing) indefinitely.
# Paid CoinGecko tier rarely 429s and recovers in seconds, so 20s is
# enough headroom even with a full retry cycle.
_PORTFOLIO_MARK_TO_MARKET_OVERALL_TIMEOUT_SECONDS = 20.0

# Per-run concurrency cap for outbound market-data requests. Sized for
# paid CoinGecko tier (250 req/min). Free-tier (~30 req/min) callers
# should lower this via env or wrap the call with a tighter semaphore.
_PORTFOLIO_MARK_TO_MARKET_MAX_CONCURRENCY = 10


@dataclass(frozen=True)
class PositionSummary:
    """Read-only position projection with optional mark-to-market metadata.

    Sprint A (2026-05-12): erweitert um position_side, leverage, source,
    opened_at, correlation_id, take_profit_tiers — Operator-Auftrag Sektion 8
    + 10 verlangt diese Felder im Portfolio-View.
    """

    symbol: str
    quantity: float
    avg_entry_price: float
    stop_loss: float | None
    take_profit: float | None
    market_price: float | None
    market_value_usd: float | None
    unrealized_pnl_usd: float | None
    provider: str
    market_data_retrieved_at_utc: str | None
    market_data_source_timestamp_utc: str | None
    market_data_is_stale: bool
    market_data_freshness_seconds: float | None
    market_data_available: bool
    market_data_error: str | None = None
    # Sprint A erweitert — alle optional damit Rückwärtskompatibilität für
    # pre-Sprint-A audit-records gilt.
    position_side: str = "long"
    leverage: float | None = None
    source: str = ""
    opened_at: str | None = None
    correlation_id: str = ""
    realized_pnl_usd: float = 0.0
    # C-Fix 2026-06-13: display-only Mark-to-Market fallback for symbols the
    # price provider does not list (Bybit microcaps: SKYAI, COAI, …). When no
    # LIVE price is available, ``display_value_usd`` carries the entry-cost value
    # (quantity × avg_entry_price) so the Portfolio view shows a number instead
    # of blank. CRITICAL: the gate-relevant fields (market_price,
    # market_value_usd, unrealized_pnl_usd, market_data_available) stay
    # None/False, so position_risk / promotion_gate stay fail-closed and never
    # treat the entry-cost fallback as a real quote. ``mark_basis`` tells the UI
    # which case applies: "live" | "entry_fallback".
    display_value_usd: float | None = None
    mark_basis: str = "live"
    # Multi-target staged-exit ladder. List of (price, qty_share)-Tuples.
    take_profit_tiers: list[tuple[float, float]] = field(default_factory=list)
    initial_quantity: float = 0.0

    def to_json_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_entry_price": self.avg_entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "market_price": self.market_price,
            "market_value_usd": self.market_value_usd,
            "unrealized_pnl_usd": self.unrealized_pnl_usd,
            "provider": self.provider,
            "market_data_retrieved_at": self.market_data_retrieved_at_utc,
            "market_data_source_timestamp": self.market_data_source_timestamp_utc,
            "market_data_is_stale": self.market_data_is_stale,
            "market_data_freshness_seconds": self.market_data_freshness_seconds,
            "market_data_available": self.market_data_available,
            "market_data_error": self.market_data_error,
            "position_side": self.position_side,
            "leverage": self.leverage,
            "source": self.source,
            "opened_at": self.opened_at,
            "correlation_id": self.correlation_id,
            "realized_pnl_usd": self.realized_pnl_usd,
            "display_value_usd": self.display_value_usd,
            "mark_basis": self.mark_basis,
            "take_profit_tiers": [{"price": p, "qty_share": q} for p, q in self.take_profit_tiers],
            "initial_quantity": self.initial_quantity,
        }


@dataclass(frozen=True)
class ExposureSummary:
    """Read-only exposure projection derived from position mark-to-market values."""

    priced_position_count: int
    stale_position_count: int
    unavailable_price_count: int
    gross_exposure_usd: float
    net_exposure_usd: float
    largest_position_symbol: str | None
    largest_position_weight_pct: float | None
    mark_to_market_status: str
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "paper_exposure_summary",
            "priced_position_count": self.priced_position_count,
            "stale_position_count": self.stale_position_count,
            "unavailable_price_count": self.unavailable_price_count,
            "gross_exposure_usd": self.gross_exposure_usd,
            "net_exposure_usd": self.net_exposure_usd,
            "largest_position_symbol": self.largest_position_symbol,
            "largest_position_weight_pct": self.largest_position_weight_pct,
            "mark_to_market_status": self.mark_to_market_status,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Canonical read-only paper portfolio snapshot."""

    generated_at_utc: str
    source: str
    audit_path: str
    cash_usd: float
    realized_pnl_usd: float
    total_market_value_usd: float
    total_equity_usd: float
    position_count: int
    positions: tuple[PositionSummary, ...]
    exposure_summary: ExposureSummary
    available: bool
    error: str | None = None
    execution_enabled: bool = False
    write_back_allowed: bool = False
    # 2026-06-25: explizite Echtzeit-Wahrheit für das Portfolio-Panel.
    # total_unrealized_pnl_usd = Σ vorzeichen-korrektes unrealized (long: price-entry,
    # short: entry-price). total_fees_usd = kumulative Paper-Fees (Entry+Exit).
    # total_equity_usd ist jetzt short-aware (cash + long_mv - short_mv).
    total_unrealized_pnl_usd: float = 0.0
    total_fees_usd: float = 0.0
    # 2026-06-25: Mai-60-bps-error-path-Artefakt separat ausgewiesen (audit_replay).
    total_fees_artifact_usd: float = 0.0

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "paper_portfolio_snapshot",
            "generated_at": self.generated_at_utc,
            "source": self.source,
            "audit_path": self.audit_path,
            "cash_usd": self.cash_usd,
            "realized_pnl_usd": self.realized_pnl_usd,
            "total_market_value_usd": self.total_market_value_usd,
            "total_equity_usd": self.total_equity_usd,
            "total_unrealized_pnl_usd": self.total_unrealized_pnl_usd,
            "total_fees_usd": self.total_fees_usd,
            "total_fees_artifact_usd": self.total_fees_artifact_usd,
            "position_count": self.position_count,
            "positions": [position.to_json_dict() for position in self.positions],
            "exposure_summary": self.exposure_summary.to_json_dict(),
            "available": self.available,
            "error": self.error,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


_AuditReplayResult = AuditReplayResult  # backwards-compat alias for internal callers


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _signed_market_value(position: PositionSummary) -> float:
    """Equity contribution of a position's market value: + for long, - for short.

    ``market_value_usd`` is stored gross-positive per position; a short position is a
    liability and contributes NEGATIVELY to equity / net exposure. Mirrors the engine
    SSOT ``PaperPortfolio.total_equity`` (app/execution/models.py:176-184). Without this
    the read path double-counted shorts (cash already holds the sale proceeds AND the
    position was added positively) → the 2026-06 equity-swing bug (20k→60k→20k).
    """
    mv = position.market_value_usd or 0.0
    return -mv if position.position_side == "short" else mv


def _replay_paper_audit(audit_path: Path) -> AuditReplayResult:
    return replay_paper_audit(audit_path)


def _build_exposure_summary(positions: tuple[PositionSummary, ...]) -> ExposureSummary:
    priced_positions = [position for position in positions if position.market_value_usd is not None]
    stale_count = sum(1 for position in positions if position.market_data_is_stale)
    unavailable_count = sum(1 for position in positions if not position.market_data_available)
    gross_exposure = sum(abs(position.market_value_usd or 0.0) for position in priced_positions)
    # 2026-06-25 short-aware: net exposure = long market value MINUS short market
    # value. market_value_usd is stored gross-positive per position, so the sign is
    # applied here from position_side. Fixes the "always long" directional bias.
    net_exposure = sum(_signed_market_value(position) for position in priced_positions)

    largest_symbol: str | None = None
    largest_weight: float | None = None
    if gross_exposure > 0.0 and priced_positions:
        largest = max(priced_positions, key=lambda position: abs(position.market_value_usd or 0.0))
        largest_symbol = largest.symbol
        largest_weight = round((abs(largest.market_value_usd or 0.0) / gross_exposure) * 100, 4)

    status = "ok" if stale_count == 0 and unavailable_count == 0 else "degraded"
    return ExposureSummary(
        priced_position_count=len(priced_positions),
        stale_position_count=stale_count,
        unavailable_price_count=unavailable_count,
        gross_exposure_usd=round(gross_exposure, 8),
        net_exposure_usd=round(net_exposure, 8),
        largest_position_symbol=largest_symbol,
        largest_position_weight_pct=largest_weight,
        mark_to_market_status=status,
    )


async def _query_db_latest_portfolio_state(
    session: AsyncSession,
) -> PortfolioStateRecord | None:
    """Query the most recent PortfolioStateRecord. Returns None on error or empty table."""
    try:
        result = await session.execute(
            select(PortfolioStateRecord).order_by(PortfolioStateRecord.created_at.desc()).limit(1)
        )
        return result.scalars().first()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTFOLIO] DB state query failed, falling back to JSONL: %s", exc)
        return None


def _build_snapshot_from_portfolio_state(
    state: PortfolioStateRecord,
    generated_at: str,
) -> PortfolioSnapshot:
    """Build a PortfolioSnapshot from a PortfolioStateRecord (DB-primary path).

    The positions_json field stores the full portfolio.to_dict() snapshot written
    at fill-simulation time, enabling complete position reconstruction without JSONL.
    """
    positions_data: dict[str, object] = {}
    cash_usd = 0.0
    realized_pnl_usd = 0.0

    raw = state.positions_json or {}
    if isinstance(raw, dict):
        positions_data = raw.get("positions", {})  # type: ignore[assignment]
        cash_usd = float(raw.get("cash", 0.0))  # type: ignore[arg-type]

    position_summaries: list[PositionSummary] = []
    for sym, pos_raw in positions_data.items():
        if not isinstance(pos_raw, dict):
            continue
        raw_tiers = pos_raw.get("take_profit_tiers") or []
        tiers: list[tuple[float, float]] = []
        if isinstance(raw_tiers, list):
            for t in raw_tiers:
                if isinstance(t, dict):
                    p = _coerce_float(t.get("price"))
                    q = _coerce_float(t.get("qty_share"))
                    if p is not None and q is not None:
                        tiers.append((p, q))
                elif isinstance(t, (list, tuple)) and len(t) == 2:
                    p = _coerce_float(t[0])
                    q = _coerce_float(t[1])
                    if p is not None and q is not None:
                        tiers.append((p, q))
        position_summaries.append(
            PositionSummary(
                symbol=sym,
                quantity=float(pos_raw.get("quantity", 0.0)),
                avg_entry_price=float(pos_raw.get("avg_entry_price", 0.0)),
                stop_loss=pos_raw.get("stop_loss"),
                take_profit=pos_raw.get("take_profit"),
                market_price=None,
                market_value_usd=None,
                unrealized_pnl_usd=None,
                provider="db_snapshot",
                market_data_retrieved_at_utc=None,
                market_data_source_timestamp_utc=None,
                market_data_is_stale=True,
                market_data_freshness_seconds=None,
                market_data_available=False,
                market_data_error="market_price_not_available_from_db_snapshot",
                position_side=str(pos_raw.get("position_side") or "long"),
                leverage=_coerce_float(pos_raw.get("leverage")),
                source=str(pos_raw.get("source") or ""),
                opened_at=_coerce_str(pos_raw.get("opened_at")),
                correlation_id=str(pos_raw.get("correlation_id") or ""),
                realized_pnl_usd=float(pos_raw.get("realized_pnl_usd") or 0.0),
                # DB-snapshot path never has a live price → entry-cost fallback.
                display_value_usd=round(
                    float(pos_raw.get("quantity", 0.0))
                    * float(pos_raw.get("avg_entry_price", 0.0)),
                    8,
                ),
                mark_basis="entry_fallback",
                take_profit_tiers=tiers,
                initial_quantity=float(pos_raw.get("initial_quantity") or 0.0),
            )
        )

    exposure = _build_exposure_summary(tuple(position_summaries))
    # Gross entry-basis value (display) vs. short-aware NET for equity. No live
    # price in the DB path → entry basis; short positions still subtract.
    total_market_value = sum(p.quantity * p.avg_entry_price for p in position_summaries)
    net_entry_value = sum(
        (-1.0 if p.position_side == "short" else 1.0) * p.quantity * p.avg_entry_price
        for p in position_summaries
    )
    total_fees_db = float(raw.get("total_fees_usd") or 0.0) if isinstance(raw, dict) else 0.0

    return PortfolioSnapshot(
        generated_at_utc=generated_at,
        source="db_portfolio_state",
        audit_path=f"db_portfolio_states:cycle_id={state.cycle_id}",
        cash_usd=cash_usd,
        realized_pnl_usd=realized_pnl_usd,
        total_market_value_usd=round(total_market_value, 8),
        total_equity_usd=round(cash_usd + net_entry_value, 8),
        position_count=len(position_summaries),
        positions=tuple(position_summaries),
        exposure_summary=exposure,
        available=True,
        error=None,
        execution_enabled=False,
        write_back_allowed=False,
        # DB path has no live price → unrealized not computable here (stays 0).
        total_unrealized_pnl_usd=0.0,
        total_fees_usd=round(total_fees_db, 8),
        total_fees_artifact_usd=0.0,
    )


async def _gather_market_snapshots(
    *,
    symbols: list[str],
    provider: str,
    freshness_threshold_seconds: float,
    timeout_seconds: int,
) -> dict[str, MarketDataSnapshot]:
    """Fetch mark-to-market snapshots for every symbol in parallel.

    Ensures a failing/throttled provider cannot serialize the whole snapshot —
    a single request may back off, but sibling symbols proceed concurrently.
    An overall timeout guarantees the caller (CLI, briefing, cron) can never
    block indefinitely; timed-out symbols degrade to `available=False` with a
    clear error tag, matching the semantics the adapter itself emits on
    failure.
    """
    if not symbols:
        return {}

    semaphore = asyncio.Semaphore(_PORTFOLIO_MARK_TO_MARKET_MAX_CONCURRENCY)

    async def _fetch(symbol: str) -> tuple[str, MarketDataSnapshot]:
        async with semaphore:
            snapshot = await get_market_data_snapshot(
                symbol=symbol,
                provider=provider,
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
            )
        return symbol, snapshot

    tasks = [asyncio.create_task(_fetch(symbol)) for symbol in symbols]
    results: dict[str, MarketDataSnapshot] = {}
    try:
        finished = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=_PORTFOLIO_MARK_TO_MARKET_OVERALL_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "[PORTFOLIO] parallel mark-to-market fan-out exceeded %.1fs timeout; "
            "degrading remaining symbols",
            _PORTFOLIO_MARK_TO_MARKET_OVERALL_TIMEOUT_SECONDS,
        )
        for task in tasks:
            task.cancel()
        finished = await asyncio.gather(*tasks, return_exceptions=True)

    finished_by_symbol: dict[str, MarketDataSnapshot] = {}
    for idx, outcome in enumerate(finished):
        symbol = symbols[idx]
        if isinstance(outcome, tuple):
            fetched_symbol, snapshot = outcome
            finished_by_symbol[fetched_symbol] = snapshot
        elif isinstance(outcome, asyncio.CancelledError):
            # Cancelled as a direct consequence of the overall-timeout path —
            # label it accordingly so downstream can distinguish "we timed out"
            # from "the adapter raised".
            finished_by_symbol[symbol] = MarketDataSnapshot(
                symbol=symbol,
                provider=provider,
                retrieved_at_utc=datetime.now(UTC).isoformat(),
                source_timestamp_utc=None,
                price=None,
                is_stale=True,
                freshness_seconds=None,
                available=False,
                error="snapshot_gather_timeout",
            )
        elif isinstance(outcome, BaseException):
            finished_by_symbol[symbol] = MarketDataSnapshot(
                symbol=symbol,
                provider=provider,
                retrieved_at_utc=datetime.now(UTC).isoformat(),
                source_timestamp_utc=None,
                price=None,
                is_stale=True,
                freshness_seconds=None,
                available=False,
                error=f"snapshot_gather_failed:{outcome.__class__.__name__}",
            )

    for symbol in symbols:
        results[symbol] = finished_by_symbol.get(
            symbol,
            MarketDataSnapshot(
                symbol=symbol,
                provider=provider,
                retrieved_at_utc=datetime.now(UTC).isoformat(),
                source_timestamp_utc=None,
                price=None,
                is_stale=True,
                freshness_seconds=None,
                available=False,
                error="snapshot_gather_timeout",
            ),
        )
    return results


async def build_portfolio_snapshot(
    *,
    audit_path: str | Path = "artifacts/paper_execution_audit.jsonl",
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> PortfolioSnapshot:
    """Build canonical read-only portfolio snapshot from paper execution audit + market data.

    If session_factory is provided and a PortfolioStateRecord exists in the DB, the snapshot
    is sourced from the DB (DB-primary path). Otherwise falls back to JSONL replay.

    The DB path reconstructs full position state from the most recent PortfolioStateRecord
    written by TradingLoop._write_db() after fill_simulated cycles. Market prices are not
    available from the DB snapshot (no live price fetching on read).
    """
    generated_at = datetime.now(UTC).isoformat()

    # DB-primary: open a scoped session, query latest portfolio state
    if session_factory is not None:
        try:
            async with session_factory() as session:
                state = await _query_db_latest_portfolio_state(session)
            if state is not None:
                return _build_snapshot_from_portfolio_state(state, generated_at)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PORTFOLIO] DB-primary path failed, falling back to JSONL: %s", exc)
        # DB empty or query failed → fall through to JSONL

    resolved_path = Path(audit_path).resolve()
    replay = _replay_paper_audit(resolved_path)

    if not replay.available:
        empty_exposure = _build_exposure_summary(())
        return PortfolioSnapshot(
            generated_at_utc=generated_at,
            source="paper_execution_audit_replay",
            audit_path=str(resolved_path),
            cash_usd=0.0,
            realized_pnl_usd=0.0,
            total_market_value_usd=0.0,
            total_equity_usd=0.0,
            position_count=0,
            positions=(),
            exposure_summary=empty_exposure,
            available=False,
            error=replay.error,
        )

    sorted_symbols = sorted(replay.positions)
    snapshots_by_symbol = await _gather_market_snapshots(
        symbols=sorted_symbols,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )

    position_summaries: list[PositionSummary] = []
    for symbol in sorted_symbols:
        position = replay.positions[symbol]
        market_snapshot = snapshots_by_symbol[symbol]

        # Fail closed: stale snapshots are treated as not usable for mark-to-market.
        can_use_market_price = (
            market_snapshot.available
            and not market_snapshot.is_stale
            and market_snapshot.price is not None
        )
        market_price = market_snapshot.price if can_use_market_price else None
        market_value = (
            round(position.quantity * market_price, 8) if market_price is not None else None
        )
        # 2026-06-25 short-aware unrealized PnL (mirrors PaperPosition.unrealized_pnl,
        # app/execution/models.py:136-139): long profits when price rises, short when
        # price falls. Previously always (price-entry) → shorts in profit were shown
        # as a loss (red) and the equity swing bug double-counted them.
        if market_price is None:
            unrealized_pnl = None
        elif position.position_side == "short":
            unrealized_pnl = round((position.avg_entry_price - market_price) * position.quantity, 8)
        else:
            unrealized_pnl = round((market_price - position.avg_entry_price) * position.quantity, 8)

        # C-Fix 2026-06-13: display-only fallback for unlistable symbols. The
        # gate-relevant fields above stay None when there is no live price; this
        # only fills a separate display value (entry cost) so the Portfolio view
        # is not blank. mark_basis lets the UI flag it as non-live.
        if market_value is not None:
            display_value = market_value
            mark_basis = "live"
        else:
            display_value = round(position.quantity * position.avg_entry_price, 8)
            mark_basis = "entry_fallback"

        position_summaries.append(
            PositionSummary(
                symbol=position.symbol,
                quantity=position.quantity,
                avg_entry_price=position.avg_entry_price,
                stop_loss=position.stop_loss,
                take_profit=position.take_profit,
                market_price=market_price,
                market_value_usd=market_value,
                unrealized_pnl_usd=unrealized_pnl,
                provider=market_snapshot.provider,
                market_data_retrieved_at_utc=market_snapshot.retrieved_at_utc,
                market_data_source_timestamp_utc=market_snapshot.source_timestamp_utc,
                market_data_is_stale=market_snapshot.is_stale,
                market_data_freshness_seconds=market_snapshot.freshness_seconds,
                market_data_available=can_use_market_price,
                market_data_error=(
                    market_snapshot.error or ("stale_data" if market_snapshot.is_stale else None)
                ),
                position_side=position.position_side,
                leverage=position.leverage,
                source=position.source,
                opened_at=position.opened_at,
                correlation_id=position.correlation_id,
                realized_pnl_usd=position.realized_pnl_usd,
                display_value_usd=display_value,
                mark_basis=mark_basis,
                take_profit_tiers=list(position.take_profit_tiers),
                initial_quantity=position.initial_quantity,
            )
        )

    positions_tuple = tuple(position_summaries)
    exposure_summary = _build_exposure_summary(positions_tuple)
    # total_market_value_usd stays GROSS (Σ |market value|) for the "in Position"
    # display + daily-briefing. Equity uses the short-aware SIGNED sum so a short is
    # a liability, not a positive asset (the 2026-06 equity-swing fix). For an
    # all-long book gross == net, so Equity = Cash + In-Position still holds.
    total_market_value = round(
        sum(position.market_value_usd or 0.0 for position in positions_tuple),
        8,
    )
    net_market_value = round(
        sum(_signed_market_value(position) for position in positions_tuple),
        8,
    )
    total_unrealized = round(
        sum(position.unrealized_pnl_usd or 0.0 for position in positions_tuple),
        8,
    )
    total_equity = round(replay.cash_usd + net_market_value, 8)
    has_unpriced_positions = bool(positions_tuple) and exposure_summary.priced_position_count == 0

    return PortfolioSnapshot(
        generated_at_utc=generated_at,
        source="paper_execution_audit_replay",
        audit_path=str(resolved_path),
        cash_usd=round(replay.cash_usd, 8),
        realized_pnl_usd=round(replay.realized_pnl_usd, 8),
        total_market_value_usd=total_market_value,
        total_equity_usd=total_equity,
        position_count=len(positions_tuple),
        positions=positions_tuple,
        exposure_summary=exposure_summary,
        available=not has_unpriced_positions,
        error=("market_data_unavailable_for_open_positions" if has_unpriced_positions else None),
        total_unrealized_pnl_usd=total_unrealized,
        total_fees_usd=round(replay.total_fees_usd, 8),
        total_fees_artifact_usd=round(replay.total_fees_artifact_usd, 8),
    )


def build_positions_summary(snapshot: PortfolioSnapshot) -> dict[str, object]:
    """Return the positions-only read projection from a canonical portfolio snapshot."""
    return {
        "report_type": "paper_positions_summary",
        "generated_at": snapshot.generated_at_utc,
        "audit_path": snapshot.audit_path,
        "position_count": snapshot.position_count,
        "positions": [position.to_json_dict() for position in snapshot.positions],
        "mark_to_market_status": snapshot.exposure_summary.mark_to_market_status,
        "available": snapshot.available,
        "error": snapshot.error,
        "execution_enabled": False,
        "write_back_allowed": False,
    }


def build_exposure_summary(snapshot: PortfolioSnapshot) -> dict[str, object]:
    """Return the exposure-only read projection from a canonical portfolio snapshot."""
    payload = snapshot.exposure_summary.to_json_dict()
    payload.update(
        {
            "generated_at": snapshot.generated_at_utc,
            "audit_path": snapshot.audit_path,
            "available": snapshot.available,
            "error": snapshot.error,
        }
    )
    return payload


# 2026-05-25 Forensik-Antwort auf "Vor Live-Mode keine sinnvolle Visualisierung":
# realized-by-asset ist OHNE Live-Mode aus paper_execution_audit.jsonl ableitbar.
# Datenquelle: position_closed + position_partial_closed Events tragen per-trade
# trade_pnl_usd (schema_version=v2 NEO-P-101-r2). Diese Funktion ist die
# kanonische, replay-unabhängige Aggregation. Sie ignoriert den portfolio-kumu-
# lativen Snapshot-Field realized_pnl_usd (siehe Memory paper_audit_pnl_field_
# semantics) und summiert ausschließlich per-trade trade_pnl_usd.
def compute_realized_by_asset(
    audit_path: Path,
    *,
    source_prefix: str | None = None,
    source_filter: str | None = None,
) -> dict[str, object]:
    """Aggregate realized PnL per asset from paper_execution_audit.jsonl.

    RC-3 (2026-06-04): ``source_prefix`` and ``source_filter`` restrict the aggregation.
    Supports 5 distinct portfolio views: Gesamt, Premium Telegram, Autonomous Loop,
    Reconciled completions, and Demo/Legacy/Unknown.

    Returns a dict shaped as::
    ...
    """
    result: dict[str, object] = {
        "as_of_utc": datetime.now(UTC).isoformat(),
        "audit_path": str(audit_path),
        "source_prefix": source_prefix,
        "source_filter": source_filter,
        "audit_file_exists": False,
        "audit_last_event_utc": None,
        "by_asset": [],
        "totals": {
            "realized_pnl_usd": 0.0,
            "closed_trades": 0,
            "assets_count": 0,
            "fees_usd_total": 0.0,
            "partial_close_events": 0,
            "full_close_events": 0,
        },
        "top_performer": None,
        "worst_performer": None,
        # 2026-06-25: chronologische Liste der jüngsten Einzel-Trades (Operator-
        # Wunsch "Übersicht der letzten Trades"). Vorzeichen-korrekt (trade_pnl_usd
        # aus dem Engine-Close-Event), quarantänierte Closes ausgeschlossen.
        "recent_trades": [],
        "available": False,
        "error": None,
        "invalid_lines": [],
    }

    if not audit_path.exists():
        result["error"] = "audit_file_missing"
        return result

    result["audit_file_exists"] = True

    per_asset: dict[str, dict[str, float | int | str | None]] = {}
    invalid: list[tuple[int, str]] = []
    last_event_ts: str | None = None
    full_close_total = 0
    partial_close_total = 0
    recent_trades: list[dict[str, object]] = []

    try:
        raw = audit_path.read_text(encoding="utf-8")
    except OSError as e:
        result["error"] = f"audit_read_error:{e}"
        return result

    parsed_records: list[tuple[int, dict[str, object]]] = []
    source_by_order: dict[str, str] = {}
    source_by_correlation: dict[str, str] = {}
    for line_no, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as e:
            invalid.append((line_no, f"json_decode_error:{e.msg}"))
            continue
        if not isinstance(d, dict):
            invalid.append((line_no, "non_object_payload"))
            continue
        parsed_records.append((line_no, d))
        source_raw = d.get("signal_source") or d.get("source")
        source = source_raw.strip() if isinstance(source_raw, str) and source_raw.strip() else None
        if source is not None:
            order_id = d.get("order_id")
            if isinstance(order_id, str) and order_id:
                source_by_order.setdefault(order_id, source)
            correlation_id = d.get("correlation_id")
            if isinstance(correlation_id, str) and correlation_id:
                source_by_correlation.setdefault(correlation_id, source)

    for line_no, d in parsed_records:
        ts = d.get("timestamp_utc") or d.get("created_at") or d.get("filled_at")
        if isinstance(ts, str) and ts:
            if last_event_ts is None or ts > last_event_ts:
                last_event_ts = ts
        ev = d.get("event_type")
        if ev not in {"position_closed", "position_partial_closed"}:
            continue
        # Resolve source attribution and close reason
        sig_source = (
            d.get("signal_source")
            or d.get("source")
            or source_by_order.get(str(d.get("order_id") or ""))
            or source_by_correlation.get(str(d.get("correlation_id") or ""))
        )
        sig_source_str = (
            sig_source.strip() if isinstance(sig_source, str) and sig_source.strip() else ""
        )
        sig_source_lower = sig_source_str.lower()

        reason = str(d.get("reason") or d.get("close_reason") or "").strip().lower()
        is_reconciled = "reconcile" in reason or "touch_price" in reason

        # Apply source_prefix (legacy / backward compatibility)
        if source_prefix is not None:
            if not sig_source_str:
                continue
            if not sig_source_lower.startswith(source_prefix.strip().lower()):
                continue

        # Apply source_filter (five distinct portfolio views)
        if source_filter is not None:
            sf = source_filter.strip().lower()
            if sf in {"gesamt", "all", "*"}:
                pass
            elif sf in {"telegram_premium", "premium_telegram", "telegram-premium"}:
                if not sig_source_lower.startswith("telegram_premium"):
                    continue
            elif sf in {"autonomous", "autonomous_loop"}:
                if (
                    sig_source_lower.startswith("telegram_premium")
                    or not sig_source_str
                    or is_reconciled
                ):
                    continue
            elif sf == "reconciled":
                if not is_reconciled:
                    continue
            elif sf in {"legacy_unknown", "legacy", "unknown"}:
                if sig_source_str or is_reconciled:
                    continue
        sym = d.get("symbol")
        if not isinstance(sym, str) or not sym.strip():
            invalid.append((line_no, "close_event_missing_symbol"))
            continue
        sym = sym.strip()
        # per-trade PnL — never realized_pnl_usd (which is portfolio-cumulative).
        pnl_raw = d.get("trade_pnl_usd")
        if pnl_raw is None:
            # legacy v1: reconstruct from entry/exit/quantity if available
            ep = d.get("entry_price")
            xp = d.get("exit_price")
            qty = d.get("quantity")
            if all(isinstance(v, (int, float)) for v in (ep, xp, qty)):
                pnl = (float(xp) - float(ep)) * float(qty)
            else:
                invalid.append((line_no, "close_event_missing_trade_pnl_usd_and_v1_fields"))
                continue
        else:
            try:
                pnl = float(pnl_raw)
            except (TypeError, ValueError):
                invalid.append((line_no, "close_event_pnl_not_numeric"))
                continue
        fee = 0.0
        fee_raw = d.get("fee_usd")
        if isinstance(fee_raw, (int, float)):
            fee = float(fee_raw)
        bucket = per_asset.setdefault(
            sym,
            {
                "symbol": sym,
                "realized_pnl_usd": 0.0,
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "fees_usd_total": 0.0,
                "partial_closes": 0,
                "full_closes": 0,
                "last_close_utc": None,
                "quarantined_pnl_usd": 0.0,
                "quarantined_closes": 0,
            },
        )
        # Exclude corrupt closes from realized PnL so the dashboard shows the real
        # number; surface the excluded amount for transparency. Unified verdict
        # (bayes_quarantine.is_corrupt_close) = exact forensic signatures (DS-
        # 20260529-V1 MATIC stale-exit, DS-20260601 ETH off-market) OVER the generic
        # phantom-return guard. Closes the 2026-06-23 leak where this path used only
        # the generic guard and let the ETH off-market signature (+55%, under the
        # 200% cap) leak into realized PnL.
        if is_corrupt_close(d):
            bucket["quarantined_pnl_usd"] = float(bucket["quarantined_pnl_usd"]) + pnl  # type: ignore[arg-type]
            bucket["quarantined_closes"] = int(bucket["quarantined_closes"]) + 1  # type: ignore[arg-type]
            if isinstance(ts, str) and ts:
                prev_last = bucket["last_close_utc"]
                if prev_last is None or ts > str(prev_last):
                    bucket["last_close_utc"] = ts
            continue
        bucket["realized_pnl_usd"] = float(bucket["realized_pnl_usd"]) + pnl  # type: ignore[arg-type]
        bucket["closed_trades"] = int(bucket["closed_trades"]) + 1  # type: ignore[arg-type]
        if pnl > 0:
            bucket["wins"] = int(bucket["wins"]) + 1  # type: ignore[arg-type]
        elif pnl < 0:
            bucket["losses"] = int(bucket["losses"]) + 1  # type: ignore[arg-type]
        bucket["fees_usd_total"] = float(bucket["fees_usd_total"]) + fee  # type: ignore[arg-type]
        if ev == "position_partial_closed":
            bucket["partial_closes"] = int(bucket["partial_closes"]) + 1  # type: ignore[arg-type]
            partial_close_total += 1
        else:
            bucket["full_closes"] = int(bucket["full_closes"]) + 1  # type: ignore[arg-type]
            full_close_total += 1
        prev_last = bucket["last_close_utc"]
        if isinstance(ts, str) and ts and (prev_last is None or ts > str(prev_last)):
            bucket["last_close_utc"] = ts

        # 2026-06-25: Einzel-Trade für die "letzte Trades"-Liste. trade_pnl_usd (pnl)
        # ist bereits vorzeichen-korrekt aus dem Engine-Close-Event; fee separat.
        entry_p = d.get("entry_price")
        exit_p = d.get("exit_price") or d.get("fill_price")
        recent_trades.append(
            {
                "symbol": sym,
                "position_side": str(d.get("position_side") or "long"),
                "trade_pnl_usd": round(pnl, 4),
                "fee_usd": round(fee, 4),
                "entry_price": float(entry_p) if isinstance(entry_p, (int, float)) else None,
                "exit_price": float(exit_p) if isinstance(exit_p, (int, float)) else None,
                "closed_at_utc": ts if isinstance(ts, str) else None,
                "source": sig_source_str or None,
                "is_partial": ev == "position_partial_closed",
                "win": pnl > 0,
            }
        )

    by_asset: list[dict[str, object]] = []
    total_pnl = 0.0
    total_trades = 0
    total_fees = 0.0
    total_quarantined_pnl = 0.0
    total_quarantined_closes = 0
    for _sym, bucket in per_asset.items():
        n = int(bucket["closed_trades"])  # type: ignore[arg-type]
        w = int(bucket["wins"])  # type: ignore[arg-type]
        win_rate = round((w / n * 100.0), 2) if n > 0 else None
        bucket["realized_pnl_usd"] = round(float(bucket["realized_pnl_usd"]), 4)  # type: ignore[arg-type]
        bucket["fees_usd_total"] = round(float(bucket["fees_usd_total"]), 4)  # type: ignore[arg-type]
        bucket["quarantined_pnl_usd"] = round(float(bucket["quarantined_pnl_usd"]), 4)  # type: ignore[arg-type]
        bucket["win_rate_pct"] = win_rate
        by_asset.append(dict(bucket))
        total_pnl += float(bucket["realized_pnl_usd"])
        total_trades += n
        total_fees += float(bucket["fees_usd_total"])
        total_quarantined_pnl += float(bucket["quarantined_pnl_usd"])
        total_quarantined_closes += int(bucket["quarantined_closes"])  # type: ignore[arg-type]

    by_asset.sort(key=lambda b: float(b["realized_pnl_usd"]), reverse=True)

    result["by_asset"] = by_asset
    result["totals"] = {
        "realized_pnl_usd": round(total_pnl, 4),
        "closed_trades": total_trades,
        "assets_count": len(by_asset),
        "fees_usd_total": round(total_fees, 4),
        "partial_close_events": partial_close_total,
        "full_close_events": full_close_total,
        # DS-20260529-V1: phantom closes excluded from realized_pnl_usd above.
        "quarantined_pnl_usd": round(total_quarantined_pnl, 4),
        "quarantined_closes": total_quarantined_closes,
    }
    result["top_performer"] = by_asset[0] if by_asset else None
    result["worst_performer"] = by_asset[-1] if by_asset else None
    # Letzte Trades chronologisch absteigend, auf 20 begrenzt (Übersicht, kein Audit).
    recent_trades.sort(key=lambda r: str(r.get("closed_at_utc") or ""), reverse=True)
    result["recent_trades"] = recent_trades[:20]
    result["audit_last_event_utc"] = last_event_ts
    result["available"] = True
    result["invalid_lines"] = invalid
    return result


__all__ = [
    "PositionSummary",
    "PortfolioSnapshot",
    "build_portfolio_snapshot",
    "build_exposure_summary",
    "compute_realized_by_asset",
]
