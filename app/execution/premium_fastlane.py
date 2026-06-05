"""Premium-Telegram Fastlane decision core (Goal 2026-06-05).

This module is the single source of truth for *whether* and *how* an authentic
premium-telegram signal is routed into immediate PAPER/TESTNET/DEMO execution
during the controlled 30-day test window.

It is intentionally **pure**: no disk, no market-data, no engine calls. The
bridge (``envelope_to_paper_bridge``) and the runtime endpoint both consult it
so the operator-facing truth and the execution behaviour cannot drift apart.

Design contract (per Goal §5):

``should_route_premium_fastlane(envelope, settings) -> FastlaneDecision``

The fastlane may ONLY block on hard, signal-integrity reasons:
  - not an authentic premium-telegram signal
  - missing required fields (entry / SL / targets / side / symbol / leverage)
  - fastlane disabled or window expired
  - a live route requested without the full triple-flag arming

It must NOT block on (these become ``observe_only`` measurements instead):
  - missing manual approval
  - classic source allowlist
  - ``entry_mode=disabled``
  - source quality / premium bonus / forward precision / priority tier / lift /
    quality-bar / historical hit-rate
  - unknown exchange (as long as a paper/simulated route exists)

LIVE stays hard-protected: a live route is only permitted when ALL THREE hold
(``premium_fastlane.live_enabled`` + ``premium.live_execution_enabled`` +
``premium.live_canary_explicit_ack == LIVE_CANARY_ACK_SENTINEL``). Absent that,
``live_protected`` is True and the route is forced to the first non-live target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.core.settings import LIVE_CANARY_ACK_SENTINEL

if TYPE_CHECKING:
    from app.core.settings import AppSettings, PremiumFastlaneSettings

# Non-live routes the fastlane may pick. ``simulated_exchange`` is the last
# resort so an exotic symbol with no testnet listing still produces data.
_NON_LIVE_ROUTES = ("paper", "testnet", "demo", "simulated_exchange")

# The set of pre-trade gates the fastlane is *allowed* to bypass. Kept explicit
# so a reader can audit exactly what is relaxed — and what is not.
_BYPASSABLE_GATES = (
    "manual_approval",
    "source_allowlist",
    "entry_mode_for_paper",
    "risk_quality_gates",
    "source_quality_gates",
    "priority_tier_gates",
    "forward_precision_gates",
)

# Quality / scoring metric keys that become observe_only under fastlane. They
# are surfaced to the dashboard but never block. (Goal §10/§19.)
OBSERVE_ONLY_METRICS = (
    "premium_signal_bonus",
    "delta_to_target",
    "forward_precision",
    "priority_tier_lift",
    "source_quality",
    "path_a",
    "path_b",
    "lift",
    "quality_bar",
    "historical_hit_rate",
    "resolved_alerts",
)


@dataclass(frozen=True)
class FastlaneDecision:
    """Result of the fastlane routing decision for one envelope."""

    enabled: bool
    route: str  # "paper" | "testnet" | "demo" | "simulated_exchange" | "blocked"
    reason: str | None
    bypassed_gates: list[str] = field(default_factory=list)
    required_guards_passed: list[str] = field(default_factory=list)
    live_protected: bool = True

    @property
    def is_routable(self) -> bool:
        return self.enabled and self.route != "blocked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "route": self.route,
            "reason": self.reason,
            "bypassed_gates": list(self.bypassed_gates),
            "required_guards_passed": list(self.required_guards_passed),
            "live_protected": self.live_protected,
        }


def _blocked(reason: str, *, live_protected: bool = True) -> FastlaneDecision:
    return FastlaneDecision(
        enabled=False,
        route="blocked",
        reason=reason,
        live_protected=live_protected,
    )


# ── Window ──────────────────────────────────────────────────────────────────


def _parse_start(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def fastlane_window(
    fl: PremiumFastlaneSettings, *, now: datetime | None = None
) -> tuple[bool, str | None, datetime | None, int | None]:
    """Return ``(active, reason, end_date, days_remaining)``.

    Empty ``start_date`` → open-ended-from-now: active while enabled (no end).
    A set ``start_date`` pins a ``duration_days`` window; once past, expired.
    """
    now = now or datetime.now(UTC)
    start = _parse_start(fl.start_date)
    if start is None:
        return True, None, None, None
    end = start + timedelta(days=fl.duration_days)
    if now > end:
        return False, "fastlane_window_expired", end, 0
    days_remaining = max(0, (end - now).days)
    return True, None, end, days_remaining


# ── Live arming (triple-flag) ───────────────────────────────────────────────


def live_fastlane_armed(settings: AppSettings) -> bool:
    """True only when ALL THREE live-arming conditions hold. Any missing flag
    keeps live execution refused (§4)."""
    fl = settings.premium_fastlane
    prem = settings.premium
    return (
        fl.live_enabled
        and prem.live_execution_enabled
        and prem.live_canary_explicit_ack == LIVE_CANARY_ACK_SENTINEL
    )


# ── Source authenticity ─────────────────────────────────────────────────────


def _payload(envelope: dict[str, Any]) -> dict[str, Any]:
    p = envelope.get("payload")
    return p if isinstance(p, dict) else {}


def _source(envelope: dict[str, Any]) -> str:
    raw = envelope.get("source")
    return raw.strip().lower() if isinstance(raw, str) else ""


def is_authorized_premium_fastlane_source(envelope: dict[str, Any]) -> bool:
    """Authentic premium-telegram origin check (§8).

    Authentic when the record carries the canonical premium-channel source tag
    AND a stable telegram identity (source_uid / chat_id). The ``_approved``
    re-emit suffix is accepted (it is the same signal after an operator click).
    A non-telegram or unmarked record is NOT authentic and is refused — the
    allowlist bypass must never widen to arbitrary sources.
    """
    source = _source(envelope)
    if not source.startswith("telegram_premium"):
        return False
    payload = _payload(envelope)
    has_identity = bool(
        envelope.get("source_uid")
        or payload.get("source_uid")
        or envelope.get("chat_id")
        or payload.get("source_chat_id")
        or envelope.get("message_id")
        or payload.get("source_message_id")
    )
    return has_identity


# ── Required-field guards ───────────────────────────────────────────────────


def _is_pos_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) > 0


def _num(v: Any) -> float | None:
    """Return ``v`` as a positive float, or None (bool/non-number/<=0)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return float(v)
    return None


