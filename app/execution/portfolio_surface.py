"""Read-only portfolio and exposure surface for paper trading.

Produces immutable snapshots of portfolio state for operator surfaces
(Telegram, CLI, MCP). No mutation, no trading, no execution capability.

Security invariants:
- All output models are frozen=True
- No write/trade/execute methods
- Fail-closed: returns empty/safe defaults on any error
- No broker or exchange imports
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.execution.models import PaperPortfolio

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionSnapshot:
    """Immutable snapshot of a single position."""

    symbol: str
    quantity: float
    avg_entry_price: float
    stop_loss: float | None
    take_profit: float | None
    current_price: float | None
    unrealized_pnl: float
    unrealized_pnl_pct: float
    is_stale_price: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_entry_price": self.avg_entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "current_price": self.current_price,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "is_stale_price": self.is_stale_price,
        }


@dataclass(frozen=True)
class PortfolioSummary:
    """Immutable read-only portfolio summary."""

    initial_equity: float
    cash: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    total_fees: float
    trade_count: int
    open_position_count: int
    drawdown_pct: float
    positions: tuple[PositionSnapshot, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "initial_equity": self.initial_equity,
            "cash": self.cash,
            "total_equity": round(self.total_equity, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "total_fees": round(self.total_fees, 4),
            "trade_count": self.trade_count,
            "open_position_count": self.open_position_count,
            "drawdown_pct": round(self.drawdown_pct, 2),
            "positions": [p.to_dict() for p in self.positions],
        }

    def to_telegram_text(self) -> str:
        """Format for Telegram Markdown display."""
        lines = [
            "*Portfolio Summary*",
            f"Equity: `${self.total_equity:,.2f}`",
            f"Cash: `${self.cash:,.2f}`",
            f"Realized PnL: `${self.realized_pnl:,.2f}`",
            f"Unrealized PnL: `${self.unrealized_pnl:,.2f}`",
            f"Drawdown: `{self.drawdown_pct:.2f}%`",
            f"Fees: `${self.total_fees:,.4f}`",
            f"Trades: `{self.trade_count}`",
            f"Open Positions: `{self.open_position_count}`",
        ]
        if self.positions:
            lines.append("")
            for p in self.positions:
                stale = " ⚠️STALE" if p.is_stale_price else ""
                price_str = (
                    f"${p.current_price:,.2f}" if p.current_price
                    else "N/A"
                )
                pnl_sign = "+" if p.unrealized_pnl >= 0 else ""
                lines.append(
                    f"`{p.symbol}` "
                    f"qty={p.quantity:.4f} "
                    f"entry=${p.avg_entry_price:,.2f} "
                    f"now={price_str} "
                    f"pnl={pnl_sign}{p.unrealized_pnl:,.2f}"
                    f"{stale}"
                )
        else:
            lines.append("\n_No open positions_")
        return "\n".join(lines)


@dataclass(frozen=True)
class ExposureSummary:
    """Immutable exposure breakdown."""

    total_exposure_usd: float
    cash_pct: float
    invested_pct: float
    largest_position_symbol: str
    largest_position_pct: float
    position_count: int
    per_position: tuple[
        tuple[str, float, float], ...
    ] = ()  # (symbol, usd_value, pct)

    def to_dict(self) -> dict[str, object]:
        return {
            "total_exposure_usd": round(
                self.total_exposure_usd, 2,
            ),
            "cash_pct": round(self.cash_pct, 2),
            "invested_pct": round(self.invested_pct, 2),
            "largest_position_symbol": (
                self.largest_position_symbol
            ),
            "largest_position_pct": round(
                self.largest_position_pct, 2,
            ),
            "position_count": self.position_count,
            "per_position": [
                {
                    "symbol": s,
                    "usd_value": round(v, 2),
                    "pct": round(p, 2),
                }
                for s, v, p in self.per_position
            ],
        }

    def to_telegram_text(self) -> str:
        """Format for Telegram Markdown display."""
        lines = [
            "*Exposure Summary*",
            f"Total: `${self.total_exposure_usd:,.2f}`",
            f"Cash: `{self.cash_pct:.1f}%`",
            f"Invested: `{self.invested_pct:.1f}%`",
            f"Positions: `{self.position_count}`",
        ]
        if self.per_position:
            lines.append("")
            for sym, usd_val, pct in self.per_position:
                lines.append(
                    f"`{sym}` "
                    f"${usd_val:,.2f} ({pct:.1f}%)"
                )
        else:
            lines.append("\n_No exposure — fully in cash_")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Builder functions (read-only, fail-closed)
# ------------------------------------------------------------------


def build_portfolio_summary(
    portfolio: PaperPortfolio,
    prices: dict[str, float] | None = None,
    stale_symbols: set[str] | None = None,
) -> PortfolioSummary:
    """Build immutable portfolio summary from live state.

    Args:
        portfolio: PaperPortfolio instance (mutable state)
        prices: current prices per symbol (for unrealized PnL)
        stale_symbols: symbols with stale price data

    Returns:
        Frozen PortfolioSummary snapshot
    """
    p = prices or {}
    stale = stale_symbols or set()
    snapshots: list[PositionSnapshot] = []
    total_unrealized = 0.0

    for sym, pos in portfolio.positions.items():
        current = p.get(sym)
        if current is not None and current > 0:
            upnl = (current - pos.avg_entry_price) * pos.quantity
            upnl_pct = (
                (current - pos.avg_entry_price)
                / pos.avg_entry_price * 100
                if pos.avg_entry_price > 0 else 0.0
            )
        else:
            upnl = 0.0
            upnl_pct = 0.0
        total_unrealized += upnl
        snapshots.append(PositionSnapshot(
            symbol=sym,
            quantity=pos.quantity,
            avg_entry_price=pos.avg_entry_price,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            current_price=current,
            unrealized_pnl=round(upnl, 2),
            unrealized_pnl_pct=round(upnl_pct, 2),
            is_stale_price=sym in stale,
        ))

    equity = portfolio.total_equity(p)
    dd = portfolio.drawdown_pct(p)

    return PortfolioSummary(
        initial_equity=portfolio.initial_equity,
        cash=portfolio.cash,
        total_equity=equity,
        realized_pnl=portfolio.realized_pnl_usd,
        unrealized_pnl=round(total_unrealized, 2),
        total_fees=portfolio.total_fees_usd,
        trade_count=portfolio.trade_count,
        open_position_count=len(portfolio.positions),
        drawdown_pct=dd,
        positions=tuple(snapshots),
    )


def build_exposure_summary(
    portfolio: PaperPortfolio,
    prices: dict[str, float] | None = None,
) -> ExposureSummary:
    """Build immutable exposure summary from live state.

    Args:
        portfolio: PaperPortfolio instance
        prices: current prices per symbol

    Returns:
        Frozen ExposureSummary snapshot
    """
    p = prices or {}
    equity = portfolio.total_equity(p)
    if equity <= 0:
        return ExposureSummary(
            total_exposure_usd=0.0,
            cash_pct=100.0,
            invested_pct=0.0,
            largest_position_symbol="",
            largest_position_pct=0.0,
            position_count=0,
        )

    per_pos: list[tuple[str, float, float]] = []
    largest_sym = ""
    largest_pct = 0.0

    for sym, pos in portfolio.positions.items():
        current = p.get(sym, pos.avg_entry_price)
        value = pos.quantity * current
        pct = (value / equity * 100) if equity > 0 else 0.0
        per_pos.append((sym, round(value, 2), round(pct, 2)))
        if pct > largest_pct:
            largest_pct = pct
            largest_sym = sym

    invested = sum(v for _, v, _ in per_pos)
    cash_pct = (portfolio.cash / equity * 100) if equity > 0 else 0.0
    invested_pct = (invested / equity * 100) if equity > 0 else 0.0

    return ExposureSummary(
        total_exposure_usd=equity,
        cash_pct=round(cash_pct, 2),
        invested_pct=round(invested_pct, 2),
        largest_position_symbol=largest_sym,
        largest_position_pct=round(largest_pct, 2),
        position_count=len(portfolio.positions),
        per_position=tuple(per_pos),
    )
