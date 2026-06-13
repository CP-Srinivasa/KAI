"""Cohort- and forward-edge diagnostics (Sprint C, 2026-06-01 /goal).

Purpose
-------
Measure, defensibly, whether the entry loop has *cost-adjusted* edge, and
separate two failure modes the operator keeps confusing:

  - "the ENTRY SIGNAL is bad"      -> forward returns right after entry are flat/negative
  - "entry is fine, EXIT/HOLDING/RE-ENTRY is bad" -> good forward return but negative realised

This module is **read-only on the trading path**. It never touches run_cycle,
risk, or the engine. It reads the paper execution audit stream and the
CostModel, and produces a typed, JSON-serialisable report plus a human-readable
table.

Hard contracts (kai-master-coding-regeln)
------------------------------------------
- Closed-only realised PnL is STRICTLY separated from open-position
  mark-to-market. They are never summed (this is the +433-vs--283 bug, see
  NEO-F-302). Open positions at the cutoff are reported in their own bucket.
- All costs come from the SAME ``CostModel`` the engine charges (Sprint B
  single-source). The gate (Sprint D) consumes the exact same ``net_bps``.
- Winrate alone is never the verdict. We report ``p_mu_net_positive`` — the
  probability that the mean net edge is > 0 — via bootstrap resampling. Below a
  minimum sample size it is honestly ``None`` ("insufficient"), never invented.
- Forward returns require historical minute bars. If they are not available for
  past entries, coverage is reported as ``0/N`` with an explicit reason. We do
  not fabricate a single forward number. A prospective-capture path is provided
  (record entry price now, sample later) without altering the loop.

Regime cohort
-------------
The ``position_closed`` audit event does not carry a per-trade regime label
today. If a ``regime`` (or ``regime_state``) key is present on a trade record we
use it; otherwise the regime cohort is ``"unknown"`` and marked as such. We do
not back-derive a regime from price history here (that would be a separate,
fallible inference and is out of Sprint C scope).
"""

from __future__ import annotations

import json
import logging
import math
import random
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.execution.cost_model import CostModel
from app.learning.bayes_quarantine import quarantine_reason
from app.storage.jsonl_io import read_jsonl_tolerant

logger = logging.getLogger(__name__)

# A bootstrap on fewer than this many trades is not statistically meaningful;
# P(mu_net > 0) is reported as None ("insufficient") below it. Conservative
# default — the operator can lower it but then must read the result as weak.
MIN_SAMPLE_FOR_P = 8
_DEFAULT_BOOTSTRAP_N = 5000
# OPERATOR-SIGN-OFF PARAMETER (Goal 2026-06-01, B). A realised single-trade
# round-trip whose |exit/entry - 1| exceeds this is treated as an off-market /
# corrupt print (e.g. the 2026-05-26 ETH close at $3260 vs real $1960-$2100) and
# excluded from the cost-adjusted edge, SYMMETRICALLY (both directions — never
# used to scrub losses). Mirrors the #98 close-circuit-breaker's sanity logic in
# the reporting layer. Default 0.40 (40%): single-bar |move| > 40% on the majors
# KAI trades is almost certainly a bad price, not a real round-trip; legitimate
# intraday >40% round-trips are vanishingly rare. Lower → also drops large but
# plausible real moves (biases edge); higher → lets corrupt prints back in. 0
# disables the guard (forensic signatures in bayes_quarantine still apply).
DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD = 0.40

# B-Fix 2026-06-13 (Operator): every premium paper trade BEFORE this cutoff was
# sized 1x because the stated signal leverage was audit-only
# (leverage_mode="paper_audit_only"). Their PnL is systematically too small and
# would drag down Premium-EV / forward hit-rate / premium-bonus / re-entry-gate.
# A (#232, premium.apply_signal_leverage) went live at this instant, so premium
# closes timestamped before it are excluded from edge/EV metrics (audit rows are
# NEVER deleted — append-only integrity; they are skipped + counted). Set to ""
# to disable the exclusion. Only premium sources are affected; the autonomous
# generator / real_analysis trades were never leverage-distorted.
PREMIUM_LEVERAGE_CUTOFF_UTC = "2026-06-13T14:09:49Z"
# Forward horizons in minutes (C2). Side-adjusted AND cost-adjusted.
FORWARD_HORIZONS_MIN: tuple[int, ...] = (1, 5, 15, 60)
_SHADOW_FORWARD_BPS_KEYS: dict[int, str] = {
    1: "fwd_60s_bps",
    5: "fwd_300s_bps",
    15: "fwd_900s_bps",
    60: "fwd_3600s_bps",
}


# --- core math (pure, IO-free, unit-tested with known numbers) ----------------


def side_adjusted_return_bps(entry_price: float, exit_price: float, position_side: str) -> float:
    """Side-adjusted price return in basis points.

    long:  (exit/entry - 1) * 10000
    short: negated -> profit when price falls.

    Raises ValueError on non-positive prices (a phantom/corrupt record must not
    silently produce a fake return).
    """
    if entry_price <= 0 or exit_price <= 0:
        raise ValueError(f"non-positive price: entry={entry_price} exit={exit_price}")
    raw_bps = (exit_price / entry_price - 1.0) * 10_000.0
    side = (position_side or "long").strip().lower()
    if side == "short":
        return -raw_bps
    return raw_bps


def bootstrap_p_mean_positive(
    values: Sequence[float],
    *,
    n_resamples: int = _DEFAULT_BOOTSTRAP_N,
    min_sample: int = MIN_SAMPLE_FOR_P,
    seed: int | None = 1337,
) -> float | None:
    """P(mean(values) > 0) by bootstrap resampling of the mean.

    Resamples ``values`` with replacement ``n_resamples`` times and returns the
    fraction of resample means strictly greater than zero. This is the answer to
    "is the cost-adjusted edge plausibly positive?", which winrate cannot give.

    Returns ``None`` when ``len(values) < min_sample`` — honest insufficiency,
    not a fabricated probability.
    """
    vals = [float(v) for v in values]
    n = len(vals)
    if n < min_sample:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(n_resamples):
        s = 0.0
        for _ in range(n):
            s += vals[rng.randrange(n)]
        if s / n > 0.0:
            positive += 1
    return positive / n_resamples


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


