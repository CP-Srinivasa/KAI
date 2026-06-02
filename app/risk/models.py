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
    atr_multiplier: float = 2.0
    tp_atr_multiplier: float = 4.0
    regime_filter_enabled: bool = True
    regime_sma_period: int = 200
    # DS-20260528-V2: reject orders whose notional falls below this floor.
    # Guards against dust fills when sizing equity (remaining cash) is depleted.
    min_notional_usd: float = 10.0
    # DS-20260529-V2: hard upper cap on a single position's notional, expressed
    # as % of equity. A tight stop (small ATR) yields huge units → notional can
    # exceed the diversification asset-cap (25%) and the whole order is rejected,
    # deadlocking the loop. This clamps notional to keep first positions tradeable.
    # <= 0 disables the cap (backward-compatible). Productive source is Settings (20).
    # Default 20.0 mirrors the productive Settings default so RiskLimits() built
    # without args (legacy unit tests) gets the safe, enforced behaviour too.
    max_position_size_pct: float = 20.0
    # NEO-V1 (2026-06-01): cost-aware SL geometry gate. The paper venue's
    # round-trip taker fee (~1.2% = 2x60bps) can exceed the ATR-derived stop
    # distance (~0.8-1.0%), making a stopped trade a structurally guaranteed net
    # loss. Reject when |entry-SL|/entry < min_sl_cost_multiple * round_trip_fee.
    #   round_trip_fee_pct: total round-trip cost in PERCENT (entry+exit fees).
    #     Sprint B (CostModel): the PRODUCTIVE value is derived from the CostModel
    #     paper venue (10 bp/side -> 0.2%) and injected via Settings ->
    #     _build_risk_limits_from_settings. This standalone dataclass default
    #     (1.2) only ever matters when the gate is OFF (min_sl_cost_multiple=0.0,
    #     the dataclass default below), so it cannot affect productive gating. It
    #     is intentionally NOT the source of truth — do not read fees from here.
    #   min_sl_cost_multiple: factor k. <= 0 DISABLES the gate (backward-compatible
    #     default — legacy unit tests build RiskLimits without this field). The
    #     productive value lives in Settings (RISK_MIN_SL_COST_MULTIPLE, default
    #     1.5). k=1.5 => min SL ~1.8%. OPERATOR-SIGN-OFF PARAMETER.
    round_trip_fee_pct: float = 1.2
    min_sl_cost_multiple: float = 0.0
    # Sprint 2026-06-02 — reward/risk + risk-budget gates. ALL default-OFF
    # (disabled sentinel) so this is a strict no-op for existing callers and
    # tests; productive values are injected from Settings and are
    # OPERATOR-SIGN-OFF parameters. Evaluation is fail-closed: when a gate is
    # ENABLED but the inputs needed to evaluate it (targets / leverage / entry /
    # SL) are missing or non-positive, the order is rejected rather than waved
    # through. See app/risk/engine.py Gate 10.
    #   min_rr: minimum reward/risk on the nearest target. <= 0 disables.
    min_rr: float = 0.0
    #   min_avg_rr: minimum reward/risk averaged over all targets. <= 0 disables.
    min_avg_rr: float = 0.0
    #   max_signal_risk_pct: max UN-leveraged stop distance |entry-SL|/entry*100.
    #     <= 0 disables.
    max_signal_risk_pct: float = 0.0
    #   max_leveraged_risk_pct: max stop distance * leverage (the "Risk 42%"
    #     figure a 10x channel reports). <= 0 disables.
    max_leveraged_risk_pct: float = 0.0
    #   min_net_edge_bps: minimum cost-adjusted edge on the nearest target,
    #     net of the round-trip fee, in basis points. None disables. Uses
    #     round_trip_fee_pct (the SAME cost the engine/CostModel charge).
    min_net_edge_bps: float | None = None
    #   min_target_distance_pct: nearest target must be at least this far from
    #     entry (favourable direction), in percent. <= 0 disables.
    min_target_distance_pct: float = 0.0


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
    # Stable machine-grade codes mapped from `violations` (see
    # app/risk/reason_codes.py). Additive: `violations` stays the human contract.
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PositionSizeResult:
    """Result of position sizing calculation."""

    approved: bool
    symbol: str
    position_size_pct: float  # % of equity
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
    loss_pct: float  # negative means loss
    kill_switch_triggered: bool


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def _new_check_id() -> str:
    return f"rck_{uuid.uuid4().hex[:12]}"
