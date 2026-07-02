"""Fail-closed promotion / re-enable gate (Bleed-Breaker enforcement half).

Operator-Auftrag 2026-06-03 (Pre-Re-Enable-Blocker #4, enforcement half). The
detection half (``app/observability/position_risk.py``) can *see* an open-position
bleed; this module *blocks* a risk-increasing ``EntryMode`` promotion while that
bleed (or unknown position data) is unresolved.

Semantics — this is a **promotion / re-enable stop, NOT a trading stop**:

* ALLOWED, always: read-only diagnostics, exits, risk reductions, de-risking
  transitions (target rank <= current rank, e.g. ``probe -> disabled``).
* BLOCKED (-> ``manual_review_required``): risk-INCREASING transitions
  (``disabled -> paper -> probe -> live_limited -> live_normal``) while any open
  position is ``risk_open`` / ``data_unknown`` / source-stale, the aggregate
  unrealized PnL is bleeding, or the position-risk artifact is missing.

Fail-closed (SENTR posture): on missing artifact, unavailable snapshot, unknown
position data or stale source the gate **blocks** rather than waving through.
It never auto-closes and never changes execution state — it only returns a
decision the caller (CLI / operator promotion path) must honour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.enums import EntryMode
from app.observability.position_risk import RISK_OPEN, RISK_UNKNOWN

# Reason codes (operator-specified).
PROMOTION_BLOCKED_RISK_OPEN = "PROMOTION_BLOCKED_RISK_OPEN"
PROMOTION_BLOCKED_POSITION_DATA_UNKNOWN = "PROMOTION_BLOCKED_POSITION_DATA_UNKNOWN"
PROMOTION_BLOCKED_POSITION_SOURCE_STALE = "PROMOTION_BLOCKED_POSITION_SOURCE_STALE"
PROMOTION_BLOCKED_UNREALIZED_BLEED = "PROMOTION_BLOCKED_UNREALIZED_BLEED"
PROMOTION_BLOCKED_MISSING_ARTIFACT = "PROMOTION_BLOCKED_MISSING_ARTIFACT"

STATUS_ALLOWED = "allowed"
STATUS_MANUAL_REVIEW = "manual_review_required"

# V2-Befund 2026-06-12: with a 0.0 threshold the bleed check measured "any open
# position is a few cents red", not bleed — the paper engine fills every entry
# with +5bps adverse slippage, so a fresh position starts negative by
# construction (observed block: -23.93 USD on ~7.8k USD open notional, -0.31%).
# 75 USD ~= 1% of the typical open notional and sits well above the worst-case
# entry slippage+fee drag (~15bps x 10 open route-limited positions), while a
# genuinely bleeding book still trips it. Per-position losses >= 1% keep
# blocking independently via RISK_OPEN (loss_threshold_pct).
DEFAULT_BLEED_USD_THRESHOLD = 75.0

# Ladder rank (least -> most permissive). A promotion is risk-increasing when
# the target rank is strictly greater than the current rank.
#
# D-233 follow-up fix (2026-06-11): derived from the EntryMode declaration
# order instead of a hand-maintained copy — the enum IS the ladder (S3
# deliberately inserted the limited paper modes between DISABLED and PAPER).
# The hand-copied tuple missed paper_premium_limited/paper_learning, so
# ``_rank`` raised ValueError for the very mode the Pi now runs; deriving it
# makes a future mode unrankable only if it is missing from the enum itself,
# which the invariant test below pins.
_LADDER: tuple[EntryMode, ...] = tuple(EntryMode)


def _rank(mode: EntryMode) -> int:
    return _LADDER.index(mode)


def is_risk_increasing(current: EntryMode, target: EntryMode) -> bool:
    """True when ``target`` opens more / higher-risk entries than ``current``."""
    return _rank(target) > _rank(current)


@dataclass(frozen=True)
class PromotionGateDecision:
    """Outcome of a promotion-gate evaluation. ``allowed`` is advisory-binding:
    the caller must not promote ``entry_mode`` when ``allowed`` is False."""

    allowed: bool
    status: str
    current_mode: EntryMode
    target_mode: EntryMode
    risk_increasing: bool
    reason_codes: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_type": "promotion_gate_decision",
            "evaluated_at": datetime.now(UTC).isoformat(),
            "allowed": self.allowed,
            "status": self.status,
            "current_mode": self.current_mode.value,
            "target_mode": self.target_mode.value,
            "risk_increasing": self.risk_increasing,
            "reason_codes": list(self.reason_codes),
            "detail": self.detail,
        }


def evaluate_promotion(
    current_mode: EntryMode,
    target_mode: EntryMode,
    risk_report: dict[str, Any] | None,
    *,
    bleed_usd_threshold: float = DEFAULT_BLEED_USD_THRESHOLD,
) -> PromotionGateDecision:
    """Decide whether promoting ``current_mode`` -> ``target_mode`` is allowed.

    ``risk_report`` is the dict from
    ``position_risk.build_positions_risk_snapshot``. ``bleed_usd_threshold`` is the
    aggregate-unrealized-loss magnitude (USD) that trips an UNREALIZED_BLEED block
    even if no single position crossed its own per-position threshold.
    """
    risk_increasing = is_risk_increasing(current_mode, target_mode)

    # De-risking and lateral transitions are never gated — de-risking must always
    # be possible (exits, ->disabled, ->lower mode).
    if not risk_increasing:
        return PromotionGateDecision(
            allowed=True,
            status=STATUS_ALLOWED,
            current_mode=current_mode,
            target_mode=target_mode,
            risk_increasing=False,
            detail="non-risk-increasing transition — not gated",
        )

    reasons: list[str] = []

    # Fail-closed: no artifact / unavailable snapshot -> block.
    if not isinstance(risk_report, dict) or not risk_report.get("available", False):
        reasons.append(PROMOTION_BLOCKED_MISSING_ARTIFACT)
        return PromotionGateDecision(
            allowed=False,
            status=STATUS_MANUAL_REVIEW,
            current_mode=current_mode,
            target_mode=target_mode,
            risk_increasing=True,
            reason_codes=reasons,
            detail="position-risk artifact missing or snapshot unavailable (fail-closed)",
        )

    positions = risk_report.get("positions") or []
    overall = risk_report.get("overall_risk_status")

    has_open = overall == RISK_OPEN or any(
        isinstance(p, dict) and p.get("risk_status") == RISK_OPEN for p in positions
    )
    has_unknown = overall == RISK_UNKNOWN or any(
        isinstance(p, dict) and p.get("risk_status") == RISK_UNKNOWN for p in positions
    )
    has_stale = any(
        isinstance(p, dict)
        and (p.get("market_data_stale") or not p.get("market_data_available", True))
        for p in positions
    )

    if has_open:
        reasons.append(PROMOTION_BLOCKED_RISK_OPEN)
    if has_unknown:
        reasons.append(PROMOTION_BLOCKED_POSITION_DATA_UNKNOWN)
    if has_stale:
        reasons.append(PROMOTION_BLOCKED_POSITION_SOURCE_STALE)

    total_unrealized = risk_report.get("total_unrealized_pnl_usd")
    is_bleeding = (
        isinstance(total_unrealized, (int, float))
        and total_unrealized < 0
        and total_unrealized <= -abs(bleed_usd_threshold)
    )
    if is_bleeding:
        reasons.append(PROMOTION_BLOCKED_UNREALIZED_BLEED)

    if reasons:
        return PromotionGateDecision(
            allowed=False,
            status=STATUS_MANUAL_REVIEW,
            current_mode=current_mode,
            target_mode=target_mode,
            risk_increasing=True,
            reason_codes=reasons,
            detail="risk-increasing promotion blocked pending operator review",
        )

    return PromotionGateDecision(
        allowed=True,
        status=STATUS_ALLOWED,
        current_mode=current_mode,
        target_mode=target_mode,
        risk_increasing=True,
        detail=(
            "no open-position risk — promotion permitted "
            "(still needs edge/operator sign-off elsewhere)"
        ),
    )