# --- parsed audit records ------------------------------------------------------


@dataclass(frozen=True)
class ClosedTrade:
    """One realised round-trip from a ``position_closed`` audit event."""

    symbol: str
    position_side: str
    entry_price: float
    exit_price: float
    quantity: float
    reason: str
    trade_pnl_usd: float
    fee_usd: float
    timestamp_utc: str
    regime: str = "unknown"
    # NEO-P-20260603-001: signal-source attribution. "" / "unknown" for legacy
    # rows persisted before the attribution fields existed.
    signal_source: str = "unknown"

    @property
    def notional_usd(self) -> float:
        return abs(self.entry_price * self.quantity)

    @property
    def day(self) -> str:
        """UTC date (YYYY-MM-DD) of the close, or 'unknown' if unparseable."""
        try:
            return datetime.fromisoformat(self.timestamp_utc).astimezone(UTC).date().isoformat()
        except (ValueError, TypeError):
            return "unknown"


@dataclass(frozen=True)
class OpenPosition:
    """An open position at the cutoff, for mark-to-market — kept SEPARATE."""

    symbol: str
    position_side: str
    entry_price: float
    quantity: float
    opened_at: str

    @property
    def notional_usd(self) -> float:
        return abs(self.entry_price * self.quantity)


# --- per-trade cost-adjusted edge ----------------------------------------------


@dataclass(frozen=True)
class TradeEdge:
    """Cost-decomposed realised edge for a single closed trade."""

    symbol: str
    timestamp_utc: str
    gross_bps: float
    fee_bps: float
    spread_bps: float
    slippage_bps: float
    net_bps: float
    trade_pnl_usd: float
    notional_usd: float
    reason: str
    regime: str
    signal_source: str = "unknown"


def compute_trade_edge(
    trade: ClosedTrade,
    cost_model: CostModel,
    *,
    venue: str = "paper",
    safety_margin_bps: float = 0.0,
) -> TradeEdge:
    """Decompose one closed trade into gross / fee / spread / slippage / net bps.

    net_bps uses CostModel.net_edge_bps on the realised side-adjusted return, so
    the cost charged here is byte-for-byte the cost the engine and the Sprint D
    gate use. No second fee formula lives in this module.
    """
    gross = side_adjusted_return_bps(trade.entry_price, trade.exit_price, trade.position_side)
    rt = cost_model.round_trip(venue=venue)
    net = cost_model.net_edge_bps(
        venue=venue,
        side_adjusted_return_bps=gross,
        safety_margin_bps=safety_margin_bps,
    )
    return TradeEdge(
        symbol=trade.symbol,
        timestamp_utc=trade.timestamp_utc,
        gross_bps=gross,
        fee_bps=rt.round_trip_fee_bps,
        spread_bps=rt.expected_spread_bps,
        slippage_bps=rt.expected_slippage_bps,
        net_bps=net,
        trade_pnl_usd=trade.trade_pnl_usd,
        notional_usd=trade.notional_usd,
        reason=trade.reason,
        regime=trade.regime,
        signal_source=trade.signal_source,
    )


# --- cohort aggregation --------------------------------------------------------


@dataclass(frozen=True)
class CohortEdge:
    """Aggregated cost-adjusted edge for one cohort (symbol / regime / day)."""

    cohort_key: str
    cohort_type: str  # "symbol" | "regime" | "day"
    count: int
    gross_bps_sum: float
    gross_bps_mean: float
    fee_bps_mean: float
    spread_bps_mean: float
    slippage_bps_mean: float
    net_bps_sum: float
    net_bps_mean: float
    net_bps_per_notional_mean: float
    winrate: float
    avg_win_bps: float
    avg_loss_bps: float
    p_mu_net_positive: float | None
    realized_pnl_usd_sum: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohort_key": self.cohort_key,
            "cohort_type": self.cohort_type,
            "count": self.count,
            "gross_bps_sum": round(self.gross_bps_sum, 4),
            "gross_bps_mean": round(self.gross_bps_mean, 4),
            "fee_bps_mean": round(self.fee_bps_mean, 4),
            "spread_bps_mean": round(self.spread_bps_mean, 4),
            "slippage_bps_mean": round(self.slippage_bps_mean, 4),
            "net_bps_sum": round(self.net_bps_sum, 4),
            "net_bps_mean": round(self.net_bps_mean, 4),
            "net_bps_per_notional_mean": round(self.net_bps_per_notional_mean, 4),
            "winrate": round(self.winrate, 4),
            "avg_win_bps": round(self.avg_win_bps, 4),
            "avg_loss_bps": round(self.avg_loss_bps, 4),
            "p_mu_net_positive": (
                None if self.p_mu_net_positive is None else round(self.p_mu_net_positive, 4)
            ),
            "realized_pnl_usd_sum": round(self.realized_pnl_usd_sum, 4),
        }


