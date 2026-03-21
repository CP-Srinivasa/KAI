"""Risk Engine typed models — all frozen, immutable, auditable."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class RiskLimits:
    """Hard limits enforced by the Risk Engine. Non-negotiable."""
    initial_equity: float
    max_risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_total_drawdown_pct: float
    max_open_positions: int
    max_leverage: float
    require_stop_loss: bool
    allow_averaging_down: bool
    allow_martingale: bool
    kill_switch_enabled: bool
    min_signal_confidence: float
    min_signal_confluence_count: int


@dataclass(frozen=True)
class RiskCheckResult:
    """Result of a single risk gate check."""
    approved: bool
    check_id: str
    timestamp_utc: str
    symbol: str
    check_type: str
    reason: str
    violations: list[str] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionSizeResult:
    """Result of position sizing calculation."""
    approved: bool
    symbol: str
    position_size_pct: float   # % of equity
    position_size_units: float
    entry_price: float
    stop_loss_price: float | None
    max_loss_usd: float
    max_loss_pct: float
    rationale: str


@dataclass(frozen=True)
class DailyLossState:
    """Tracks daily loss for kill-switch logic."""
    date_utc: str
    realized_pnl_usd: float
    loss_pct: float          # negative means loss
    kill_switch_triggered: bool


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def _new_check_id() -> str:
    return f"rck_{uuid.uuid4().hex[:12]}"
