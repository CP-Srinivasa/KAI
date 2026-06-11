"""Entry-policy SSOT — explicit, operator-readable entry modes (Sprint S3, #181).

One place that answers, per signal route, the question: *may this route open a
NEW risk-increasing position under the active ``EXECUTION_ENTRY_MODE`` — and
under which volume limits?*

Background (#181): ``entry_mode=disabled`` had grown three parallel override
mechanisms (fastlane two-flag cascade #179, premium three-arm ack #208,
real-analysis three-arm ack #209). Each is individually fail-closed, but the
combined semantics were only readable by auditing three modules. This module
consolidates them behind two NEW explicit modes and turns the legacy acks into
**migration aliases**:

  - ``EXECUTION_ENTRY_MODE=paper_premium_limited`` — ONLY the premium paper
    route is open (with default volume limits); autonomous loop, learning
    feeder and fastlane stay closed.
  - ``EXECUTION_ENTRY_MODE=paper_learning`` — premium paper + real-analysis
    paper-learning routes are open (with default volume limits); autonomous
    loop and fastlane stay closed.

Migration-alias contract (Pi-neutrality): under ``disabled`` the resolution
delegates byte-for-byte to the existing three-arm override functions
(:func:`premium_paper_entry_disabled_override`,
:func:`real_analysis_paper_entry_disabled_override`). A Pi that today runs
``disabled`` + armed acks keeps the exact same behaviour after this change —
the acks simply surface as ``alias_used`` in the policy verdict so the audit
trail shows they are the legacy spelling of the new modes.

Fail-closed rules:
  - Contradictory configurations (e.g. fastlane enabled while a limited paper
    mode is active) refuse ALL routes (#181 §7).
  - A route is open only when the mode opens it AND its master enable holds.
  - Limits: 0 == unlimited for any axis. The two new modes inject conservative
    DEFAULT limits where the operator has not configured explicit ones
    (#181 §5); legacy modes never get implicit limits (behaviour-neutral).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.enums import EntryMode

# Sprint S7 (D-234): the opening-fill predicate is the SHARED truth in
# paper_entry_accounting — the route limiter and the daily cap can never
# disagree about what counts as an entry, because there is only one copy.
from app.execution.paper_entry_accounting import is_opening_fill as _is_opening_fill

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.core.settings import AppSettings

logger = logging.getLogger(__name__)


class EntryRoute(StrEnum):
    """The signal routes that can open NEW risk-increasing exposure."""

    AUTONOMOUS_LOOP = "autonomous_loop"
    PREMIUM_PAPER = "premium_paper"
    REAL_ANALYSIS_PAPER = "real_analysis_paper"
    PREMIUM_FASTLANE = "premium_fastlane"


# Audit `source` prefixes per route — used by the route-usage limiter to
# attribute opening fills in artifacts/paper_execution_audit.jsonl.
ROUTE_SOURCE_PREFIXES: dict[EntryRoute, tuple[str, ...]] = {
    EntryRoute.PREMIUM_PAPER: ("telegram_premium",),
    EntryRoute.REAL_ANALYSIS_PAPER: ("real_analysis",),
}


@dataclass(frozen=True)
class RouteLimits:
    """Volume limits for one route (#181 §5). 0 == unlimited per axis."""

    max_trades_per_hour: int = 0
    max_notional_per_day_usd: float = 0.0
    max_open_positions: int = 0

    @property
    def any_active(self) -> bool:
        return (
            self.max_trades_per_hour > 0
            or self.max_notional_per_day_usd > 0
            or self.max_open_positions > 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_trades_per_hour": self.max_trades_per_hour,
            "max_notional_per_day_usd": self.max_notional_per_day_usd,
            "max_open_positions": self.max_open_positions,
        }


# Built-in conservative defaults for the NEW explicit modes only (#181 §5).
# Legacy modes (disabled-alias, paper, probe, live_*) never get implicit
# limits — explicit env configuration is required there (behaviour-neutral).
DEFAULT_PREMIUM_ROUTE_LIMITS = RouteLimits(
    max_trades_per_hour=6,
    max_notional_per_day_usd=10_000.0,
    max_open_positions=10,
)
DEFAULT_LEARNING_ROUTE_LIMITS = RouteLimits(
    max_trades_per_hour=6,
    max_notional_per_day_usd=5_000.0,
    max_open_positions=10,
)

# alias_used markers (audit contract).
ALIAS_PREMIUM_THREE_ARM = "premium_three_arm_ack"
ALIAS_REAL_ANALYSIS_THREE_ARM = "real_analysis_three_arm_ack"

_LIMITED_MODES = (EntryMode.PAPER_PREMIUM_LIMITED, EntryMode.PAPER_LEARNING)


@dataclass(frozen=True)
class RouteVerdict:
    """Per-route resolution: open or not, why, via which alias, which limits."""

    route: EntryRoute
    allowed: bool
    reason_code: str | None = None
    alias_used: str | None = None
    limits: RouteLimits | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "alias_used": self.alias_used,
            "limits": self.limits.to_dict() if self.limits else None,
        }