def aggregate_cohort(
    cohort_key: str,
    cohort_type: str,
    edges: Sequence[TradeEdge],
    *,
    bootstrap_n: int = _DEFAULT_BOOTSTRAP_N,
    min_sample: int = MIN_SAMPLE_FOR_P,
) -> CohortEdge:
    """Aggregate a list of TradeEdge into one cohort row.

    net_bps_per_notional weights each trade's net edge by its notional so a
    $5k loser and a $50 winner are not treated as equal votes. P(mu_net > 0) is
    bootstrapped on the *unweighted* per-trade net_bps (the per-trade edge
    distribution), and is None below ``min_sample``.
    """
    n = len(edges)
    if n == 0:
        return CohortEdge(cohort_key, cohort_type, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, None, 0)
    net_list = [e.net_bps for e in edges]
    gross_list = [e.gross_bps for e in edges]
    wins = [e.net_bps for e in edges if e.net_bps > 0]
    losses = [e.net_bps for e in edges if e.net_bps <= 0]
    total_notional = sum(e.notional_usd for e in edges)
    if total_notional > 0:
        net_per_notional = sum(e.net_bps * e.notional_usd for e in edges) / total_notional
    else:
        net_per_notional = _mean(net_list)
    return CohortEdge(
        cohort_key=cohort_key,
        cohort_type=cohort_type,
        count=n,
        gross_bps_sum=sum(gross_list),
        gross_bps_mean=_mean(gross_list),
        fee_bps_mean=_mean([e.fee_bps for e in edges]),
        spread_bps_mean=_mean([e.spread_bps for e in edges]),
        slippage_bps_mean=_mean([e.slippage_bps for e in edges]),
        net_bps_sum=sum(net_list),
        net_bps_mean=_mean(net_list),
        net_bps_per_notional_mean=net_per_notional,
        winrate=len(wins) / n,
        avg_win_bps=_mean(wins),
        avg_loss_bps=_mean(losses),
        p_mu_net_positive=bootstrap_p_mean_positive(
            net_list, n_resamples=bootstrap_n, min_sample=min_sample
        ),
        realized_pnl_usd_sum=sum(e.trade_pnl_usd for e in edges),
    )


# --- churn ---------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolChurn:
    """Re-entry frequency and holding behaviour per symbol."""

    symbol: str
    closes: int
    distinct_days: int
    reentries_per_day: float
    mean_hold_minutes: float | None  # None if entry timestamps unavailable

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "closes": self.closes,
            "distinct_days": self.distinct_days,
            "reentries_per_day": round(self.reentries_per_day, 4),
            "mean_hold_minutes": (
                None if self.mean_hold_minutes is None else round(self.mean_hold_minutes, 2)
            ),
        }


def compute_churn(
    trades: Sequence[ClosedTrade],
    *,
    entry_times: dict[str, list[str]] | None = None,
) -> list[SymbolChurn]:
    """Per-symbol churn: closes/day and mean holding minutes.

    Holding minutes require entry timestamps (from ``order_filled`` entry legs).
    If they are not supplied, ``mean_hold_minutes`` is honestly ``None`` rather
    than guessed. Re-entries/day uses distinct close days as the denominator.
    """
    by_symbol: dict[str, list[ClosedTrade]] = defaultdict(list)
    for t in trades:
        by_symbol[t.symbol].append(t)
    out: list[SymbolChurn] = []
    for symbol, ts in sorted(by_symbol.items()):
        days = {t.day for t in ts if t.day != "unknown"}
        distinct_days = len(days) if days else 1
        hold = _mean_hold_minutes(symbol, ts, entry_times)
        out.append(
            SymbolChurn(
                symbol=symbol,
                closes=len(ts),
                distinct_days=len(days),
                reentries_per_day=len(ts) / distinct_days,
                mean_hold_minutes=hold,
            )
        )
    return out


def _mean_hold_minutes(
    symbol: str,
    closes: Sequence[ClosedTrade],
    entry_times: dict[str, list[str]] | None,
) -> float | None:
    if not entry_times or symbol not in entry_times:
        return None
    entries = sorted(entry_times[symbol])
    close_ts = sorted(c.timestamp_utc for c in closes)
    holds: list[float] = []
    for e_iso, c_iso in zip(entries, close_ts, strict=False):
        try:
            e = datetime.fromisoformat(e_iso)
            c = datetime.fromisoformat(c_iso)
        except (ValueError, TypeError):
            continue
        delta_min = (c - e).total_seconds() / 60.0
        if delta_min >= 0:
            holds.append(delta_min)
    return _mean(holds) if holds else None


# --- forward-return coverage (C2) ----------------------------------------------


@dataclass(frozen=True)
class ForwardCoverage:
    """Honest forward-return coverage for one horizon. No fabricated values."""

    horizon_minutes: int
    covered: int
    total: int
    net_bps_mean: float | None  # None when covered == 0
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon_minutes": self.horizon_minutes,
            "covered": self.covered,
            "total": self.total,
            "coverage": f"{self.covered}/{self.total}",
            "net_bps_mean": (None if self.net_bps_mean is None else round(self.net_bps_mean, 4)),
            "reason": self.reason,
        }


def build_forward_coverage(
    trades: Sequence[ClosedTrade],
    forward_samples: dict[int, list[float]] | None = None,
    *,
    cost_model: CostModel | None = None,
    venue: str = "paper",
) -> list[ForwardCoverage]:
    """Forward-return coverage per horizon — honest about gaps.

    ``forward_samples`` maps horizon_minutes -> list of side-adjusted GROSS
    forward returns in bps that were actually sampled from historical bars. When
    it is ``None`` or a horizon is missing/empty, coverage is reported as
    ``0/N`` with the reason "no_historical_minute_bars" — we never invent a
    forward number for a past entry.

    When samples exist, each is cost-adjusted via the CostModel so entry-quality
    is measured net of what a real round-trip would have cost.
    """
    total = len(trades)
    cm = cost_model or CostModel()
    rt = cm.round_trip(venue=venue)
    cost_bps = rt.total_cost_bps
    out: list[ForwardCoverage] = []
    for horizon in FORWARD_HORIZONS_MIN:
        samples = (forward_samples or {}).get(horizon)
        if not samples:
            out.append(
                ForwardCoverage(
                    horizon_minutes=horizon,
                    covered=0,
                    total=total,
                    net_bps_mean=None,
                    reason="no_historical_minute_bars",
                )
            )
            continue
        net = [s - cost_bps for s in samples]
        out.append(
            ForwardCoverage(
                horizon_minutes=horizon,
                covered=len(samples),
                total=total,
                net_bps_mean=_mean(net),
                reason="sampled",
            )
        )
    return out


# --- open mark-to-market (kept strictly separate from closed) ------------------