def _resolve_entry(payload: dict[str, Any]) -> float | None:
    if str(payload.get("entry_type") or "").lower() == "range":
        emin = _num(payload.get("entry_min"))
        emax = _num(payload.get("entry_max"))
        if emin is not None and emax is not None and emax > emin:
            return (emin + emax) / 2
        return None
    return _num(payload.get("entry_value"))


def check_required_fields(
    envelope: dict[str, Any], fl: PremiumFastlaneSettings
) -> tuple[list[str], list[str]]:
    """Return ``(missing, passed)`` for the hard minimum-guard fields.

    ``require_leverage`` is satisfied either by a present positive leverage OR
    by the configured default (default-leverage policy, §12) — a missing
    leverage is only a hard miss when a default is not permitted.
    """
    payload = _payload(envelope)
    missing: list[str] = []
    passed: list[str] = []

    symbol = payload.get("display_symbol") or payload.get("symbol")
    if isinstance(symbol, str) and symbol.strip():
        passed.append("symbol")
    else:
        missing.append("symbol")

    if not fl.require_entry or _resolve_entry(payload) is not None:
        passed.append("entry")
    else:
        missing.append("entry")

    if not fl.require_sl or _is_pos_number(payload.get("stop_loss")):
        passed.append("stop_loss")
    else:
        missing.append("stop_loss")

    targets = payload.get("targets")
    has_target = isinstance(targets, list) and any(_is_pos_number(t) for t in targets)
    if not fl.require_targets or has_target:
        passed.append("targets")
    else:
        missing.append("targets")

    direction = str(payload.get("direction") or "").lower()
    side = str(payload.get("side") or "").lower()
    if direction in {"long", "short"} or side in {"buy", "sell"}:
        passed.append("side")
    else:
        missing.append("side")

    if (
        not fl.require_leverage
        or _is_pos_number(payload.get("leverage"))
        or fl.default_leverage > 0
    ):
        passed.append("leverage")
    else:
        missing.append("leverage")

    if fl.require_schema_valid:
        passed.append("schema_valid")

    return missing, passed


# ── Leverage + notional policy ──────────────────────────────────────────────


def resolve_leverage(
    signal_leverage: float | None, fl: PremiumFastlaneSettings
) -> tuple[float, str | None]:
    """Apply §12 leverage policy. Returns ``(leverage, audit_note|None)``."""
    if signal_leverage is None or not _is_pos_number(signal_leverage):
        return fl.default_leverage, "leverage_defaulted_to_10x"
    lev = float(signal_leverage)
    if lev > fl.max_leverage:
        return fl.max_leverage, "leverage_clamped_to_10x"
    return lev, None


def resolve_notional(
    entry_price: float, fl: PremiumFastlaneSettings
) -> tuple[float, float, str | None]:
    """Compute fastlane notional + quantity (§12). Returns
    ``(notional_usdt, quantity, reject_reason|None)``.

    notional = clamp(default, [min, max]); quantity = notional / entry_price.
    Rejects (hard guard) when entry_price<=0, or the clamped notional falls
    outside [min, max] (cannot happen with valid bounds but checked defensively),
    or quantity collapses to a dust/zero size.
    """
    if not _is_pos_number(entry_price):
        return 0.0, 0.0, "entry_price_invalid"
    notional = min(max(fl.default_notional_usdt, fl.min_notional_usdt), fl.max_notional_usdt)
    if notional < fl.min_notional_usdt:
        return notional, 0.0, "notional_below_min"
    if notional > fl.max_notional_usdt:
        return notional, 0.0, "notional_above_max"
    quantity = notional / float(entry_price)
    if quantity <= 0:
        return notional, 0.0, "quantity_non_positive"
    return notional, quantity, None


# ── Main decision ───────────────────────────────────────────────────────────


