"""Read-only paper portfolio projections for CLI/MCP/Telegram surfaces."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import PaperPosition
from app.market_data.service import get_market_data_snapshot
from app.storage.models.trading import TradingCycleRecord

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class _AuditReplayResult:
    positions: dict[str, PaperPosition]
    cash_usd: float
    realized_pnl_usd: float
    available: bool
    error: str | None = None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _replay_paper_audit(audit_path: Path) -> _AuditReplayResult:
    if not audit_path.exists():
        return _AuditReplayResult(
            positions={},
            cash_usd=0.0,
            realized_pnl_usd=0.0,
            available=True,
            error=None,
        )

    positions: dict[str, PaperPosition] = {}
    order_meta: dict[str, tuple[float | None, float | None]] = {}
    cash_usd = 0.0
    realized_pnl_usd = 0.0

    for line_number, raw_line in enumerate(
        audit_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return _AuditReplayResult(
                positions={},
                cash_usd=0.0,
                realized_pnl_usd=0.0,
                available=False,
                error=f"audit_json_decode_error_line_{line_number}",
            )

        if not isinstance(payload, dict):
            return _AuditReplayResult(
                positions={},
                cash_usd=0.0,
                realized_pnl_usd=0.0,
                available=False,
                error=f"audit_payload_type_error_line_{line_number}",
            )

        event_type = _coerce_str(payload.get("event_type"))
        if event_type == "order_created":
            order_id = _coerce_str(payload.get("order_id"))
            if order_id is not None:
                order_meta[order_id] = (
                    _coerce_float(payload.get("stop_loss")),
                    _coerce_float(payload.get("take_profit")),
                )
            continue

        if event_type != "order_filled":
            continue

        symbol = _coerce_str(payload.get("symbol"))
        side = _coerce_str(payload.get("side"))
        quantity = _coerce_float(payload.get("quantity"))
        fill_price = _coerce_float(payload.get("fill_price"))
        order_id = _coerce_str(payload.get("order_id"))
        filled_at = _coerce_str(payload.get("filled_at")) or datetime.now(UTC).isoformat()

        if (
            symbol is None
            or side not in {"buy", "sell"}
            or quantity is None
            or fill_price is None
            or quantity <= 0.0
            or fill_price <= 0.0
        ):
            return _AuditReplayResult(
                positions={},
                cash_usd=0.0,
                realized_pnl_usd=0.0,
                available=False,
                error=f"audit_fill_validation_error_line_{line_number}",
            )

        stop_loss: float | None = None
        take_profit: float | None = None
        if order_id is not None:
            stop_loss, take_profit = order_meta.get(order_id, (None, None))

        existing = positions.get(symbol)
        if side == "buy":
            if existing is None:
                positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=quantity,
                    avg_entry_price=fill_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    opened_at=filled_at,
                    realized_pnl_usd=0.0,
                )
            else:
                total_qty = existing.quantity + quantity
                avg_entry = (
                    (existing.avg_entry_price * existing.quantity) + (fill_price * quantity)
                ) / total_qty
                positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=total_qty,
                    avg_entry_price=avg_entry,
                    stop_loss=stop_loss if stop_loss is not None else existing.stop_loss,
                    take_profit=(
                        take_profit if take_profit is not None else existing.take_profit
                    ),
                    opened_at=existing.opened_at,
                    realized_pnl_usd=existing.realized_pnl_usd,
                )
        else:
            if existing is None or existing.quantity + 1e-9 < quantity:
                return _AuditReplayResult(
                    positions={},
                    cash_usd=0.0,
                    realized_pnl_usd=0.0,
                    available=False,
                    error=f"audit_sell_without_position_line_{line_number}",
                )
            remaining = existing.quantity - quantity
            if remaining <= 1e-8:
                del positions[symbol]
            else:
                positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=remaining,
                    avg_entry_price=existing.avg_entry_price,
                    stop_loss=existing.stop_loss,
                    take_profit=existing.take_profit,
                    opened_at=existing.opened_at,
                    realized_pnl_usd=existing.realized_pnl_usd,
                )

        portfolio_cash = _coerce_float(payload.get("portfolio_cash"))
        if portfolio_cash is not None:
            cash_usd = portfolio_cash
        realized = _coerce_float(payload.get("realized_pnl_usd"))
        if realized is not None:
            realized_pnl_usd = realized

    return _AuditReplayResult(
        positions=positions,
        cash_usd=cash_usd,
        realized_pnl_usd=realized_pnl_usd,
        available=True,
        error=None,
    )


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


async def _query_db_cycles(db_session: AsyncSession) -> list[TradingCycleRecord]:
    """Query all TradingCycleRecords ordered by created_at ascending. Returns [] on error."""
    try:
        result = await db_session.execute(
            select(TradingCycleRecord).order_by(TradingCycleRecord.created_at.asc())
        )
        return list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTFOLIO] DB query failed, falling back to JSONL: %s", exc)
        return []


def _build_snapshot_from_db(
    records: list[TradingCycleRecord],
    generated_at: str,
) -> PortfolioSnapshot:
    """Build a minimal PortfolioSnapshot from DB TradingCycleRecords (positions not available).

    TradingCycleRecord contains cycle-level data (signal, fill flags, notes) but does NOT
    store full position state. The snapshot therefore reports cycle counts and last-cycle
    metadata only, with zero open positions. For position-level data, use the JSONL path.
    """
    completed = [r for r in records if r.status == "completed" and r.fill_simulated]

    source_note = f"db_cycle_records:{len(records)}_total:{len(completed)}_completed"
    empty_exposure = _build_exposure_summary(())

    return PortfolioSnapshot(
        generated_at_utc=generated_at,
        source="db_trading_cycles",
        audit_path=source_note,
        cash_usd=0.0,
        realized_pnl_usd=0.0,
        total_market_value_usd=0.0,
        total_equity_usd=0.0,
        position_count=0,
        positions=(),
        exposure_summary=empty_exposure,
        available=True,
        error=None,
        execution_enabled=False,
        write_back_allowed=False,
    )


async def build_portfolio_snapshot(
    *,
    audit_path: str | Path = "artifacts/paper_execution_audit.jsonl",
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    db_session: AsyncSession | None = None,
) -> PortfolioSnapshot:
    """Build canonical read-only portfolio snapshot from paper execution audit + market data.

    If db_session is provided and the DB contains TradingCycleRecords, the snapshot
    is sourced from the DB (DB-first path). Otherwise falls back to JSONL replay.

    The DB path returns cycle-level metadata only (no open positions); the JSONL path
    returns the full position state including mark-to-market prices.
    """
    generated_at = datetime.now(UTC).isoformat()

    # DB-first: if session provided, try DB
    if db_session is not None:
        db_records = await _query_db_cycles(db_session)
        if db_records:
            return _build_snapshot_from_db(db_records, generated_at)
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

    position_summaries: list[PositionSummary] = []
    for symbol in sorted(replay.positions):
        position = replay.positions[symbol]
        market_snapshot = await get_market_data_snapshot(
            symbol=symbol,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )

        # Fail closed: stale snapshots are treated as not usable for mark-to-market.
        can_use_market_price = (
            market_snapshot.available
            and not market_snapshot.is_stale
            and market_snapshot.price is not None
        )
        market_price = market_snapshot.price if can_use_market_price else None
        market_value = (
            round(position.quantity * market_price, 8)
            if market_price is not None
            else None
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
                    market_snapshot.error
                    or ("stale_data" if market_snapshot.is_stale else None)
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