@dataclass(frozen=True)
class OpenMarkToMarket:
    """Unrealised MTM of open positions at the cutoff. NEVER summed with closed."""

    count: int
    total_notional_usd: float
    unrealized_pnl_usd: float | None  # None if no live price available
    priced: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "total_notional_usd": round(self.total_notional_usd, 4),
            "unrealized_pnl_usd": (
                None if self.unrealized_pnl_usd is None else round(self.unrealized_pnl_usd, 4)
            ),
            "priced": self.priced,
        }


def mark_to_market_open(
    positions: Sequence[OpenPosition],
    mark_prices: dict[str, float] | None = None,
) -> OpenMarkToMarket:
    """Mark open positions at cutoff prices. Closed PnL is NOT touched here.

    ``mark_prices`` maps symbol -> current price. Symbols without a price are
    counted but contribute no unrealised PnL (honest gap). If NO position can be
    priced, ``unrealized_pnl_usd`` is ``None``, not 0 (0 would falsely imply
    flat).
    """
    prices = mark_prices or {}
    total_notional = sum(p.notional_usd for p in positions)
    priced = 0
    upnl = 0.0
    for p in positions:
        price = prices.get(p.symbol)
        if price is None or price <= 0:
            continue
        priced += 1
        side = (p.position_side or "long").strip().lower()
        if side == "short":
            upnl += (p.entry_price - price) * p.quantity
        else:
            upnl += (price - p.entry_price) * p.quantity
    return OpenMarkToMarket(
        count=len(positions),
        total_notional_usd=total_notional,
        unrealized_pnl_usd=(upnl if priced > 0 else None),
        priced=priced,
    )


# --- top-level report ----------------------------------------------------------


@dataclass(frozen=True)
class DiagnosticBlocker:
    """Explicitly surface missing attribution that blocks edge root-cause work."""

    code: str
    affected_count: int
    total_count: int
    share: float
    severity: str
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "affected_count": self.affected_count,
            "total_count": self.total_count,
            "share": round(self.share, 4),
            "severity": self.severity,
            "detail": self.detail,
            "meta": self.meta,
        }


@dataclass
class EdgeReport:
    """Typed, JSON-serialisable Sprint-C diagnostic report.

    ``by_symbol`` / ``by_regime`` / ``by_day`` are the cohort breakdowns the
    Sprint D edge gate consumes. ``overall`` is the all-closed-trades cohort.
    ``open_mtm`` is structurally separate from every closed figure.
    """

    generated_at_utc: str
    venue: str
    closed_trade_count: int
    overall: CohortEdge
    by_symbol: list[CohortEdge]
    by_regime: list[CohortEdge]
    by_day: list[CohortEdge]
    # NEO-P-20260603-001: edge split by signal source (canary_probe vs
    # autonomous_generator vs tv_promoted vs unknown). Lets the operator read
    # the REAL generator's edge separately from the hardcoded test probe.
    by_source: list[CohortEdge]
    churn: list[SymbolChurn]
    forward_coverage: list[ForwardCoverage]
    open_mtm: OpenMarkToMarket
    notes: list[str] = field(default_factory=list)
    diagnostic_blockers: list[DiagnosticBlocker] = field(default_factory=list)
    shadow_drift_report: dict[str, Any] | None = None
    excluded_quarantined: QuarantineExclusion = field(
        default_factory=lambda: QuarantineExclusion(0, {})
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at_utc": self.generated_at_utc,
            "venue": self.venue,
            "closed_trade_count": self.closed_trade_count,
            "excluded_quarantined": self.excluded_quarantined.to_dict(),
            "overall": self.overall.to_dict(),
            "by_symbol": [c.to_dict() for c in self.by_symbol],
            "by_regime": [c.to_dict() for c in self.by_regime],
            "by_day": [c.to_dict() for c in self.by_day],
            "by_source": [c.to_dict() for c in self.by_source],
            "churn": [c.to_dict() for c in self.churn],
            "forward_coverage": [f.to_dict() for f in self.forward_coverage],
            "open_mtm": self.open_mtm.to_dict(),
            "diagnostic_blockers": [b.to_dict() for b in self.diagnostic_blockers],
            "shadow_drift_report": self.shadow_drift_report,
            "notes": self.notes,
        }


