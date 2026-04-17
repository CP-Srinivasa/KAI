"""Read-only paper portfolio projections for CLI/MCP/Telegram surfaces."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.execution.audit_replay import AuditReplayResult, replay_paper_audit
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
    """Read-only position projection with optional mark-to-market metadata."""

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


def _replay_paper_audit(audit_path: Path) -> AuditReplayResult:
    return replay_paper_audit(audit_path)


def _build_exposure_summary(positions: tuple[PositionSummary, ...]) -> ExposureSummary:
    priced_positions = [position for position in positions if position.market_value_usd is not None]
    stale_count = sum(1 for position in positions if position.market_data_is_stale)
    unavailable_count = sum(1 for position in positions if not position.market_data_available)
    gross_exposure = sum(abs(position.market_value_usd or 0.0) for position in priced_positions)
    net_exposure = sum(position.market_value_usd or 0.0 for position in priced_positions)

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
            )
        )

    exposure = _build_exposure_summary(tuple(position_summaries))
    total_market_value = sum(p.quantity * p.avg_entry_price for p in position_summaries)

    return PortfolioSnapshot(
        generated_at_utc=generated_at,
        source="db_portfolio_state",
        audit_path=f"db_portfolio_states:cycle_id={state.cycle_id}",
        cash_usd=cash_usd,
        realized_pnl_usd=realized_pnl_usd,
        total_market_value_usd=round(total_market_value, 8),
        total_equity_usd=round(cash_usd + total_market_value, 8),
        position_count=len(position_summaries),
        positions=tuple(position_summaries),
        exposure_summary=exposure,
        available=True,
        error=None,
        execution_enabled=False,
        write_back_allowed=False,
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
        unrealized_pnl = (
            round((market_price - position.avg_entry_price) * position.quantity, 8)
            if market_price is not None
            else None
        )

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
            )
        )

    positions_tuple = tuple(position_summaries)
    exposure_summary = _build_exposure_summary(positions_tuple)
    total_market_value = round(
        sum(position.market_value_usd or 0.0 for position in positions_tuple),
        8,
    )
    total_equity = round(replay.cash_usd + total_market_value, 8)
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