@dataclass(frozen=True)
class EntryPolicy:
    """Resolved entry policy for the active settings snapshot."""

    mode: EntryMode
    verdicts: Mapping[EntryRoute, RouteVerdict]
    contradictions: tuple[str, ...] = ()

    def verdict(self, route: EntryRoute) -> RouteVerdict:
        return self.verdicts[route]

    def allows(self, route: EntryRoute) -> bool:
        return self.verdicts[route].allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "contradictions": list(self.contradictions),
            "routes": {r.value: v.to_dict() for r, v in self.verdicts.items()},
        }


def detect_contradictions(settings: AppSettings) -> tuple[str, ...]:
    """#181 §7 preflight: configurations that contradict the active mode.

    Any hit refuses ALL routes fail-closed (a contradictory config must never
    silently resolve to the more permissive reading). Under legacy modes the
    pre-existing override semantics apply unchanged — the historical
    ``disabled`` + fastlane two-flag arming is governed by
    :func:`app.execution.premium_fastlane.fastlane_entry_mode_override`, not
    re-judged here (Pi-neutrality).
    """
    mode = settings.execution.entry_mode
    found: list[str] = []
    if mode in _LIMITED_MODES:
        if settings.premium_fastlane.enabled:
            found.append("fastlane_enabled_in_limited_paper_mode")
        if settings.premium.live_execution_enabled:
            found.append("premium_live_execution_enabled_in_limited_paper_mode")
        if getattr(settings.premium_fastlane, "live_enabled", False):
            found.append("fastlane_live_enabled_in_limited_paper_mode")
    return tuple(found)


def _explicit_limits(max_trades: int, max_notional: float, max_open: int) -> RouteLimits | None:
    limits = RouteLimits(
        max_trades_per_hour=max_trades,
        max_notional_per_day_usd=max_notional,
        max_open_positions=max_open,
    )
    return limits if limits.any_active else None


def _premium_route_limits(settings: AppSettings, mode: EntryMode) -> RouteLimits | None:
    explicit = _explicit_limits(
        settings.premium.paper_route_max_trades_per_hour,
        settings.premium.paper_route_max_notional_per_day_usd,
        settings.premium.paper_route_max_open_positions,
    )
    if explicit is not None:
        return explicit
    return DEFAULT_PREMIUM_ROUTE_LIMITS if mode in _LIMITED_MODES else None


def _learning_route_limits(settings: AppSettings, mode: EntryMode) -> RouteLimits | None:
    explicit = _explicit_limits(
        settings.real_analysis_paper.paper_route_max_trades_per_hour,
        settings.real_analysis_paper.paper_route_max_notional_per_day_usd,
        settings.real_analysis_paper.paper_route_max_open_positions,
    )
    if explicit is not None:
        return explicit
    return DEFAULT_LEARNING_ROUTE_LIMITS if mode is EntryMode.PAPER_LEARNING else None