def build_edge_report(
    closed_trades: Sequence[ClosedTrade],
    open_positions: Sequence[OpenPosition] = (),
    *,
    cost_model: CostModel | None = None,
    venue: str = "paper",
    safety_margin_bps: float = 0.0,
    mark_prices: dict[str, float] | None = None,
    forward_samples: dict[int, list[float]] | None = None,
    entry_times: dict[str, list[str]] | None = None,
    bootstrap_n: int = _DEFAULT_BOOTSTRAP_N,
    min_sample: int = MIN_SAMPLE_FOR_P,
    shadow_drift_report: Any | None = None,
    excluded_quarantined: QuarantineExclusion | None = None,
) -> EdgeReport:
    """Build the full Sprint-C report from parsed records. Pure / IO-free.

    All cost numbers come from ``cost_model`` (default ``CostModel()``), so the
    report and the engine charge identical costs. Closed and open buckets are
    computed independently and never combined.

    ``closed_trades`` must already EXCLUDE forensically-quarantined corrupt
    closes (caller's responsibility; the audit loaders do this via
    ``parse_closed_trades``). ``excluded_quarantined`` carries the honest tally
    of what was dropped so it is reported, not silently swallowed.
    """
    cm = cost_model or CostModel()
    edges = [
        compute_trade_edge(t, cm, venue=venue, safety_margin_bps=safety_margin_bps)
        for t in closed_trades
    ]

    overall = aggregate_cohort(
        "ALL", "overall", edges, bootstrap_n=bootstrap_n, min_sample=min_sample
    )

    by_symbol = _group_aggregate(
        edges,
        key=lambda e: e.symbol,
        cohort_type="symbol",
        bootstrap_n=bootstrap_n,
        min_sample=min_sample,
    )
    by_regime = _group_aggregate(
        edges,
        key=lambda e: e.regime or "unknown",
        cohort_type="regime",
        bootstrap_n=bootstrap_n,
        min_sample=min_sample,
    )
    by_day = _group_aggregate(
        edges,
        key=_edge_day,
        cohort_type="day",
        bootstrap_n=bootstrap_n,
        min_sample=min_sample,
    )
    by_source = _group_aggregate(
        edges,
        key=lambda e: e.signal_source or "unknown",
        cohort_type="source",
        bootstrap_n=bootstrap_n,
        min_sample=min_sample,
    )

    churn = compute_churn(closed_trades, entry_times=entry_times)
    forward_cov = build_forward_coverage(closed_trades, forward_samples, cost_model=cm, venue=venue)
    open_mtm = mark_to_market_open(open_positions, mark_prices)

    notes: list[str] = []
    regimes = {e.regime or "unknown" for e in edges}
    if regimes == {"unknown"} and edges:
        notes.append(
            "regime cohort = 'unknown' for all trades: position_closed events "
            "carry no per-trade regime label; not back-derived (out of scope)."
        )
    if any(f.covered == 0 for f in forward_cov):
        notes.append(
            "forward returns: 0 coverage on uncovered horizons — no historical "
            "minute-bar source for past entries. Use the prospective capture "
            "path (record entry price now, sample later) to populate these."
        )
    if overall.p_mu_net_positive is None and edges:
        notes.append(
            f"P(mu_net>0) = insufficient: n={overall.count} < min_sample={min_sample}. "
            "Verdict on edge sign is NOT statistically supported yet."
        )

    shadow_drift_payload = _shadow_drift_payload(shadow_drift_report)
    diagnostic_blockers = _build_diagnostic_blockers(
        edges,
        forward_cov,
        shadow_drift_report=shadow_drift_report,
    )
    for blocker in diagnostic_blockers:
        if blocker.code == "source_unknown":
            notes.append(
                f"source attribution blocker: {blocker.affected_count}/{blocker.total_count} "
                "closed trades have source='unknown'; generator/premium/canary edge cannot be "
                "separated until close events carry signal_source."
            )
        elif blocker.code == "regime_unknown":
            notes.append(
                f"regime attribution blocker: {blocker.affected_count}/{blocker.total_count} "
                "closed trades have regime='unknown'; regime-specific edge cannot be judged "
                "until close events carry a regime stamp."
            )
        elif blocker.code == "shadow_feature_degenerate":
            notes.append(
                "shadow feature variance blocker: constant confidence/rr/gate fields "
                "block edge learning until the shadow stream shows feature variance."
            )

    excl = excluded_quarantined or QuarantineExclusion(0, {})
    if excl.excluded_count > 0:
        reason_str = ", ".join(f"{r}={c}" for r, c in sorted(excl.reasons.items()))
        notes.append(
            f"EXCLUDED {excl.excluded_count} forensically-quarantined corrupt close(s) "
            f"from ALL edge/cohort figures ({reason_str}). Shared "
            "app.learning.bayes_quarantine signatures (PR #112) — not deleted, "
            "excluded so the release verdict is not poisoned by known-bad outcomes."
        )

    return EdgeReport(
        generated_at_utc=datetime.now(UTC).isoformat(),
        venue=venue,
        closed_trade_count=len(closed_trades),
        overall=overall,
        by_symbol=by_symbol,
        by_regime=by_regime,
        by_day=by_day,
        by_source=by_source,
        churn=churn,
        forward_coverage=forward_cov,
        open_mtm=open_mtm,
        notes=notes,
        diagnostic_blockers=diagnostic_blockers,
        shadow_drift_report=shadow_drift_payload,
        excluded_quarantined=excl,
    )


def _edge_day(e: TradeEdge) -> str:
    try:
        return datetime.fromisoformat(e.timestamp_utc).astimezone(UTC).date().isoformat()
    except (ValueError, TypeError):
        return "unknown"


def _group_aggregate(
    edges: Sequence[TradeEdge],
    *,
    key: Any,
    cohort_type: str,
    bootstrap_n: int,
    min_sample: int,
) -> list[CohortEdge]:
    groups: dict[str, list[TradeEdge]] = defaultdict(list)
    for e in edges:
        groups[str(key(e))].append(e)
    return [
        aggregate_cohort(k, cohort_type, g, bootstrap_n=bootstrap_n, min_sample=min_sample)
        for k, g in sorted(groups.items())
    ]


def _diagnostic_severity(share: float) -> str:
    if share >= 0.50:
        return "blocker"
    if share > 0.0:
        return "warning"
    return "ok"


def _unknownish(value: str | None) -> bool:
    return (value or "").strip().lower() in {"", "unknown", "?", "none", "null"}


def _shadow_drift_payload(shadow_drift_report: Any | None) -> dict[str, Any] | None:
    if shadow_drift_report is None:
        return None
    if hasattr(shadow_drift_report, "to_dict"):
        payload = shadow_drift_report.to_dict()
        return payload if isinstance(payload, dict) else None
    return shadow_drift_report if isinstance(shadow_drift_report, dict) else None


def _shadow_feature_blocker(shadow_drift_report: Any | None) -> DiagnosticBlocker | None:
    if shadow_drift_report is None:
        return None
    features = getattr(shadow_drift_report, "feature_variance", None)
    if features is None and isinstance(shadow_drift_report, dict):
        features = shadow_drift_report.get("feature_variance")
    if not isinstance(features, list) or not features:
        return None

    degenerate: list[dict[str, Any]] = []
    for feature in features:
        if isinstance(feature, dict):
            is_degenerate = bool(feature.get("is_degenerate"))
            payload = dict(feature)
        else:
            is_degenerate = bool(getattr(feature, "is_degenerate", False))
            payload = feature.to_dict() if hasattr(feature, "to_dict") else {}
        if is_degenerate:
            degenerate.append(payload)

    if not degenerate:
        return None
    total = len(features)
    return DiagnosticBlocker(
        code="shadow_feature_degenerate",
        affected_count=len(degenerate),
        total_count=total,
        share=len(degenerate) / total,
        severity="blocker",
        detail=(
            "Zero-variance or constant shadow features block edge learning; "
            "the report stays diagnostic-only until feature variance recovers."
        ),
        meta={"features": degenerate},
    )


