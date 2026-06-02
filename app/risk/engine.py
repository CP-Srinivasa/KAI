"""Risk Engine — hard pre-gate before any order. Never bypass. (Security First)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.risk.models import (
    DailyLossState,
    PositionSizeResult,
    RiskCheckResult,
    RiskLimits,
    _new_check_id,
    _now_utc,
)
from app.risk.reason_codes import map_violations_to_codes

logger = logging.getLogger(__name__)


class RiskEngine:
    """
    Hard risk gate — must be called before EVERY potential order.

    Design invariants:
    - Never raises on bad input → returns RiskCheckResult(approved=False)
    - Kill switch overrides all checks
    - If kill_switch_enabled and triggered → ALL checks fail
    - Never allows averaging down or martingale
    - Requires stop_loss when configured
    - All decisions are logged
    """

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits
        self._kill_switch_active = False
        self._paused = False
        self._daily_loss_pct: float = 0.0  # updated by execution engine
        self._total_drawdown_pct: float = 0.0  # updated externally
        self._open_position_count: int = 0  # updated externally

    @property
    def is_halted(self) -> bool:
        """True when system is paused or kill switch is active."""
        return self._kill_switch_active or self._paused

    @property
    def limits(self) -> RiskLimits:
        """Read-only access to the active risk limits (for audit/observability)."""
        return self._limits

    def pause(self) -> None:
        """Operator-triggered pause. No new orders allowed."""
        self._paused = True
        logger.warning("[RISK] System PAUSED by operator.")

    def resume(self) -> None:
        """Operator-triggered resume. Only valid if kill switch not active."""
        if self._kill_switch_active:
            logger.error(
                "[RISK] Cannot resume — kill switch is active. Manual intervention required."
            )
            return
        self._paused = False
        logger.info("[RISK] System RESUMED by operator.")

    def trigger_kill_switch(self) -> None:
        """Emergency stop. Requires manual reset to clear."""
        self._kill_switch_active = True
        logger.critical("[RISK] KILL SWITCH ACTIVATED. All operations halted.")

    def reset_kill_switch(self) -> None:
        """Manual operator reset of kill switch."""
        self._kill_switch_active = False
        self._paused = False
        logger.warning("[RISK] Kill switch RESET by operator. System not yet active.")

    def update_daily_loss(self, realized_pnl_usd: float, equity: float) -> DailyLossState:
        """Update daily loss state. Auto-triggers kill switch if limit breached."""
        loss_pct = (realized_pnl_usd / equity * 100) if equity > 0 else 0.0
        self._daily_loss_pct = loss_pct

        kill_triggered = False
        if loss_pct < -abs(self._limits.max_daily_loss_pct):
            if self._limits.kill_switch_enabled:
                self.trigger_kill_switch()
                kill_triggered = True
            logger.error(
                "[RISK] Daily loss limit breached: %.2f%% (limit=%.2f%%)",
                loss_pct,
                -abs(self._limits.max_daily_loss_pct),
            )

        return DailyLossState(
            date_utc=datetime.now(UTC).date().isoformat(),
            realized_pnl_usd=realized_pnl_usd,
            loss_pct=loss_pct,
            kill_switch_triggered=kill_triggered,
        )

    def update_drawdown(self, drawdown_pct: float) -> bool:
        """Update total drawdown. Returns True if kill switch triggered."""
        self._total_drawdown_pct = drawdown_pct
        if drawdown_pct > abs(self._limits.max_total_drawdown_pct):
            if self._limits.kill_switch_enabled:
                self.trigger_kill_switch()
            logger.error(
                "[RISK] Max drawdown breached: %.2f%% (limit=%.2f%%)",
                drawdown_pct,
                self._limits.max_total_drawdown_pct,
            )
            return True
        return False

    def check_order(
        self,
        *,
        symbol: str,
        side: str,
        signal_confidence: float,
        signal_confluence_count: int,
        stop_loss_price: float | None,
        current_open_positions: int,
        is_averaging_down: bool = False,
        entry_price: float | None = None,
        take_profit_price: float | None = None,
        take_profit_targets: list[float] | tuple[float, ...] | None = None,
        leverage: float | None = None,
        sma: float | None = None,
    ) -> RiskCheckResult:
        """
        Pre-order risk gate. Must return approved=True before any order is sent.
        All checks are evaluated; violations accumulate.

        ``take_profit_targets`` (full tier list) and ``leverage`` feed the
        reward/risk + risk-budget gates (Gate 10). They are optional and the
        gates are default-off, so omitting them is backward compatible.
        """
        check_id = _new_check_id()
        violations: list[str] = []

        # Gate 1: Kill switch / pause
        if self._kill_switch_active:
            return RiskCheckResult(
                approved=False,
                check_id=check_id,
                timestamp_utc=_now_utc(),
                symbol=symbol,
                check_type="kill_switch",
                reason="Kill switch is active — no orders allowed",
                violations=["kill_switch_active"],
            )

        if self._paused:
            return RiskCheckResult(
                approved=False,
                check_id=check_id,
                timestamp_utc=_now_utc(),
                symbol=symbol,
                check_type="system_paused",
                reason="System is paused — no orders allowed",
                violations=["system_paused"],
            )

        # Gate 2: Martingale / averaging down
        if is_averaging_down and not self._limits.allow_averaging_down:
            violations.append("averaging_down_not_allowed")

        # AUDIT-A8/F-4: previously a no-op ("trust the caller"). An averaging-down
        # order — adding to an existing (typically losing) position — is exactly
        # the mechanism a martingale uses. We cannot verify *size escalation*
        # in-engine without order-history (proposed vs. prior fill size), which
        # would require plumbing through every caller; that fuller detection is
        # tracked as a follow-up. Until then we enforce fail-safe: when martingale
        # is disallowed, an averaging-down add is blocked rather than silently
        # permitted. (Default config disallows averaging_down too, so this only
        # changes the allow_averaging_down=True + allow_martingale=False case,
        # which is precisely where the operator wants the stricter policy to bite.)
        if self._limits.allow_martingale is False and is_averaging_down:
            if "martingale_not_allowed" not in violations:
                violations.append("martingale_not_allowed")

        # Gate 3: Stop loss required (Hard-Gate, no longer configurable)
        if stop_loss_price is None or stop_loss_price <= 0:
            violations.append("stop_loss_required_but_missing")

        # Gate 3b: SL/TP geometry — prevent inverted stops (long with sl>=entry,
        # short with sl<=entry) and mirror-inverted take-profits. Triggered only
        # when entry_price is provided AND the respective level is set; skips
        # validation when the caller omits entry_price (backwards compatible).
        normalized_side = side.lower()
        if entry_price is not None and entry_price > 0:
            if normalized_side == "buy":
                if stop_loss_price is not None and stop_loss_price >= entry_price:
                    violations.append(
                        f"sl_geometry_invalid:long_sl_at_or_above_entry|"
                        f"entry={entry_price}|sl={stop_loss_price}"
                    )
                if take_profit_price is not None and take_profit_price <= entry_price:
                    violations.append(
                        f"tp_geometry_invalid:long_tp_at_or_below_entry|"
                        f"entry={entry_price}|tp={take_profit_price}"
                    )
            elif normalized_side == "sell":
                if stop_loss_price is not None and stop_loss_price <= entry_price:
                    violations.append(
                        f"sl_geometry_invalid:short_sl_at_or_below_entry|"
                        f"entry={entry_price}|sl={stop_loss_price}"
                    )
                if take_profit_price is not None and take_profit_price >= entry_price:
                    violations.append(
                        f"tp_geometry_invalid:short_tp_at_or_above_entry|"
                        f"entry={entry_price}|tp={take_profit_price}"
                    )

        # Gate 3c (NEO-V1): cost-aware SL geometry. A stop tighter than the
        # round-trip transaction cost cannot win — the fee alone turns the trade
        # net-negative on the way out. Reject when the stop distance fails to
        # clear `min_sl_cost_multiple x round_trip_fee`. Default-off
        # (min_sl_cost_multiple <= 0) for backward compatibility. Skipped when
        # entry_price/SL are absent or non-positive (those are caught by Gate 3 /
        # the missing-entry contract); strict `<` so a stop exactly at the
        # threshold is allowed.
        if (
            self._limits.min_sl_cost_multiple > 0
            and entry_price is not None
            and entry_price > 0
            and stop_loss_price is not None
            and stop_loss_price > 0
        ):
            sl_distance_pct = abs(entry_price - stop_loss_price) / entry_price * 100.0
            min_required_pct = self._limits.min_sl_cost_multiple * self._limits.round_trip_fee_pct
            if sl_distance_pct < min_required_pct:
                violations.append(
                    f"sub_cost_geometry_rejected:sl_dist={sl_distance_pct:.4g}%<"
                    f"{min_required_pct:.4g}%"
                    f"(k={self._limits.min_sl_cost_multiple:.4g}x"
                    f"rt_fee={self._limits.round_trip_fee_pct:.4g}%)"
                )

        # Gate 4: Signal confidence
        if signal_confidence < self._limits.min_signal_confidence:
            violations.append(
                f"signal_confidence_too_low:{signal_confidence:.2f}<{self._limits.min_signal_confidence}"
            )

        # Gate 5: Signal confluence
        if signal_confluence_count < self._limits.min_signal_confluence_count:
            violations.append(
                f"signal_confluence_too_low:{signal_confluence_count}<{self._limits.min_signal_confluence_count}"
            )

        # Gate 6: Max open positions
        if current_open_positions >= self._limits.max_open_positions:
            violations.append(
                f"max_open_positions_reached:{current_open_positions}>={self._limits.max_open_positions}"
            )

        # Gate 7: Daily loss limit
        if self._daily_loss_pct < -abs(self._limits.max_daily_loss_pct):
            violations.append(
                f"daily_loss_limit_breached:{self._daily_loss_pct:.2f}%<-{self._limits.max_daily_loss_pct}%"
            )

        # Gate 8: Total drawdown
        if self._total_drawdown_pct > abs(self._limits.max_total_drawdown_pct):
            violations.append(
                f"drawdown_limit_breached:{self._total_drawdown_pct:.2f}%>{self._limits.max_total_drawdown_pct}%"
            )

        # Gate 9: Regime Filter (Anti-Fehlsignal — Cluster 3b)
        # Reject trades that fight the prevailing trend defined by an SMA reference.
        # Bypassed when regime_filter_enabled=False OR sma=None OR entry_price=None,
        # so callers that don't provide regime context remain backwards compatible.
        if self._limits.regime_filter_enabled and sma is not None and entry_price is not None:
            side_norm = side.strip().lower()
            if entry_price > sma and side_norm in {"sell", "short"}:
                violations.append(f"regime_conflict:uptrend_rejects_{side_norm}")
            elif entry_price < sma and side_norm in {"buy", "long"}:
                violations.append(f"regime_conflict:downtrend_rejects_{side_norm}")

        # Gate 10 (Sprint 2026-06-02): reward/risk + risk-budget gates.
        # Geometry diagnostics are computed ALWAYS (even when an earlier gate
        # already fired, e.g. max_open_positions) so the audit/UI can show WHY a
        # signal is structurally good or bad independent of the first blocker.
        geometry = self._signal_geometry(
            side=normalized_side,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            targets=list(take_profit_targets) if take_profit_targets else None,
            leverage=leverage,
        )
        # Gate 10 honours gates_mode: "off" skips, "audit" records would_reject
        # without blocking, "enforce" merges into the blocking violations.
        gates_mode = (self._limits.gates_mode or "audit").strip().lower()
        rr_violations: list[str] = []
        if gates_mode != "off":
            self._apply_reward_risk_gates(geometry, rr_violations)
        would_reject = bool(rr_violations)
        would_reject_codes = map_violations_to_codes(rr_violations)
        if gates_mode == "enforce":
            violations.extend(rr_violations)

        approved = len(violations) == 0
        reason = (
            "All risk gates passed" if approved else f"Risk violations: {'; '.join(violations)}"
        )

        result = RiskCheckResult(
            approved=approved,
            check_id=check_id,
            timestamp_utc=_now_utc(),
            symbol=symbol,
            check_type="pre_order",
            reason=reason,
            violations=violations,
            reason_codes=map_violations_to_codes(violations),
            would_reject=would_reject,
            would_reject_violations=rr_violations,
            would_reject_codes=would_reject_codes,
            details={
                "side": side,
                "signal_confidence": signal_confidence,
                "signal_confluence": signal_confluence_count,
                "open_positions": current_open_positions,
                "daily_loss_pct": self._daily_loss_pct,
                "drawdown_pct": self._total_drawdown_pct,
                "signal_geometry": geometry,
                "gates_mode": gates_mode,
            },
        )

        if approved:
            logger.info("[RISK] Order approved: %s %s (check_id=%s)", side, symbol, check_id)
        else:
            logger.warning(
                "[RISK] Order REJECTED: %s %s violations=%s (check_id=%s)",
                side,
                symbol,
                violations,
                check_id,
            )

        return result

    def _signal_geometry(
        self,
        *,
        side: str,
        entry_price: float | None,
        stop_loss_price: float | None,
        targets: list[float] | None,
        leverage: float | None,
    ) -> dict[str, object] | None:
        """Compute reward/risk geometry for the reward-risk gates + audit.

        Returns ``None`` when entry/SL are not usable (non-positive/missing) — a
        sentinel the gate layer treats as "insufficient data". `targets` may be
        ``None`` (some callers have only a single TP); reward fields are then
        ``None`` but the risk-distance fields still populate.

        Reward is signed in the *favourable* direction: for a long, target above
        entry is positive; for a short, target below entry is positive. ``t1`` is
        the first listed target (matches the channel's tier-1 convention).
        """
        if entry_price is None or entry_price <= 0:
            return None
        if stop_loss_price is None or stop_loss_price <= 0:
            return None

        lev = leverage if (leverage is not None and leverage > 0) else 1.0
        stop_distance_pct = abs(entry_price - stop_loss_price) / entry_price * 100.0
        leveraged_risk_pct = stop_distance_pct * lev

        def _favourable_reward_pct(target: float) -> float:
            if side == "buy":
                return (target - entry_price) / entry_price * 100.0
            # sell/short
            return (entry_price - target) / entry_price * 100.0

        geom: dict[str, object] = {
            "side": side,
            "leverage": lev,
            "stop_distance_pct": round(stop_distance_pct, 6),
            "leveraged_risk_pct": round(leveraged_risk_pct, 6),
            "round_trip_fee_pct": self._limits.round_trip_fee_pct,
        }

        valid_targets = [t for t in (targets or []) if isinstance(t, (int, float)) and t > 0]
        if valid_targets and stop_distance_pct > 0:
            rewards = [_favourable_reward_pct(float(t)) for t in valid_targets]
            t1_reward_pct = rewards[0]
            avg_reward_pct = sum(rewards) / len(rewards)
            net_edge_bps = (t1_reward_pct - self._limits.round_trip_fee_pct) * 100.0
            geom.update(
                {
                    "t1_reward_pct": round(t1_reward_pct, 6),
                    "avg_reward_pct": round(avg_reward_pct, 6),
                    "nearest_target_distance_pct": round(t1_reward_pct, 6),
                    "rr_t1": round(t1_reward_pct / stop_distance_pct, 6),
                    "avg_rr": round(avg_reward_pct / stop_distance_pct, 6),
                    "net_edge_bps_t1": round(net_edge_bps, 4),
                    "n_targets": len(valid_targets),
                }
            )
        return geom

    def _apply_reward_risk_gates(
        self, geometry: dict[str, object] | None, violations: list[str]
    ) -> None:
        """Append violations for the Sprint-2026-06-02 reward/risk gates.

        Fail-closed: when a gate is ENABLED (threshold > 0 / not None) but the
        required geometry is unavailable, the order is rejected with an
        ``*:insufficient_data`` violation rather than silently passing.
        """
        lim = self._limits
        gate_enabled = (
            lim.min_rr > 0
            or lim.min_avg_rr > 0
            or lim.max_signal_risk_pct > 0
            or lim.max_leveraged_risk_pct > 0
            or lim.min_net_edge_bps is not None
            or lim.min_target_distance_pct > 0
        )
        if not gate_enabled:
            return

        if geometry is None:
            violations.append("signal_risk_too_high:insufficient_data:entry_or_sl_missing")
            return

        def _num(key: str) -> float | None:
            v = geometry.get(key)
            return float(v) if isinstance(v, (int, float)) else None

        rr_t1 = _num("rr_t1")
        avg_rr = _num("avg_rr")
        stop_distance_pct = _num("stop_distance_pct")
        leveraged_risk_pct = _num("leveraged_risk_pct")
        net_edge_bps = _num("net_edge_bps_t1")
        nearest_target_pct = _num("nearest_target_distance_pct")

        # Reward/risk floors (need targets).
        if lim.min_rr > 0:
            if rr_t1 is None:
                violations.append("rr_too_low:insufficient_data:no_targets")
            elif rr_t1 < lim.min_rr:
                violations.append(f"rr_too_low:{rr_t1:.4g}<{lim.min_rr:.4g}")
        if lim.min_avg_rr > 0:
            if avg_rr is None:
                violations.append("avg_rr_too_low:insufficient_data:no_targets")
            elif avg_rr < lim.min_avg_rr:
                violations.append(f"avg_rr_too_low:{avg_rr:.4g}<{lim.min_avg_rr:.4g}")

        # Risk-budget ceilings (need stop distance — always present when geometry
        # is not None).
        if lim.max_signal_risk_pct > 0 and stop_distance_pct is not None:
            if stop_distance_pct > lim.max_signal_risk_pct:
                violations.append(
                    f"signal_risk_too_high:{stop_distance_pct:.4g}%>{lim.max_signal_risk_pct:.4g}%"
                )
        if lim.max_leveraged_risk_pct > 0 and leveraged_risk_pct is not None:
            if leveraged_risk_pct > lim.max_leveraged_risk_pct:
                violations.append(
                    f"leveraged_risk_too_high:{leveraged_risk_pct:.4g}%"
                    f">{lim.max_leveraged_risk_pct:.4g}%"
                )

        # Net edge after fees (need targets).
        if lim.min_net_edge_bps is not None:
            if net_edge_bps is None:
                violations.append("net_edge_too_low:insufficient_data:no_targets")
            elif net_edge_bps < lim.min_net_edge_bps:
                violations.append(
                    f"net_edge_too_low:{net_edge_bps:.4g}bps<{lim.min_net_edge_bps:.4g}bps"
                )

        # Minimum nearest-target distance (need targets).
        if lim.min_target_distance_pct > 0:
            if nearest_target_pct is None:
                violations.append("target_too_close:insufficient_data:no_targets")
            elif nearest_target_pct < lim.min_target_distance_pct:
                violations.append(
                    f"target_too_close:{nearest_target_pct:.4g}%<{lim.min_target_distance_pct:.4g}%"
                )

    def calculate_risk_geometry(
        self,
        *,
        entry_price: float,
        direction: str,
        atr: float | None,
    ) -> tuple[float | None, float | None]:
        """
        Dynamically calculate stop-loss and take-profit bounds based on ATR.
        Returns (stop_loss_price, take_profit_price).
        If atr is None, returns (None, None).
        """
        if atr is None or atr <= 0 or entry_price <= 0:
            return None, None

        direction_normalized = direction.strip().lower()

        sl_distance = atr * self._limits.atr_multiplier
        tp_distance = atr * self._limits.tp_atr_multiplier

        if direction_normalized in {"long", "buy"}:
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        elif direction_normalized in {"short", "sell"}:
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance
        else:
            return None, None

        return stop_loss, take_profit

    def calculate_position_size(
        self,
        *,
        symbol: str,
        entry_price: float,
        stop_loss_price: float | None,
        equity: float,
        leverage: float | None = None,
        risk_allocation_pct: float | None = None,
    ) -> PositionSizeResult:
        """
        Calculate safe position size based on risk limits or explicit channel sizing.
        If `risk_allocation_pct` and `leverage` are provided, computes deterministically.
        Otherwise, Risk per trade = max_risk_per_trade_pct % of equity.
        If no stop loss, uses minimum position size.
        """
        if entry_price <= 0 or equity <= 0:
            return PositionSizeResult(
                approved=False,
                symbol=symbol,
                position_size_pct=0.0,
                position_size_units=0.0,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                max_loss_usd=0.0,
                max_loss_pct=0.0,
                rationale="Invalid price or equity",
            )

        max_risk_usd = equity * (self._limits.max_risk_per_trade_pct / 100)

        # V-DB5 (2026-05-09): Stop-Loss ist mandatory für Sizing — ohne SL
        # kann Risk nicht quantifiziert werden, also reject hard.
        if stop_loss_price is None or stop_loss_price <= 0:
            return PositionSizeResult(
                approved=False,
                symbol=symbol,
                position_size_pct=0.0,
                position_size_units=0.0,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                max_loss_usd=0.0,
                max_loss_pct=0.0,
                rationale="Risk geometry missing: Stop-Loss is mandatory for sizing.",
            )

        sizing_mode = "risk_based"
        leverage_capped = False
        if risk_allocation_pct is not None and risk_allocation_pct > 0:
            # Channel-stated fixed margin and leverage (Antigravity 2026-05-10).
            # Risk caps still win: requested notional is clipped to
            # max_risk_per_trade_pct whenever stop-distance implies a larger
            # worst-case loss.
            sizing_mode = "signal_margin_leverage"
            eff_leverage = leverage if leverage is not None and leverage > 0 else 1.0
            if eff_leverage > self._limits.max_leverage:
                logger.warning(
                    "[RISK] Capping leverage from %s to %s for %s",
                    eff_leverage,
                    self._limits.max_leverage,
                    symbol,
                )
                eff_leverage = self._limits.max_leverage
                leverage_capped = True
            margin_usd = equity * (risk_allocation_pct / 100.0)
            notional_usd = margin_usd * eff_leverage
            requested_units = notional_usd / entry_price
            units = requested_units
            risk_per_unit = abs(entry_price - stop_loss_price)
            if risk_per_unit > 0:
                risk_capped_units = max_risk_usd / risk_per_unit
                if units > risk_capped_units:
                    sizing_mode = "signal_margin_leverage_risk_capped"
                    units = risk_capped_units
        else:
            # V-DB5-Default: SL-distanzbasiertes Sizing (kein channel-margin gegeben).
            risk_per_unit = abs(entry_price - stop_loss_price)
            if risk_per_unit > 0:
                units = max_risk_usd / risk_per_unit
            else:
                units = max_risk_usd / entry_price

        position_value = units * entry_price

        # DS-20260529-V2: hard upper notional cap (% of equity). Applied AFTER the
        # risk-cap and SL-distance sizing (so the loss-cap still wins on the
        # downside) but BEFORE the dust gate (a clamp may legitimately push a
        # position below min_notional → then dust-reject is correct). A tight stop
        # (small ATR → huge units) would otherwise bind 50-70% of equity and trip
        # the 25% diversification asset-cap, deadlocking the loop. max_position_size_pct
        # <= 0 disables the cap (backward-compatible).
        position_capped = False
        if self._limits.max_position_size_pct > 0:
            max_position_value = equity * (self._limits.max_position_size_pct / 100)
            if position_value > max_position_value:
                uncapped_value = position_value
                units = max_position_value / entry_price
                position_value = units * entry_price
                position_capped = True
                sizing_mode = f"{sizing_mode}_position_capped"
                position_cap_note = (
                    f" position_size_capped: ${uncapped_value:.2f} -> "
                    f"${position_value:.2f} ({self._limits.max_position_size_pct:.4g}% "
                    f"equity cap)"
                )

        # DS-20260528-V2: dust gate. Sizing equity is the portfolio's remaining
        # cash (trading_loop), so a nearly-deployed portfolio yields a near-zero
        # notional (~1e-16 units). Those fill but take no real position — they
        # only pollute the audit and inflate the fill count. Reject below floor.
        if position_value < self._limits.min_notional_usd:
            return PositionSizeResult(
                approved=False,
                symbol=symbol,
                position_size_pct=0.0,
                position_size_units=0.0,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                max_loss_usd=0.0,
                max_loss_pct=0.0,
                rationale=(
                    f"dust_below_min_notional: ${position_value:.4g} < "
                    f"${self._limits.min_notional_usd:.2f} (sizing_equity=${equity:.2f})"
                    + (position_cap_note if position_capped else "")
                ),
            )
        position_size_pct = (position_value / equity) * 100
        if stop_loss_price is not None and stop_loss_price > 0:
            max_loss_usd = abs(entry_price - stop_loss_price) * units
        else:
            max_loss_usd = min(max_risk_usd, position_value)
        max_loss_pct = (max_loss_usd / equity) * 100

        if risk_allocation_pct is not None and risk_allocation_pct > 0:
            rationale = (
                f"{sizing_mode}: margin={risk_allocation_pct:.4g}% "
                f"leverage={eff_leverage:.4g}x"
                f"{' (capped)' if leverage_capped else ''}, "
                f"{units:.4f} units @ {entry_price:.2f}, "
                f"max_loss={max_loss_pct:.4g}%"
            )
        else:
            rationale = (
                f"Risk-based sizing: {self._limits.max_risk_per_trade_pct}% equity risk, "
                f"{units:.4f} units @ {entry_price:.2f}"
            )
        if position_capped:
            rationale += position_cap_note

        return PositionSizeResult(
            approved=True,
            symbol=symbol,
            position_size_pct=position_size_pct,
            position_size_units=units,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            max_loss_usd=max_loss_usd,
            max_loss_pct=max_loss_pct,
            rationale=rationale,
        )