def resolve_entry_policy(settings: AppSettings) -> EntryPolicy:
    """Resolve the per-route entry policy for the active settings snapshot.

    Pure function of the settings object: no disk, no network, no engine.
    Callers enforce the verdicts at their choke points (bridge / run_cycle /
    feeder) and write the verdict into their audit records.
    """
    # Local imports: the override modules import settings sentinels; importing
    # them lazily keeps module import order independent (no cycle risk).
    from app.execution.premium_fastlane import premium_paper_entry_disabled_override
    from app.execution.real_analysis_paper import real_analysis_paper_entry_disabled_override

    mode = settings.execution.entry_mode
    contradictions = detect_contradictions(settings)

    if contradictions:
        # Fail-closed: a contradictory configuration opens NOTHING (#181 §7).
        reason = "entry_policy_contradiction:" + ",".join(contradictions)
        refused = {
            route: RouteVerdict(route=route, allowed=False, reason_code=reason)
            for route in EntryRoute
        }
        logger.warning("[entry-policy] contradictory config — all routes refused: %s", reason)
        return EntryPolicy(mode=mode, verdicts=refused, contradictions=contradictions)

    verdicts: dict[EntryRoute, RouteVerdict] = {}

    # ── autonomous loop ────────────────────────────────────────────────────
    if mode.allows_autonomous_loop_entry:
        verdicts[EntryRoute.AUTONOMOUS_LOOP] = RouteVerdict(
            route=EntryRoute.AUTONOMOUS_LOOP, allowed=True
        )
    else:
        reason = (
            "entry_mode_disabled"
            if mode is EntryMode.DISABLED
            else f"autonomous_loop_closed_in_{mode.value}"
        )
        verdicts[EntryRoute.AUTONOMOUS_LOOP] = RouteVerdict(
            route=EntryRoute.AUTONOMOUS_LOOP, allowed=False, reason_code=reason
        )

    # ── premium paper (classic bridge) ─────────────────────────────────────
    if not settings.premium.paper_execution_enabled:
        verdicts[EntryRoute.PREMIUM_PAPER] = RouteVerdict(
            route=EntryRoute.PREMIUM_PAPER,
            allowed=False,
            reason_code="premium_paper_execution_disabled",
        )
    elif mode is EntryMode.DISABLED:
        allowed, refusal = premium_paper_entry_disabled_override(settings)
        verdicts[EntryRoute.PREMIUM_PAPER] = RouteVerdict(
            route=EntryRoute.PREMIUM_PAPER,
            allowed=allowed,
            reason_code=refusal,
            alias_used=ALIAS_PREMIUM_THREE_ARM if allowed else None,
            limits=_premium_route_limits(settings, mode) if allowed else None,
        )
    else:
        # paper / probe / live_* (legacy: paper bridge open when premium paper
        # enabled) and the two new modes (premium route explicitly open).
        verdicts[EntryRoute.PREMIUM_PAPER] = RouteVerdict(
            route=EntryRoute.PREMIUM_PAPER,
            allowed=True,
            limits=_premium_route_limits(settings, mode),
        )

    # ── real-analysis paper-learning feeder ────────────────────────────────
    if not settings.real_analysis_paper.enabled:
        verdicts[EntryRoute.REAL_ANALYSIS_PAPER] = RouteVerdict(
            route=EntryRoute.REAL_ANALYSIS_PAPER,
            allowed=False,
            reason_code="real_analysis_paper_disabled",
        )
    elif mode is EntryMode.PAPER_LEARNING:
        verdicts[EntryRoute.REAL_ANALYSIS_PAPER] = RouteVerdict(
            route=EntryRoute.REAL_ANALYSIS_PAPER,
            allowed=True,
            limits=_learning_route_limits(settings, mode),
        )
    elif mode is EntryMode.PAPER_PREMIUM_LIMITED:
        verdicts[EntryRoute.REAL_ANALYSIS_PAPER] = RouteVerdict(
            route=EntryRoute.REAL_ANALYSIS_PAPER,
            allowed=False,
            reason_code="learning_route_closed_in_paper_premium_limited",
        )
    else:
        # disabled AND legacy paper/probe/live modes: the feeder has always
        # been gated by the three-arm ack regardless of entry_mode — keep that
        # (behaviour-neutral migration alias).
        allowed, refusal = real_analysis_paper_entry_disabled_override(settings)
        verdicts[EntryRoute.REAL_ANALYSIS_PAPER] = RouteVerdict(
            route=EntryRoute.REAL_ANALYSIS_PAPER,
            allowed=allowed,
            reason_code=refusal,
            alias_used=ALIAS_REAL_ANALYSIS_THREE_ARM if allowed else None,
            limits=_learning_route_limits(settings, mode) if allowed else None,
        )

    # ── premium fastlane ───────────────────────────────────────────────────
    # Operator decision (#179/#181): fastlane stays retired. In the new
    # limited modes it is hard-refused by policy; in legacy modes the bridge
    # keeps its pre-existing two-flag override gate (not re-judged here).
    if mode in _LIMITED_MODES:
        verdicts[EntryRoute.PREMIUM_FASTLANE] = RouteVerdict(
            route=EntryRoute.PREMIUM_FASTLANE,
            allowed=False,
            reason_code="fastlane_closed_in_limited_paper_mode",
        )
    else:
        verdicts[EntryRoute.PREMIUM_FASTLANE] = RouteVerdict(
            route=EntryRoute.PREMIUM_FASTLANE,
            allowed=False,
            reason_code="fastlane_not_policy_managed",
        )

    return EntryPolicy(mode=mode, verdicts=verdicts, contradictions=())


# ── Route-usage limiter (#181 §5) ───────────────────────────────────────────