def _build_diagnostic_blockers(
    edges: Sequence[TradeEdge],
    forward_coverage: Sequence[ForwardCoverage],
    *,
    shadow_drift_report: Any | None = None,
) -> list[DiagnosticBlocker]:
    total = len(edges)
    blockers: list[DiagnosticBlocker] = []
    if total > 0:
        source_unknown = sum(1 for e in edges if _unknownish(e.signal_source))
        if source_unknown:
            share = source_unknown / total
            blockers.append(
                DiagnosticBlocker(
                    code="source_unknown",
                    affected_count=source_unknown,
                    total_count=total,
                    share=share,
                    severity=_diagnostic_severity(share),
                    detail=(
                        "Closed trades without signal_source prevent source-level edge separation."
                    ),
                )
            )

        regime_unknown = sum(1 for e in edges if _unknownish(e.regime))
        if regime_unknown:
            share = regime_unknown / total
            blockers.append(
                DiagnosticBlocker(
                    code="regime_unknown",
                    affected_count=regime_unknown,
                    total_count=total,
                    share=share,
                    severity=_diagnostic_severity(share),
                    detail=(
                        "Closed trades without regime prevent regime-specific edge diagnostics."
                    ),
                )
            )

    total_forward = sum(f.total for f in forward_coverage)
    missing_forward = sum(max(f.total - f.covered, 0) for f in forward_coverage)
    if total_forward > 0 and missing_forward > 0:
        share = missing_forward / total_forward
        blockers.append(
            DiagnosticBlocker(
                code="forward_return_coverage_gap",
                affected_count=missing_forward,
                total_count=total_forward,
                share=share,
                severity=_diagnostic_severity(share),
                detail=(
                    "Missing forward-return samples prevent entry-quality diagnosis on "
                    "the uncovered horizons."
                ),
                meta={
                    "horizons": [
                        {
                            "minutes": f.horizon_minutes,
                            "covered": f.covered,
                            "total": f.total,
                            "reason": f.reason,
                        }
                        for f in forward_coverage
                        if f.covered < f.total
                    ]
                },
            )
        )
    shadow_blocker = _shadow_feature_blocker(shadow_drift_report)
    if shadow_blocker is not None:
        blockers.append(shadow_blocker)
    return blockers


def load_forward_samples_from_shadow_resolved(path: str | Path) -> dict[int, list[float]]:
    """Load side-adjusted gross forward bps from the shadow resolved ledger.

    This is a read-only bridge into the EdgeReport's forward coverage table. It
    reduces ``forward_return_coverage_gap`` when the prospective shadow capture
    stream already has horizon samples.
    """
    rows = read_jsonl_tolerant(Path(path))
    samples: dict[int, list[float]] = {horizon: [] for horizon in FORWARD_HORIZONS_MIN}
    for row in rows:
        for horizon, key in _SHADOW_FORWARD_BPS_KEYS.items():
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            samples[horizon].append(float(value))
    return {horizon: values for horizon, values in samples.items() if values}


# --- audit-stream loaders (thin IO at the edge) --------------------------------


@dataclass(frozen=True)
class QuarantineExclusion:
    """Honest accounting of corrupt closes excluded from the edge statistics.

    A quarantined ``position_closed`` row (forensically-confirmed corruption,
    e.g. the MATIC stale-exit runaway DS-20260529-V1) is NOT a real realised
    round-trip and would poison the cost-adjusted edge / P(mu_net>0) verdict if
    counted. It is excluded from EVERY edge and cohort figure — but it is never
    deleted (append-only audit integrity) and its count + reasons are reported
    so the operator sees exactly what was dropped and why.

    The quarantine definition is owned solely by ``app.learning.bayes_quarantine``
    (the same signatures the Bayes posterior recalc uses, PR #112) — this module
    introduces NO second quarantine rule.
    """

    excluded_count: int
    reasons: dict[str, int]  # reason -> count

    def to_dict(self) -> dict[str, Any]:
        return {
            "excluded_count": self.excluded_count,
            "reasons": dict(sorted(self.reasons.items())),
        }


