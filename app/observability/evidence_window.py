"""Evidence-Window report — one defensible, artefact-based edge answer.

Goal 2026-06-01 (AUFGABE 1). The operator keeps asking the same question:
*"the loop reports N completed cycles — does that prove a cost-adjusted edge?"*
A bare counter cannot answer it. This module joins the TWO append-only audit
streams into ONE typed, JSON-serialisable report:

  - ``trading_loop_audit.jsonl``    -> cycle status distribution -> **Counts**
  - ``paper_execution_audit.jsonl`` -> fills + closes -> **Edge** + **Safety**

It is **read-only on the trading runtime**. It never touches ``run_cycle``,
risk, the engine, or any setting. It DECIDES nothing — it is the evidence base
on which a *later* probe/live conversation happens (the actual release verdict
lives in ``app.risk.edge_release_policy``, Sprint D).

Single source of truth (no second rule-book)
---------------------------------------------
- Cost is the SAME ``CostModel`` the engine charges (Sprint B). net_bps here is
  byte-for-byte what the engine/gate use.
- Quarantine is the SAME ``app.learning.bayes_quarantine`` signatures (PR #112).
  A forensically-confirmed corrupt close (e.g. the MATIC stale-exit runaway) is
  COUNTED as ``quarantine_rejected`` and EXCLUDED from every edge figure — never
  deleted, never allowed to poison the verdict.
- Edge cohorts reuse ``edge_report``'s ``compute_trade_edge`` / ``aggregate_cohort``
  / bootstrap. This module only ADDS window framing, count-joining, hard safety
  assertions, and outlier-robustness (trimmed mean, bootstrap CI,
  result_without_best/worst).

Honest gaps (NOT fabricated)
----------------------------
- Forward returns (1/5/15/60m sampled at the entry) require touching the entry
  path and are an explicit FOLLOW-UP sprint. Here they are surfaced as
  ``status="pending_prospective_capture"`` with all numbers ``None``. We never
  invent a forward number for a past entry.
- Tick ``run_id`` correlation between the two streams is likewise a follow-up;
  the join here is by event semantics, not a shared run id.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.execution.cost_model import CostModel
from app.learning.bayes_quarantine import QUARANTINE_SIGNATURES
from app.observability.edge_report import (
    MIN_SAMPLE_FOR_P,
    CohortEdge,
    QuarantineExclusion,
    aggregate_cohort,
    bootstrap_p_mean_positive,
    compute_trade_edge,
    parse_closed_trades_with_exclusions,
)

logger = logging.getLogger(__name__)

_DEFAULT_BOOTSTRAP_N = 5000
# Fraction trimmed from EACH tail for the trimmed mean (10% -> robust to ~10%
# outliers on either side without discarding the bulk of the distribution).
_DEFAULT_TRIM_FRACTION = 0.10
_VERSION = "evidence_window/1.0"

# Loop statuses (must mirror app.orchestrator.models.CycleStatus). Kept as a
# local mapping so a renamed status surfaces as an unmapped raw count rather than
# silently vanishing — the raw status_breakdown is always preserved in full.
_STATUS_COMPLETED = "completed"
_STATUS_COOLDOWN = "cooldown_rejected"
_STATUS_CHURN = "churn_rejected"
_STATUS_ENTRY_MODE_BLOCKED = "entry_mode_blocked"
_STATUS_ERROR = "error"
# A cycle that did NOT even reach the sizing/gating stage (no tradable candidate).
_NON_CANDIDATE_STATUSES = frozenset(
    {"no_market_data", "stale_data", "no_signal", _STATUS_ENTRY_MODE_BLOCKED, _STATUS_ERROR}
)
# Statuses that represent an entry candidate the gates then rejected.
_EDGE_REJECT_STATUSES = frozenset({"edge_rejected"})


# === bucket: COUNTS ============================================================


@dataclass(frozen=True)
class WindowCounts:
    """Cycle-level accounting from the trading_loop_audit status distribution.

    ``status_breakdown`` is the full, lossless tally; the named counters are
    derived views of it so a renamed/new status is never silently dropped.
    """

    cycles_total: int
    cycles_completed: int
    entry_candidates: int
    paper_entries: int
    cooldown_rejected: int
    churn_rejected: int
    edge_rejected: int
    quarantine_rejected: int
    errors: int
    status_breakdown: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycles_total": self.cycles_total,
            "cycles_completed": self.cycles_completed,
            "entry_candidates": self.entry_candidates,
            "paper_entries": self.paper_entries,
            "cooldown_rejected": self.cooldown_rejected,
            "churn_rejected": self.churn_rejected,
            "edge_rejected": self.edge_rejected,
            "quarantine_rejected": self.quarantine_rejected,
            "errors": self.errors,
            "status_breakdown": dict(sorted(self.status_breakdown.items())),
        }


# === bucket: SAFETY ============================================================


@dataclass(frozen=True)
class WindowSafety:
    """Hard audit assertions. The whole point: prove no live leak happened.

    ``live_orders_attempted`` is DERIVED from the data (count of fills whose
    venue is not a paper venue), not assumed to be 0. ``auto_promotions`` is
    structurally 0 — this report and the edge gate never flip ``entry_mode``;
    promotion is always an explicit operator action.
    """

    live_orders_attempted: int
    live_orders_attempted_derivation: str
    entry_mode_blocked: int
    auto_promotions: int
    non_paper_venues_seen: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "live_orders_attempted": self.live_orders_attempted,
            "live_orders_attempted_derivation": self.live_orders_attempted_derivation,
            "entry_mode_blocked": self.entry_mode_blocked,
            "auto_promotions": self.auto_promotions,
            "non_paper_venues_seen": list(self.non_paper_venues_seen),
        }


# === bucket: EDGE (with robustness) ============================================


@dataclass(frozen=True)
class TrimmedResult:
    """A re-aggregated edge view after removing one trade (best or worst).

    Used to prove the edge is NOT carried by a single outlier: if removing the
    best trade collapses the mean, the "edge" is one lucky trade, not a process.
    """

    removed_net_bps: float | None
    mean_net_bps: float
    p_mu_net_positive: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "removed_net_bps": (
                None if self.removed_net_bps is None else round(self.removed_net_bps, 4)
            ),
            "mean_net_bps": round(self.mean_net_bps, 4),
            "p_mu_net_positive": (
                None if self.p_mu_net_positive is None else round(self.p_mu_net_positive, 4)
            ),
        }


@dataclass(frozen=True)
class WindowEdge:
    """Cost-adjusted, quarantine-cleaned realised edge over the window.

    Every figure is computed on closed round-trips AFTER quarantine exclusion and
    AFTER subtracting the single-source ``CostModel`` cost. Probabilities are
    ``None`` below ``MIN_SAMPLE_FOR_P`` (honest insufficiency, never invented).
    """

    trade_count: int
    mean_net_bps: float
    median_net_bps: float
    trimmed_mean_net_bps: float
    trim_fraction: float
    net_bps_per_notional_mean: float
    p_mu_net_positive: float | None
    p_threshold_bps: float
    p_mu_net_above_threshold: float | None
    bootstrap_ci_95: tuple[float, float] | None
    result_without_best_trade: TrimmedResult
    result_without_worst_trade: TrimmedResult
    per_symbol_net_bps: list[CohortEdge]
    realized_pnl_usd_sum: float
    quarantine_excluded: QuarantineExclusion
    forward_return_status: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_count": self.trade_count,
            "mean_net_bps": round(self.mean_net_bps, 4),
            "median_net_bps": round(self.median_net_bps, 4),
            "trimmed_mean_net_bps": round(self.trimmed_mean_net_bps, 4),
            "trim_fraction": self.trim_fraction,
            "net_bps_per_notional_mean": round(self.net_bps_per_notional_mean, 4),
            "p_mu_net_positive": (
                None if self.p_mu_net_positive is None else round(self.p_mu_net_positive, 4)
            ),
            "p_threshold_bps": round(self.p_threshold_bps, 4),
            "p_mu_net_above_threshold": (
                None
                if self.p_mu_net_above_threshold is None
                else round(self.p_mu_net_above_threshold, 4)
            ),
            "bootstrap_ci_95": (
                None
                if self.bootstrap_ci_95 is None
                else [round(self.bootstrap_ci_95[0], 4), round(self.bootstrap_ci_95[1], 4)]
            ),
            "result_without_best_trade": self.result_without_best_trade.to_dict(),
            "result_without_worst_trade": self.result_without_worst_trade.to_dict(),
            "per_symbol_net_bps": [c.to_dict() for c in self.per_symbol_net_bps],
            "realized_pnl_usd_sum": round(self.realized_pnl_usd_sum, 4),
            "quarantine_excluded": self.quarantine_excluded.to_dict(),
            "forward_return_status": self.forward_return_status,
        }


# === bucket: WINDOW metadata ===================================================


@dataclass(frozen=True)
class WindowMeta:
    """Window bounds + the versions of every rule-set that shaped the numbers."""

    started_at: str | None
    ended_at: str | None
    report_version: str
    cost_model_version: str
    gate_version: str
    quarantine_version: str
    quarantine_signature_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "report_version": self.report_version,
            "cost_model_version": self.cost_model_version,
            "gate_version": self.gate_version,
            "quarantine_version": self.quarantine_version,
            "quarantine_signature_count": self.quarantine_signature_count,
        }


# === top-level report ==========================================================


@dataclass
class EvidenceWindowReport:
    """The single typed object the operator reads to judge the evidence."""

    window: WindowMeta
    counts: WindowCounts
    safety: WindowSafety
    edge: WindowEdge
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window.to_dict(),
            "counts": self.counts.to_dict(),
            "safety": self.safety.to_dict(),
            "edge": self.edge.to_dict(),
            "notes": list(self.notes),
        }


# === pure helpers ==============================================================


def _trimmed_mean(values: Sequence[float], trim_fraction: float) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    k = int(n * trim_fraction)
    if 2 * k >= n:
        # too few samples to trim both tails meaningfully — fall back to median.
        return float(statistics.median(values))
    ordered = sorted(values)
    kept = ordered[k : n - k]
    return sum(kept) / len(kept)


def _bootstrap_ci(
    values: Sequence[float],
    *,
    n_resamples: int,
    min_sample: int,
    seed: int = 1337,
    alpha: float = 0.05,
) -> tuple[float, float] | None:
    """Percentile bootstrap CI for the mean. None below ``min_sample``."""
    import random

    vals = [float(v) for v in values]
    n = len(vals)
    if n < min_sample:
        return None
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(n_resamples):
        s = 0.0
        for _ in range(n):
            s += vals[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((alpha / 2) * n_resamples)]
    hi = means[min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))]
    return (lo, hi)


def _p_mean_above_threshold(
    values: Sequence[float],
    threshold: float,
    *,
    n_resamples: int,
    min_sample: int,
    seed: int = 7919,
) -> float | None:
    """P(mean(values) > threshold) by bootstrap. None below ``min_sample``."""
    shifted = [v - threshold for v in values]
    return bootstrap_p_mean_positive(
        shifted, n_resamples=n_resamples, min_sample=min_sample, seed=seed
    )


def _trimmed_result(
    net_values: Sequence[float],
    *,
    remove: str,
    bootstrap_n: int,
    min_sample: int,
) -> TrimmedResult:
    """Re-aggregate after removing the best (max) or worst (min) net trade."""
    if not net_values:
        return TrimmedResult(removed_net_bps=None, mean_net_bps=0.0, p_mu_net_positive=None)
    vals = list(net_values)
    target = max(vals) if remove == "best" else min(vals)
    vals.remove(target)
    if not vals:
        return TrimmedResult(removed_net_bps=target, mean_net_bps=0.0, p_mu_net_positive=None)
    mean = sum(vals) / len(vals)
    p = bootstrap_p_mean_positive(vals, n_resamples=bootstrap_n, min_sample=min_sample)
    return TrimmedResult(removed_net_bps=target, mean_net_bps=mean, p_mu_net_positive=p)


def _is_paper_venue(venue: str) -> bool:
    v = (venue or "").strip().lower()
    return v in {"", "paper", "paper_trading", "sim", "simulation"}


# === builders ==================================================================


def build_evidence_window(
    *,
    loop_events: Iterable[dict[str, Any]],
    exec_events: Iterable[dict[str, Any]],
    cost_model: CostModel | None = None,
    venue: str = "paper",
    safety_margin_bps: float = 0.0,
    p_threshold_bps: float = 0.0,
    trim_fraction: float = _DEFAULT_TRIM_FRACTION,
    bootstrap_n: int = _DEFAULT_BOOTSTRAP_N,
    min_sample: int = MIN_SAMPLE_FOR_P,
) -> EvidenceWindowReport:
    """Build the joined evidence window from parsed events. Pure / IO-free.

    ``loop_events`` are raw ``trading_loop_audit`` rows (carry ``status``).
    ``exec_events`` are raw ``paper_execution_audit`` rows (``order_filled`` /
    ``position_closed``). Both must already be windowed by the caller if a
    sub-range is wanted; this function reports over exactly what it is handed.
    """
    cm = cost_model or CostModel()
    loop_list = [e for e in loop_events if isinstance(e, dict)]
    exec_list = [e for e in exec_events if isinstance(e, dict)]

    counts, window_bounds = _build_counts(loop_list, exec_list)
    safety = _build_safety(loop_list, exec_list)

    closed, excluded = parse_closed_trades_with_exclusions(exec_list)
    # join the quarantine tally into the cycle-level count view
    counts = _with_quarantine_count(counts, excluded.excluded_count)

    edge = _build_edge(
        closed,
        excluded,
        cost_model=cm,
        venue=venue,
        safety_margin_bps=safety_margin_bps,
        p_threshold_bps=p_threshold_bps,
        trim_fraction=trim_fraction,
        bootstrap_n=bootstrap_n,
        min_sample=min_sample,
    )

    meta = WindowMeta(
        started_at=window_bounds[0],
        ended_at=window_bounds[1],
        report_version=_VERSION,
        cost_model_version=cm.round_trip(venue=venue).table_version,
        gate_version="edge_release_policy/sprint-D",
        quarantine_version="bayes_quarantine/PR-112",
        quarantine_signature_count=len(QUARANTINE_SIGNATURES),
    )

    notes = _build_notes(counts, safety, edge, min_sample)
    return EvidenceWindowReport(window=meta, counts=counts, safety=safety, edge=edge, notes=notes)


def _build_counts(
    loop_list: list[dict[str, Any]],
    exec_list: list[dict[str, Any]],
) -> tuple[WindowCounts, tuple[str | None, str | None]]:
    breakdown: dict[str, int] = defaultdict(int)
    timestamps: list[str] = []
    for ev in loop_list:
        status = str(ev.get("status", "unknown"))
        breakdown[status] += 1
        for key in ("started_at", "completed_at", "timestamp_utc"):
            ts = ev.get(key)
            if isinstance(ts, str) and ts:
                timestamps.append(ts)
    # Window bounds span BOTH streams — a close can be later than the last loop
    # cycle row, and the reported window must cover it (else ended_at lies).
    for ev in exec_list:
        for key in ("timestamp_utc", "filled_at"):
            ts = ev.get(key)
            if isinstance(ts, str) and ts:
                timestamps.append(ts)

    cycles_total = sum(breakdown.values())
    completed = breakdown.get(_STATUS_COMPLETED, 0)
    cooldown = breakdown.get(_STATUS_COOLDOWN, 0)
    churn = breakdown.get(_STATUS_CHURN, 0)
    errors = breakdown.get(_STATUS_ERROR, 0)
    edge_rej = sum(breakdown.get(s, 0) for s in _EDGE_REJECT_STATUSES)
    # an entry candidate = a cycle that reached the gating stage with a tradable
    # signal (i.e. not one that bailed before sizing). Derived, not invented.
    non_candidate = sum(breakdown.get(s, 0) for s in _NON_CANDIDATE_STATUSES)
    entry_candidates = cycles_total - non_candidate

    paper_entries = _count_paper_entries(exec_list)

    counts = WindowCounts(
        cycles_total=cycles_total,
        cycles_completed=completed,
        entry_candidates=max(entry_candidates, 0),
        paper_entries=paper_entries,
        cooldown_rejected=cooldown,
        churn_rejected=churn,
        edge_rejected=edge_rej,
        quarantine_rejected=0,  # filled later from the close-stream exclusion tally
        errors=errors,
        status_breakdown=dict(breakdown),
    )
    bounds = (min(timestamps) if timestamps else None, max(timestamps) if timestamps else None)
    return counts, bounds


def _with_quarantine_count(counts: WindowCounts, quarantine_rejected: int) -> WindowCounts:
    return WindowCounts(
        cycles_total=counts.cycles_total,
        cycles_completed=counts.cycles_completed,
        entry_candidates=counts.entry_candidates,
        paper_entries=counts.paper_entries,
        cooldown_rejected=counts.cooldown_rejected,
        churn_rejected=counts.churn_rejected,
        edge_rejected=counts.edge_rejected,
        quarantine_rejected=quarantine_rejected,
        errors=counts.errors,
        status_breakdown=counts.status_breakdown,
    )


def _count_paper_entries(exec_list: list[dict[str, Any]]) -> int:
    """order_filled BUY legs that OPEN a position (entry), not short-covers.

    Mirrors edge_report.extract_entry_times: a buy with pnl_usd==0 is an entry;
    a buy with pnl is a short-cover (exit).
    """
    n = 0
    for ev in exec_list:
        if ev.get("event_type") != "order_filled":
            continue
        if str(ev.get("side", "")).lower() != "buy":
            continue
        try:
            pnl = float(ev.get("pnl_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        if pnl != 0.0:
            continue
        n += 1
    return n


def _build_safety(
    loop_list: list[dict[str, Any]],
    exec_list: list[dict[str, Any]],
) -> WindowSafety:
    entry_mode_blocked = sum(
        1 for ev in loop_list if str(ev.get("status", "")) == _STATUS_ENTRY_MODE_BLOCKED
    )
    live_attempts = 0
    non_paper: set[str] = set()
    for ev in exec_list:
        if ev.get("event_type") != "order_filled":
            continue
        venue = str(ev.get("fee_venue", "") or ev.get("venue", ""))
        if not _is_paper_venue(venue):
            live_attempts += 1
            non_paper.add(venue or "<unknown>")
    derivation = (
        "count of order_filled events whose fee_venue/venue is not a paper venue "
        "(paper|sim|empty). 0 confirms every fill in the window was simulated; the "
        "paper engine also hard-blocks live_enabled=True at construction "
        "(PaperExecutionEngine), so this is a defence-in-depth count, not the only "
        "guard."
    )
    return WindowSafety(
        live_orders_attempted=live_attempts,
        live_orders_attempted_derivation=derivation,
        entry_mode_blocked=entry_mode_blocked,
        # structurally 0: neither this report nor the edge gate flips entry_mode.
        auto_promotions=0,
        non_paper_venues_seen=sorted(non_paper),
    )


def _build_edge(
    closed: Sequence[Any],
    excluded: QuarantineExclusion,
    *,
    cost_model: CostModel,
    venue: str,
    safety_margin_bps: float,
    p_threshold_bps: float,
    trim_fraction: float,
    bootstrap_n: int,
    min_sample: int,
) -> WindowEdge:
    edges = [
        compute_trade_edge(t, cost_model, venue=venue, safety_margin_bps=safety_margin_bps)
        for t in closed
    ]
    net_values = [e.net_bps for e in edges]
    n = len(net_values)

    overall = aggregate_cohort(
        "ALL", "overall", edges, bootstrap_n=bootstrap_n, min_sample=min_sample
    )

    by_symbol = _per_symbol(edges, bootstrap_n=bootstrap_n, min_sample=min_sample)

    median = float(statistics.median(net_values)) if net_values else 0.0
    trimmed = _trimmed_mean(net_values, trim_fraction)
    ci = _bootstrap_ci(net_values, n_resamples=bootstrap_n, min_sample=min_sample)
    p_above = _p_mean_above_threshold(
        net_values, p_threshold_bps, n_resamples=bootstrap_n, min_sample=min_sample
    )

    without_best = _trimmed_result(
        net_values, remove="best", bootstrap_n=bootstrap_n, min_sample=min_sample
    )
    without_worst = _trimmed_result(
        net_values, remove="worst", bootstrap_n=bootstrap_n, min_sample=min_sample
    )

    forward_status = {
        "status": "pending_prospective_capture",
        "reason": (
            "forward returns (1/5/15/60m sampled AT the entry) require touching the "
            "entry path and are an explicit follow-up sprint. No forward number is "
            "fabricated for past entries."
        ),
        "horizons_minutes": [1, 5, 15, 60],
        "net_bps_1m": None,
        "net_bps_5m": None,
        "net_bps_15m": None,
        "net_bps_60m": None,
    }

    return WindowEdge(
        trade_count=n,
        mean_net_bps=overall.net_bps_mean,
        median_net_bps=median,
        trimmed_mean_net_bps=trimmed,
        trim_fraction=trim_fraction,
        net_bps_per_notional_mean=overall.net_bps_per_notional_mean,
        p_mu_net_positive=overall.p_mu_net_positive,
        p_threshold_bps=p_threshold_bps,
        p_mu_net_above_threshold=p_above,
        bootstrap_ci_95=ci,
        result_without_best_trade=without_best,
        result_without_worst_trade=without_worst,
        per_symbol_net_bps=by_symbol,
        realized_pnl_usd_sum=overall.realized_pnl_usd_sum,
        quarantine_excluded=excluded,
        forward_return_status=forward_status,
    )


def _per_symbol(edges: Sequence[Any], *, bootstrap_n: int, min_sample: int) -> list[CohortEdge]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for e in edges:
        groups[e.symbol].append(e)
    return [
        aggregate_cohort(sym, "symbol", g, bootstrap_n=bootstrap_n, min_sample=min_sample)
        for sym, g in sorted(groups.items())
    ]


def _build_notes(
    counts: WindowCounts, safety: WindowSafety, edge: WindowEdge, min_sample: int
) -> list[str]:
    notes: list[str] = []
    if edge.trade_count == 0:
        notes.append(
            "No closed round-trips in the window: edge is UNKNOWN, not zero. "
            "Counts/safety are still valid."
        )
    if edge.p_mu_net_positive is None and edge.trade_count > 0:
        notes.append(
            f"P(mu_net>0) = insufficient: n={edge.trade_count} < min_sample={min_sample}. "
            "Edge-sign verdict is NOT statistically supported yet."
        )
    if edge.quarantine_excluded.excluded_count > 0:
        reasons = ", ".join(f"{r}={c}" for r, c in sorted(edge.quarantine_excluded.reasons.items()))
        notes.append(
            f"EXCLUDED {edge.quarantine_excluded.excluded_count} quarantined corrupt "
            f"close(s) from ALL edge figures ({reasons}); counted as "
            "quarantine_rejected. Shared bayes_quarantine signatures (PR #112)."
        )
    if safety.live_orders_attempted > 0:
        notes.append(
            f"*** {safety.live_orders_attempted} NON-PAPER FILL(S) DETECTED "
            f"({', '.join(safety.non_paper_venues_seen)}) — investigate immediately. "
            "The window is supposed to be paper-only."
        )
    if (
        edge.trade_count > 0
        and edge.result_without_best_trade.mean_net_bps < 0 <= edge.mean_net_bps
    ):
        notes.append(
            "OUTLIER WARNING: removing the single best trade turns the mean net "
            "edge NEGATIVE. The apparent edge is carried by one trade, not a process."
        )
    return notes


# === audit-stream IO (thin edge) ===============================================


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        logger.warning("[evidence_window] audit file not found: %s", p)
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("[evidence_window] skipping malformed audit line in %s", p)
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _within_window(ts: str | None, since: datetime | None, until: datetime | None) -> bool:
    if since is None and until is None:
        return True
    if not ts:
        # rows without a timestamp are kept only when no bound is set; with a
        # bound we cannot place them, so we drop them (honest, not guessed).
        return False
    try:
        when = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    if since is not None and when < since:
        return False
    if until is not None and when > until:
        return False
    return True


def _filter_window(
    events: list[dict[str, Any]],
    *,
    ts_keys: Sequence[str],
    since: datetime | None,
    until: datetime | None,
) -> list[dict[str, Any]]:
    if since is None and until is None:
        return events
    kept: list[dict[str, Any]] = []
    for ev in events:
        ts: str | None = None
        for key in ts_keys:
            val = ev.get(key)
            if isinstance(val, str) and val:
                ts = val
                break
        if _within_window(ts, since, until):
            kept.append(ev)
    return kept


def build_window_from_audit(
    *,
    loop_audit_path: str | Path = "artifacts/trading_loop_audit.jsonl",
    exec_audit_path: str | Path = "artifacts/paper_execution_audit.jsonl",
    since: datetime | None = None,
    until: datetime | None = None,
    cost_model: CostModel | None = None,
    venue: str = "paper",
    safety_margin_bps: float = 0.0,
    p_threshold_bps: float = 0.0,
    trim_fraction: float = _DEFAULT_TRIM_FRACTION,
    bootstrap_n: int = _DEFAULT_BOOTSTRAP_N,
    min_sample: int = MIN_SAMPLE_FOR_P,
) -> EvidenceWindowReport:
    """Load both audit files and build the window end-to-end.

    ``since`` / ``until`` (tz-aware UTC) bound the window; rows outside are
    dropped before aggregation. With no bounds the full streams are used.
    """
    loop_events = _filter_window(
        _load_jsonl(loop_audit_path),
        ts_keys=("started_at", "completed_at", "timestamp_utc"),
        since=since,
        until=until,
    )
    exec_events = _filter_window(
        _load_jsonl(exec_audit_path),
        ts_keys=("timestamp_utc", "filled_at"),
        since=since,
        until=until,
    )
    return build_evidence_window(
        loop_events=loop_events,
        exec_events=exec_events,
        cost_model=cost_model,
        venue=venue,
        safety_margin_bps=safety_margin_bps,
        p_threshold_bps=p_threshold_bps,
        trim_fraction=trim_fraction,
        bootstrap_n=bootstrap_n,
        min_sample=min_sample,
    )


# === operator rendering ========================================================


def _fmt_p(p: float | None) -> str:
    return "insufficient" if p is None else f"{p:.2%}"


def render_window(report: EvidenceWindowReport) -> str:
    """Operator-facing rendering — readable evidence, not JSON spam."""
    w, c, s, e = report.window, report.counts, report.safety, report.edge
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("EVIDENCE WINDOW REPORT (Goal 2026-06-01) — decides nothing, proves evidence")
    lines.append("=" * 78)
    lines.append(f"  window:            {w.started_at} -> {w.ended_at}")
    lines.append(f"  cost_model:        {w.cost_model_version}")
    lines.append(f"  gate_version:      {w.gate_version}")
    lines.append(
        f"  quarantine:        {w.quarantine_version} ({w.quarantine_signature_count} sigs)"
    )
    lines.append("")

    lines.append("COUNTS (from trading_loop_audit status distribution)")
    lines.append(
        f"  cycles_total={c.cycles_total}  completed={c.cycles_completed}  "
        f"entry_candidates={c.entry_candidates}  paper_entries={c.paper_entries}"
    )
    lines.append(
        f"  cooldown_rejected={c.cooldown_rejected}  churn_rejected={c.churn_rejected}  "
        f"edge_rejected={c.edge_rejected}  quarantine_rejected={c.quarantine_rejected}  "
        f"errors={c.errors}"
    )
    lines.append("")

    lines.append("SAFETY (hard audit assertions)")
    lines.append(f"  live_orders_attempted = {s.live_orders_attempted}   (MUST be 0)")
    lines.append(f"  entry_mode_blocked    = {s.entry_mode_blocked}")
    lines.append(f"  auto_promotions       = {s.auto_promotions}   (report flips nothing)")
    if s.non_paper_venues_seen:
        lines.append(f"  !! non-paper venues seen: {', '.join(s.non_paper_venues_seen)}")
    lines.append("")

    lines.append("EDGE (cost-adjusted, quarantine-cleaned, per realised close)")
    lines.append(f"  trade_count          = {e.trade_count}")
    lines.append(f"  mean_net_bps         = {e.mean_net_bps:+.1f}")
    lines.append(f"  median_net_bps       = {e.median_net_bps:+.1f}")
    lines.append(
        f"  trimmed_mean ({int(e.trim_fraction * 100)}%/tail) = {e.trimmed_mean_net_bps:+.1f}"
    )
    lines.append(f"  net_bps/notional     = {e.net_bps_per_notional_mean:+.1f}")
    ci = (
        "insufficient"
        if e.bootstrap_ci_95 is None
        else f"[{e.bootstrap_ci_95[0]:+.1f}, {e.bootstrap_ci_95[1]:+.1f}]"
    )
    lines.append(f"  bootstrap_ci_95      = {ci}")
    lines.append(f"  P(mu_net > 0)        = {_fmt_p(e.p_mu_net_positive)}   <-- the verdict")
    lines.append(
        f"  P(mu_net > {e.p_threshold_bps:+.0f} bps) = {_fmt_p(e.p_mu_net_above_threshold)}"
    )
    lines.append(f"  realized_pnl_usd_sum = {e.realized_pnl_usd_sum:+.2f}")
    if e.quarantine_excluded.excluded_count > 0:
        lines.append(
            f"  excluded (quarantine): {e.quarantine_excluded.excluded_count} corrupt close(s)"
        )
    lines.append("")

    lines.append("ROBUSTNESS (is the edge carried by one trade?)")
    rb = e.result_without_best_trade
    rw = e.result_without_worst_trade
    rb_removed = "n/a" if rb.removed_net_bps is None else f"{rb.removed_net_bps:+.1f}"
    rw_removed = "n/a" if rw.removed_net_bps is None else f"{rw.removed_net_bps:+.1f}"
    lines.append(
        f"  result_without_best_trade : mean_net={rb.mean_net_bps:+.1f}  "
        f"(removed {rb_removed} bps)  P(mu>0)={_fmt_p(rb.p_mu_net_positive)}"
    )
    lines.append(
        f"  result_without_worst_trade: mean_net={rw.mean_net_bps:+.1f}  "
        f"(removed {rw_removed} bps)  P(mu>0)={_fmt_p(rw.p_mu_net_positive)}"
    )
    lines.append("")

    lines.append("PER SYMBOL (net_bps)")
    lines.append(f"  {'symbol':<14}{'n':>4}{'net_mean':>10}{'winrate':>9}{'P(mu>0)':>13}")
    if not e.per_symbol_net_bps:
        lines.append("  (none)")
    for row in e.per_symbol_net_bps:
        lines.append(
            f"  {row.cohort_key:<14}{row.count:>4}{row.net_bps_mean:>+10.1f}"
            f"{row.winrate:>8.0%}{_fmt_p(row.p_mu_net_positive):>13}"
        )
    lines.append("")

    lines.append("FORWARD RETURNS")
    lines.append(f"  status: {e.forward_return_status['status']} (explicit follow-up sprint)")
    lines.append("")

    if report.notes:
        lines.append("NOTES / HONEST GAPS")
        for note in report.notes:
            lines.append(f"  - {note}")
    return "\n".join(lines)