@dataclass(frozen=True)
class RouteUsage:
    """Measured recent usage of one route (from the paper-execution audit)."""

    trades_last_hour: int = 0
    notional_today_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trades_last_hour": self.trades_last_hour,
            "notional_today_usd": round(self.notional_today_usd, 2),
        }


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def measure_route_usage(
    audit_path: Path,
    *,
    source_prefixes: Sequence[str],
    now: datetime | None = None,
) -> RouteUsage:
    """Measure opening fills attributed to a route from the audit JSONL.

    Attribution: ``paper_trade_label`` rows map ``order_id`` → ``source_name``;
    an opening ``order_filled`` row counts for the route when its label (or its
    own ``source`` field as fallback) starts with one of ``source_prefixes``.

    Read-only single pass. A missing/unreadable file yields zero usage — the
    *limits* stay fail-closed at the verdict layer; the usage measurement is a
    count, and an unreadable audit cannot invent past trades.
    """
    now_utc = now or datetime.now(UTC)
    hour_floor = now_utc - timedelta(hours=1)
    today_prefix = now_utc.date().isoformat()
    prefixes = tuple(source_prefixes)

    if not audit_path.exists():
        return RouteUsage()

    labels: dict[str, str] = {}
    fills: list[dict[str, Any]] = []
    try:
        with audit_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                event_type = record.get("event_type")
                if event_type == "paper_trade_label":
                    order_id = record.get("order_id")
                    source = record.get("source_name") or record.get("feed_source")
                    if isinstance(order_id, str) and isinstance(source, str):
                        labels[order_id] = source
                elif _is_opening_fill(record):
                    ts = record.get("timestamp_utc")
                    if isinstance(ts, str) and ts.startswith(today_prefix):
                        fills.append(record)
    except OSError as exc:
        logger.warning("[entry-policy] route-usage read failed: %s", exc)
        return RouteUsage()

    trades_last_hour = 0
    notional_today = 0.0
    for record in fills:
        order_id = record.get("order_id")
        source = labels.get(order_id) if isinstance(order_id, str) else None
        if source is None:
            raw_source = record.get("source")
            source = raw_source if isinstance(raw_source, str) else ""
        if not source.startswith(prefixes):
            continue
        try:
            notional_today += float(record.get("quantity") or 0) * float(
                record.get("fill_price") or 0
            )
        except (TypeError, ValueError):
            pass
        ts = _parse_ts(record.get("timestamp_utc"))
        if ts is not None and ts >= hour_floor:
            trades_last_hour += 1
    return RouteUsage(trades_last_hour=trades_last_hour, notional_today_usd=notional_today)


def check_route_limits(
    *,
    route: EntryRoute,
    limits: RouteLimits | None,
    audit_path: Path,
    current_open_positions: int | None = None,
    now: datetime | None = None,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Enforce a route's volume limits (#181 §5).

    Returns ``(ok, refusal_detail, snapshot)``. ``refusal_detail`` names the
    violated axis; ``snapshot`` carries the measured usage + limits for the
    audit record. ``limits=None`` or all-zero limits → always ok (unlimited).

    ``max_open_positions`` is checked against the caller-provided GLOBAL open
    count (engine truth) — a route cap can therefore only tighten, never widen,
    the portfolio-level ``risk.limits.max_open_positions`` gate that runs after
    it.
    """
    if limits is None or not limits.any_active:
        return True, None, {"limits": None}

    prefixes = ROUTE_SOURCE_PREFIXES.get(route, ())
    usage = measure_route_usage(audit_path, source_prefixes=prefixes, now=now)
    snapshot: dict[str, Any] = {
        "route": route.value,
        "limits": limits.to_dict(),
        "usage": usage.to_dict(),
        "current_open_positions": current_open_positions,
    }
    if 0 < limits.max_trades_per_hour <= usage.trades_last_hour:
        return False, "max_trades_per_hour", snapshot
    if 0 < limits.max_notional_per_day_usd <= usage.notional_today_usd:
        return False, "max_notional_per_day_usd", snapshot
    if (
        limits.max_open_positions > 0
        and current_open_positions is not None
        and current_open_positions >= limits.max_open_positions
    ):
        return False, "max_open_positions", snapshot
    return True, None, snapshot


__all__ = [
    "ALIAS_PREMIUM_THREE_ARM",
    "ALIAS_REAL_ANALYSIS_THREE_ARM",
    "DEFAULT_LEARNING_ROUTE_LIMITS",
    "DEFAULT_PREMIUM_ROUTE_LIMITS",
    "ROUTE_SOURCE_PREFIXES",
    "EntryPolicy",
    "EntryRoute",
    "RouteLimits",
    "RouteUsage",
    "RouteVerdict",
    "check_route_limits",
    "detect_contradictions",
    "measure_route_usage",
    "resolve_entry_policy",
]