def parse_closed_trades_with_exclusions(
    events: Iterable[dict[str, Any]],
    *,
    implausible_move_threshold: float = DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD,
    premium_leverage_cutoff_utc: str | None = PREMIUM_LEVERAGE_CUTOFF_UTC,
) -> tuple[list[ClosedTrade], QuarantineExclusion]:
    """Like :func:`parse_closed_trades` but also returns the exclusion tally.

    Two exclusion layers, applied AFTER the normal validity filter (so a row
    without a usable ``exit_price`` is dropped as invalid and never reaches
    either check):

    1. **Forensic signatures** — rows matched by the shared
       ``app.learning.bayes_quarantine`` signatures (e.g. MATIC stale-exit
       runaway, the 2026-05-26 ETH off-market close). Reason = the signature's
       reason string.
    2. **Generic implausibility guard (B)** — any remaining close whose
       ``|exit/entry - 1|`` exceeds ``implausible_move_threshold`` is an
       off-market/corrupt print and is excluded SYMMETRICALLY (both directions).
       Reason = ``implausible_move_gt_<pct>pct``. Set the threshold to 0 to
       disable this layer (signatures still apply).

    Excluded closes are counted in :class:`QuarantineExclusion` (never deleted —
    append-only audit integrity) so the operator sees exactly what was dropped.
    """
    out: list[ClosedTrade] = []
    excluded = 0
    reasons: dict[str, int] = defaultdict(int)
    guard_active = implausible_move_threshold > 0
    guard_key = f"implausible_move_gt_{int(round(implausible_move_threshold * 100))}pct"
    # B-Fix: pre-leverage 1x premium-close cutoff (parsed once).
    cutoff_dt: datetime | None = None
    if premium_leverage_cutoff_utc:
        try:
            cutoff_dt = datetime.fromisoformat(
                premium_leverage_cutoff_utc.replace("Z", "+00:00")
            ).astimezone(UTC)
        except ValueError:
            cutoff_dt = None
    for ev in events:
        if ev.get("event_type") != "position_closed":
            continue
        try:
            entry = float(ev["entry_price"])
            exit_px = float(ev["exit_price"])
            qty = float(ev["quantity"])
        except (KeyError, TypeError, ValueError):
            continue
        if entry <= 0 or exit_px <= 0 or qty <= 0:
            continue
        q_reason = quarantine_reason(ev)
        if q_reason is not None:
            excluded += 1
            reasons[q_reason] += 1
            continue
        # Generic off-market guard (B): symmetric, never scrubs plausible losers.
        if guard_active and abs(exit_px / entry - 1.0) > implausible_move_threshold:
            excluded += 1
            reasons[guard_key] += 1
            continue
        # B-Fix 2026-06-13: drop premium closes from the 1x-only era (their PnL
        # is leverage-distorted downward). Premium-only + before the A cutoff.
        if cutoff_dt is not None:
            src_for_cutoff = str(ev.get("signal_source") or ev.get("source") or "")
            if src_for_cutoff.startswith("telegram_premium"):
                ts_raw = str(ev.get("timestamp_utc", ""))
                try:
                    ts_dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(UTC)
                except ValueError:
                    ts_dt = None
                if ts_dt is not None and ts_dt < cutoff_dt:
                    excluded += 1
                    reasons["premium_pre_leverage_1x"] += 1
                    continue
        regime = (
            ev.get("regime")
            or ev.get("regime_state")
            or ev.get("regime_label")
            or ev.get("market_regime")
            or ev.get("volatility_regime")
            or "unknown"
        )
        # NEO-P-20260603-001: legacy position_closed rows lack signal_source →
        # "unknown" so by_source stays well-defined without a backfill.
        signal_source = (
            ev.get("signal_source")
            or ev.get("source")
            or ev.get("source_name")
            or ev.get("candidate_source")
            or "unknown"
        )
        out.append(
            ClosedTrade(
                symbol=str(ev.get("symbol", "?")),
                position_side=str(ev.get("position_side", "long")),
                entry_price=entry,
                exit_price=exit_px,
                quantity=qty,
                reason=str(ev.get("reason", "")),
                trade_pnl_usd=float(ev.get("trade_pnl_usd", 0.0) or 0.0),
                fee_usd=float(ev.get("fee_usd", 0.0) or 0.0),
                timestamp_utc=str(ev.get("timestamp_utc", "")),
                regime=str(regime),
                signal_source=str(signal_source),
            )
        )
    return out, QuarantineExclusion(excluded_count=excluded, reasons=dict(reasons))


def parse_closed_trades(events: Iterable[dict[str, Any]]) -> list[ClosedTrade]:
    """Extract ClosedTrade records from raw audit events.

    Only ``position_closed`` events with valid prices and quantity are kept.
    Phantom/partial/sanity-rejected events are ignored (they are not realised
    round-trips). Forensically-quarantined corrupt closes (shared
    ``bayes_quarantine`` signatures) are ALSO excluded — they are not real
    round-trips. Regime is read from a ``regime`` or ``regime_state`` key if
    present, else 'unknown'. Use :func:`parse_closed_trades_with_exclusions`
    when the excluded-count must be reported.
    """
    kept, _ = parse_closed_trades_with_exclusions(events)
    return kept