def _select_route(fl: PremiumFastlaneSettings, *, live_armed: bool) -> str | None:
    """Pick the highest-priority non-live route. Live is never auto-selected
    here — even when armed, the routing list drives paper/testnet/demo. Returns
    None when no usable route is configured (a hard block, §5)."""
    for route in fl.routing_priority_list:
        if route in _NON_LIVE_ROUTES:
            if route == "simulated_exchange" and not fl.simulated_exchange_fallback:
                continue
            return route
    return None


def should_route_premium_fastlane(
    envelope: dict[str, Any],
    settings: AppSettings,
    *,
    now: datetime | None = None,
) -> FastlaneDecision:
    """Central fastlane routing decision for one envelope record (§5)."""
    fl = settings.premium_fastlane
    live_armed = live_fastlane_armed(settings)
    live_protected = not live_armed

    if not fl.enabled:
        return _blocked("fastlane_disabled", live_protected=live_protected)

    active, win_reason, _end, _days = fastlane_window(fl, now=now)
    if not active:
        return _blocked(win_reason or "fastlane_window_expired", live_protected=live_protected)

    if not is_authorized_premium_fastlane_source(envelope):
        return _blocked("not_premium_fastlane_source", live_protected=live_protected)

    missing, passed = check_required_fields(envelope, fl)
    if missing:
        return _blocked(
            "missing_required:" + ",".join(sorted(missing)),
            live_protected=live_protected,
        )

    route = _select_route(fl, live_armed=live_armed)
    if route is None:
        return _blocked("no_routing_target", live_protected=live_protected)

    bypassed = [
        gate
        for gate, flag in (
            ("manual_approval", fl.bypass_manual_approval),
            ("source_allowlist", fl.bypass_source_allowlist),
            ("entry_mode_for_paper", fl.bypass_entry_mode_for_paper),
            ("risk_quality_gates", fl.bypass_risk_quality_gates),
            ("source_quality_gates", fl.bypass_source_quality_gates),
            ("priority_tier_gates", fl.bypass_priority_tier_gates),
            ("forward_precision_gates", fl.bypass_forward_precision_gates),
        )
        if flag
    ]

    return FastlaneDecision(
        enabled=True,
        route=route,
        reason=None,
        bypassed_gates=bypassed,
        required_guards_passed=passed,
        live_protected=live_protected,
    )


def fastlane_status(settings: AppSettings, *, now: datetime | None = None) -> dict[str, Any]:
    """Config-level fastlane truth for the runtime endpoint + dashboard (§18).

    Unlike ``should_route_premium_fastlane`` (per-envelope), this answers "is the
    fastlane armed and active right now, and does it override the classic
    premium-paper block?" without needing a concrete signal.
    """
    fl = settings.premium_fastlane
    active, win_reason, end, days_remaining = fastlane_window(fl, now=now)
    live_armed = live_fastlane_armed(settings)
    bypassed = [
        gate
        for gate, flag in (
            ("manual_approval", fl.bypass_manual_approval),
            ("source_allowlist", fl.bypass_source_allowlist),
            ("entry_mode_for_paper", fl.bypass_entry_mode_for_paper),
            ("risk_quality_gates", fl.bypass_risk_quality_gates),
            ("source_quality_gates", fl.bypass_source_quality_gates),
            ("priority_tier_gates", fl.bypass_priority_tier_gates),
            ("forward_precision_gates", fl.bypass_forward_precision_gates),
        )
        if flag
    ]
    route = _select_route(fl, live_armed=live_armed) if (fl.enabled and active) else None
    # The fastlane overrides the classic premium-paper block when it is enabled,
    # inside its window, routable, and configured to bypass entry-mode for paper.
    overrides_classic_block = bool(
        fl.enabled and active and route is not None and fl.bypass_entry_mode_for_paper
    )
    return {
        "enabled": fl.enabled,
        "active": bool(fl.enabled and active),
        "window_reason": win_reason,
        "mode": fl.mode,
        "route": route or "blocked",
        "duration_days": fl.duration_days,
        "start_date": fl.start_date or None,
        "end_date": end.isoformat() if end is not None else None,
        "days_remaining": days_remaining,
        "bypassed_gates": bypassed,
        "live_armed": live_armed,
        "live_protected": not live_armed,
        "overrides_classic_block": overrides_classic_block,
        "default_notional_usdt": fl.default_notional_usdt,
        "min_notional_usdt": fl.min_notional_usdt,
        "max_notional_usdt": fl.max_notional_usdt,
        "max_leverage": fl.max_leverage,
        "max_open_positions": fl.max_open_positions,
        "paper_equity_usdt": fl.paper_equity_usdt,
        "observe_only_metrics": list(OBSERVE_ONLY_METRICS),
    }


__all__ = [
    "OBSERVE_ONLY_METRICS",
    "FastlaneDecision",
    "check_required_fields",
    "fastlane_status",
    "fastlane_window",
    "is_authorized_premium_fastlane_source",
    "live_fastlane_armed",
    "resolve_leverage",
    "resolve_notional",
    "should_route_premium_fastlane",
]