def extract_entry_times(events: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    """Per-symbol entry-fill timestamps from order_filled BUY/long-entry legs.

    Used for holding-duration in churn. Entry legs are buy fills for long and
    sell fills that OPEN a short; here we approximate with buy-side entries
    (the dominant paper case) and mark this as an approximation in the report.
    """
    out: dict[str, list[str]] = defaultdict(list)
    for ev in events:
        if ev.get("event_type") != "order_filled":
            continue
        side = str(ev.get("side", "")).lower()
        if side != "buy":
            continue
        if float(ev.get("pnl_usd", 0.0) or 0.0) != 0.0:
            continue  # a buy with pnl is a short-cover (exit), not an entry
        ts = ev.get("filled_at") or ev.get("timestamp_utc")
        if ts:
            out[str(ev.get("symbol", "?"))].append(str(ts))
    return dict(out)


def load_audit_events(path: str | Path) -> list[dict[str, Any]]:
    """Load a paper_execution_audit.jsonl file into dicts. Skips bad lines."""
    p = Path(path)
    if not p.exists():
        logger.warning("[edge_report] audit file not found: %s", p)
        return []
    events: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("[edge_report] skipping malformed audit line")
    return events


def build_report_from_audit(
    audit_path: str | Path,
    *,
    cost_model: CostModel | None = None,
    venue: str = "paper",
    safety_margin_bps: float = 0.0,
    mark_prices: dict[str, float] | None = None,
    forward_samples: dict[int, list[float]] | None = None,
    bootstrap_n: int = _DEFAULT_BOOTSTRAP_N,
    min_sample: int = MIN_SAMPLE_FOR_P,
    implausible_move_threshold: float = DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD,
    shadow_drift_report: Any | None = None,
) -> EdgeReport:
    """Convenience: load the audit file and build the report end-to-end.

    Corrupt closes are excluded from the edge statistics and the dropped count
    is reported: (1) forensically-quarantined signatures (shared
    ``bayes_quarantine``, e.g. the MATIC stale-exit runaway) and (2) generic
    off-market prints exceeding ``implausible_move_threshold`` (Goal 2026-06-01 B).
    """
    events = load_audit_events(audit_path)
    closed, excluded = parse_closed_trades_with_exclusions(
        events, implausible_move_threshold=implausible_move_threshold
    )
    entry_times = extract_entry_times(events)
    return build_edge_report(
        closed,
        open_positions=(),
        cost_model=cost_model,
        venue=venue,
        safety_margin_bps=safety_margin_bps,
        mark_prices=mark_prices,
        forward_samples=forward_samples,
        entry_times=entry_times,
        bootstrap_n=bootstrap_n,
        min_sample=min_sample,
        shadow_drift_report=shadow_drift_report,
        excluded_quarantined=excluded,
    )


# --- human-readable rendering (operator, not JSON spam) ------------------------


def _fmt_p(p: float | None) -> str:
    return "insufficient" if p is None else f"{p:.2%}"


def render_report(report: EdgeReport) -> str:
    """Render an operator-facing table. Not JSON — readable verdict per cohort."""
    lines: list[str] = []
    o = report.overall
    lines.append("=" * 78)
    lines.append(f"EDGE REPORT (Sprint C)  venue={report.venue}  @ {report.generated_at_utc}")
    lines.append(f"closed trades: {report.closed_trade_count}")
    excl = report.excluded_quarantined
    if excl.excluded_count > 0:
        reason_str = ", ".join(f"{r}={c}" for r, c in sorted(excl.reasons.items()))
        lines.append(
            f"excluded (quarantined corrupt closes): {excl.excluded_count}  [{reason_str}]"
        )
    lines.append("=" * 78)
    lines.append("")
    lines.append("OVERALL (all closed round-trips, cost-adjusted)")
    lines.append(
        f"  n={o.count}  net_bps mean={o.net_bps_mean:+.1f}  "
        f"net_bps/notional mean={o.net_bps_per_notional_mean:+.1f}  "
        f"gross mean={o.gross_bps_mean:+.1f}  fee={o.fee_bps_mean:.1f}"
    )
    lines.append(
        f"  winrate={o.winrate:.1%}  avg_win={o.avg_win_bps:+.1f}  "
        f"avg_loss={o.avg_loss_bps:+.1f}  realized_pnl_usd={o.realized_pnl_usd_sum:+.2f}"
    )
    lines.append(f"  P(mu_net > 0) = {_fmt_p(o.p_mu_net_positive)}   <-- the verdict")
    lines.append("")

    lines.append(_render_cohort_table("PER SYMBOL", report.by_symbol))
    lines.append("")
    lines.append(_render_cohort_table("PER REGIME", report.by_regime))
    lines.append("")
    lines.append(_render_cohort_table("PER SOURCE", report.by_source))
    lines.append("")
    lines.append(_render_cohort_table("PER DAY", report.by_day))
    lines.append("")

    lines.append("CHURN (per symbol)")
    lines.append(f"  {'symbol':<14}{'closes':>7}{'days':>6}{'reentry/day':>13}{'hold_min':>11}")
    for c in report.churn:
        hold = "n/a" if c.mean_hold_minutes is None else f"{c.mean_hold_minutes:.1f}"
        lines.append(
            f"  {c.symbol:<14}{c.closes:>7}{c.distinct_days:>6}"
            f"{c.reentries_per_day:>13.2f}{hold:>11}"
        )
    lines.append("")

    lines.append("FORWARD-RETURN COVERAGE (entry-quality probe, C2)")
    for f in report.forward_coverage:
        mean = "n/a" if f.net_bps_mean is None else f"{f.net_bps_mean:+.1f} bps"
        lines.append(
            f"  forward_{f.horizon_minutes}m: {f.covered}/{f.total} covered  "
            f"net={mean}  ({f.reason})"
        )
    lines.append("")

    if report.diagnostic_blockers:
        lines.append("DIAGNOSTIC BLOCKERS (root-cause visibility)")
        lines.append(f"  {'code':<30}{'severity':>10}{'affected':>12}{'share':>9}")
        for b in report.diagnostic_blockers:
            affected = f"{b.affected_count}/{b.total_count}"
            lines.append(f"  {b.code:<30}{b.severity:>10}{affected:>12}{b.share:>8.1%}")
        lines.append("")

    m = report.open_mtm
    upnl = "n/a (no live price)" if m.unrealized_pnl_usd is None else f"{m.unrealized_pnl_usd:+.2f}"
    lines.append("OPEN POSITIONS — MARK-TO-MARKET (separate from closed; never summed)")
    lines.append(
        f"  open={m.count}  priced={m.priced}  notional={m.total_notional_usd:.2f}  "
        f"unrealized_pnl_usd={upnl}"
    )
    lines.append("")

    if report.notes:
        lines.append("NOTES / HONEST GAPS")
        for note in report.notes:
            lines.append(f"  - {note}")
    return "\n".join(lines)


def _render_cohort_table(title: str, cohorts: Sequence[CohortEdge]) -> str:
    rows = [title]
    rows.append(
        f"  {'cohort':<14}{'n':>4}{'net_mean':>10}{'net/notnl':>11}"
        f"{'gross':>9}{'winrate':>9}{'P(mu>0)':>13}"
    )
    if not cohorts:
        rows.append("  (none)")
        return "\n".join(rows)
    for c in cohorts:
        rows.append(
            f"  {c.cohort_key:<14}{c.count:>4}{c.net_bps_mean:>+10.1f}"
            f"{c.net_bps_per_notional_mean:>+11.1f}{c.gross_bps_mean:>+9.1f}"
            f"{c.winrate:>8.0%}{_fmt_p(c.p_mu_net_positive):>13}"
        )
    return "\n".join(rows)


# --- Sprint D consumer contract ------------------------------------------------
# The edge gate (Sprint D) consumes, per cohort it wants to gate on:
#   CohortEdge.net_bps_per_notional_mean  -> expected cost-adjusted edge
#   CohortEdge.p_mu_net_positive          -> confidence the edge sign is positive
#                                            (None => insufficient => do NOT pass)
#   CohortEdge.count                      -> sample size for a min-n gate
# The gate must require BOTH net_bps_per_notional_mean > margin AND
# p_mu_net_positive >= threshold AND count >= min_n. net_bps is already
# cost-adjusted via the SAME CostModel the engine charges (no double-counting).

_MATH_OK = math.isfinite  # re-exported guard for callers validating inputs
