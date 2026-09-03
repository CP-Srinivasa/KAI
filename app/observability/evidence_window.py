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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.execution.cost_model import CostModel
from app.learning.bayes_quarantine import QUARANTINE_SIGNATURES
from app.observability.edge_report import (
    DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD,
    MIN_SAMPLE_FOR_P,
    ClosedTrade,
    CohortEdge,
    QuarantineExclusion,
    aggregate_cohort,
    bootstrap_p_mean_positive,
    compute_trade_edge,
    parse_closed_trades_with_exclusions,
)
from app.observability.momentum_cohort_outcomes import DEFAULT_COHORT as MOMENTUM_COHORT

logger = logging.getLogger(__name__)

_DEFAULT_BOOTSTRAP_N = 5000
# Fraction trimmed from EACH tail for the trimmed mean (10% -> robust to ~10%
# outliers on either side without discarding the bulk of the distribution).
_DEFAULT_TRIM_FRACTION = 0.10
_VERSION = "evidence_window/1.0"

# Attributed signal_source values that identify the REAL autonomous generator.
# The canonical edge restricts the EDGE figures to these so the epoch-foreign,
# unattributed May-canary closes (which fabricated a fake positive ETH cohort —
# see memory kai_edge_epoch_contamination_20260623) can never re-contaminate the
# one defensible edge answer. ``real_analysis`` is the pre-#226 label for the
# same generator (memory kai_edge_cohort_key_fix). Structural (attribution), not
# date-magic, so it stays correct as new data arrives.
CANONICAL_EDGE_SOURCES: frozenset[str] = frozenset({"autonomous_generator", "real_analysis"})

# Cohort feeders tag each cycle with analysis_source, which — forward of the
# 2026-06-29 attribution fix (app.orchestrator.signal_source) — becomes the close's
# signal_source. But closes recorded BEFORE that fix fell through to the
# ``autonomous_generator`` default while the cohort tag survived verbatim in the
# document_id (``<cohort>_<SYM>``). The source filter must recover the TRUE source
# from that prefix, else a foreign-cohort microcap (the real ``momentum_universe``
# SLX +2799bps close) re-inflates the canonical autonomous edge — the same
# contamination class as kai_edge_epoch_contamination_20260623, here via
# mis-attribution rather than epoch. Mirrors the GENERIC branch of
# resolve_signal_source: add a cohort feeder here when it ships, so the recurring
# "the taxonomy whitelist forgot the new cohort" bug class stays closed.
_MIS_BUCKETED_COHORTS: tuple[str, ...] = (MOMENTUM_COHORT,)


def edge_source_of(trade: ClosedTrade) -> str:
    """The trade's TRUE source for the edge source-filter.

    Recovers a cohort tag that survives in ``document_id`` when the stored
    ``signal_source`` was mis-bucketed (pre-2026-06-29 cohort closes). This is
    source-RECOVERY, not source-deletion: a query whose allowlist includes the
    cohort (e.g. the G3-G7 cohort-edge gate) still keeps the close.
    """
    doc_id = trade.document_id or ""
    for cohort in _MIS_BUCKETED_COHORTS:
        if doc_id.startswith(f"{cohort}_"):
            return cohort
    return trade.signal_source


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

# Dokumentiert-benigne Non-Paper-Marker: die 2 Mai-``legacy``-Fills sind
# epoch-fremde, untersucht-benigne Alt-Closes (memories kai_triple_verdict_20260701 /
# kai_edge_epoch_contamination_20260623). Sie bleiben in ``live_orders_attempted``
# und ``non_paper_venues_seen`` voll sichtbar (Wahrheit unangetastet) — nur der
# Exit-2-Tripwire keyed auf ``live_orders_unexplained``, sonst feuert er permanent
# und alarmiert damit nichts. Ein ECHTER Live-Fill trägt einen realen Venue-Namen
# und zählt weiter als unexplained.
# STAB-2026-09-01 §1 — the forensic classification of the two known non-paper
# fills, and the reason this is no longer a bare venue-string allowlist.
#
# WHAT THE TWO ROWS ACTUALLY ARE (measured, not narrated):
#   fill_1b252b697674 / ord_24aa77e967be  ETH/USDT    sell  2026-05-04T02:41:56Z
#   fill_82cdc5b05c4e / ord_4048a7fb20f8  GIGGLE/USDT buy   2026-05-04T22:48:55Z
# Both carry fee_venue="legacy" and fee_table_version="constructor". Neither
# carries an exchange_order_id, an execution_mode, or any external venue.
#
# CLASSIFICATION = B (paper fill with a mislabelled provenance marker), with C
# (epoch-foreign) true as a secondary property. The mechanism is a deploy
# fingerprint, not an execution event: commit 6ddb83cd (2026-05-03 12:38) gave
# PaperOrder.venue the default "legacy"; commit 5614ecae (2026-05-05 10:11)
# changed it to "paper". Exactly two fills fall inside that 46-hour window and
# they are exactly these two. The venue-label timeline over the whole audit is a
# clean deploy fingerprint: "" until 2026-05-02, "legacy" on 2026-05-04 only,
# "paper" from 2026-05-06 onward, nothing else ever.
#
# WHY THE ALLOWLIST CHANGED SHAPE: matching on the STRING "legacy" excused a
# property that is still reachable today -- ``Fill.fee_venue`` defaults to
# "legacy" (app/execution/models.py:125). Any future row constructed without an
# explicit fee_venue would therefore have been auto-narrated as a
# "documented-benign epoch-foreign May close" forever. The exemption is now
# pinned to the two identified fills AND additionally requires the row to
# predate the current portfolio epoch. Both conditions must hold; anything else
# marked non-paper counts as UNEXPLAINED and trips the tripwire.
_DOCUMENTED_BENIGN_NON_PAPER_VENUES = frozenset({"legacy"})

#: The exact fills the forensic classification above covers.
_DOCUMENTED_BENIGN_NON_PAPER_FILL_IDS = frozenset({"fill_1b252b697674", "fill_82cdc5b05c4e"})
_DOCUMENTED_BENIGN_NON_PAPER_ORDER_IDS = frozenset({"ord_24aa77e967be", "ord_4048a7fb20f8"})

#: Start of the current paper epoch (``portfolio_epoch_reset``). A benign legacy
#: row must predate this; a legacy-marked row at or after it is a NEW event and
#: must never inherit a historical exemption.
_PAPER_EPOCH_START_UTC = "2026-07-12T22:22:09.568711+00:00"


def _fill_identity(ev: Mapping[str, Any]) -> tuple[str, str]:
    return str(ev.get("fill_id") or ""), str(ev.get("order_id") or "")


def is_documented_benign_non_paper(ev: Mapping[str, Any]) -> bool:
    """True only for the two forensically classified pre-epoch legacy fills.

    Fail-CLOSED in three directions: an unknown fill/order id is never benign, a
    row at or after the epoch start is never benign however it is labelled, and
    an unparseable timestamp is never benign.
    """
    venue = str(ev.get("fee_venue", "") or ev.get("venue", ""))
    if venue not in _DOCUMENTED_BENIGN_NON_PAPER_VENUES:
        return False
    fill_id, order_id = _fill_identity(ev)
    if fill_id not in _DOCUMENTED_BENIGN_NON_PAPER_FILL_IDS:
        return False
    if order_id not in _DOCUMENTED_BENIGN_NON_PAPER_ORDER_IDS:
        return False
    stamp = str(ev.get("timestamp_utc") or ev.get("filled_at") or "")
    if not stamp:
        return False
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        epoch = datetime.fromisoformat(_PAPER_EPOCH_START_UTC)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when < epoch


@dataclass(frozen=True)
class WindowSafety:
    """Hard audit assertions. The whole point: prove no live leak happened.

    ``live_orders_attempted`` is DERIVED from the data (count of fills whose
    venue is not a paper venue), not assumed to be 0.
    ``live_orders_unexplained`` excludes the documented-benign legacy marker —
    the tripwire figure that MUST be 0. ``auto_promotions`` is structurally 0 —
    this report and the edge gate never flip ``entry_mode``; promotion is always
    an explicit operator action.
    """

    live_orders_attempted: int
    live_orders_attempted_derivation: str
    live_orders_unexplained: int
    entry_mode_blocked: int
    auto_promotions: int
    non_paper_venues_seen: list[str]
    # STAB-2026-09-01 §1: the actual rows behind the counts, so the operator note
    # below can be DERIVED from them instead of asserting a fixed sentence.
    non_paper_benign_rows: list[dict[str, Any]] = field(default_factory=list)
    non_paper_unexplained_rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "live_orders_attempted": self.live_orders_attempted,
            "live_orders_attempted_derivation": self.live_orders_attempted_derivation,
            "live_orders_unexplained": self.live_orders_unexplained,
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
    # Kosten-Wahrheit (2026-06-26): the PRE-cost (gross) edge + the modelled
    # round-trip cost, so the panel can answer "is the loss a signal problem or
    # a cost problem?". breakeven cost = gross_mean_bps. Defaulted to keep
    # existing keyword/test constructors valid.
    gross_mean_bps: float = 0.0
    gross_median_bps: float = 0.0
    p_mu_gross_positive: float | None = None
    cost_roundtrip_bps: float = 0.0

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
            "gross_mean_bps": round(self.gross_mean_bps, 4),
            "gross_median_bps": round(self.gross_median_bps, 4),
            "p_mu_gross_positive": (
                None if self.p_mu_gross_positive is None else round(self.p_mu_gross_positive, 4)
            ),
            "cost_roundtrip_bps": round(self.cost_roundtrip_bps, 4),
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
    # Canonical-edge source filter (None = full stream, every source counts).
    # When set, the EDGE figures are restricted to these signal_source values;
    # counts + safety still see ALL rows. Surfaced so a contaminated (unfiltered)
    # measurement is always visible rather than silently mixed.
    source_allowlist: tuple[str, ...] | None = None
    closes_excluded_by_source: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "report_version": self.report_version,
            "cost_model_version": self.cost_model_version,
            "gate_version": self.gate_version,
            "quarantine_version": self.quarantine_version,
            "quarantine_signature_count": self.quarantine_signature_count,
            "source_allowlist": list(self.source_allowlist)
            if self.source_allowlist is not None
            else None,
            "closes_excluded_by_source": self.closes_excluded_by_source,
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
    implausible_move_threshold: float = DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD,
    source_allowlist: frozenset[str] | None = None,
) -> EvidenceWindowReport:
    """Build the joined evidence window from parsed events. Pure / IO-free.

    ``loop_events`` are raw ``trading_loop_audit`` rows (carry ``status``).
    ``exec_events`` are raw ``paper_execution_audit`` rows (``order_filled`` /
    ``position_closed``). Both must already be windowed by the caller if a
    sub-range is wanted; this function reports over exactly what it is handed.

    ``source_allowlist`` (e.g. ``CANONICAL_EDGE_SOURCES``) restricts the EDGE to
    closes whose ``signal_source`` is in the set; counts + safety still see ALL
    rows. ``None`` = full stream (every close counts) — backward-compatible.
    """
    cm = cost_model or CostModel()
    loop_list = [e for e in loop_events if isinstance(e, dict)]
    exec_list = [e for e in exec_events if isinstance(e, dict)]

    counts, window_bounds = _build_counts(loop_list, exec_list)
    safety = _build_safety(loop_list, exec_list)

    closed, excluded = parse_closed_trades_with_exclusions(
        exec_list, implausible_move_threshold=implausible_move_threshold
    )
    # join the quarantine tally into the cycle-level count view
    counts = _with_quarantine_count(counts, excluded.excluded_count)

    # Canonical-edge source filter: restrict the EDGE to the real generator's
    # attributed sources. counts + safety above already saw every row, so this
    # shapes ONLY the edge. Closes the 2026-06-23 epoch contamination where
    # unattributed May-canary closes faked a positive ETH cohort, AND (via
    # edge_source_of) the 2026-06-29 mis-attribution where pre-fix cohort closes
    # leaked into the autonomous edge under a stale signal_source.
    source_filter_tuple: tuple[str, ...] | None = None
    closes_excluded_by_source = 0
    if source_allowlist is not None:
        source_filter_tuple = tuple(sorted(source_allowlist))
        kept = [t for t in closed if edge_source_of(t) in source_allowlist]
        closes_excluded_by_source = len(closed) - len(kept)
        closed = kept

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
        source_allowlist=source_filter_tuple,
        closes_excluded_by_source=closes_excluded_by_source,
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
    unexplained = 0
    non_paper: set[str] = set()
    benign_rows: list[dict[str, Any]] = []
    unexplained_rows: list[dict[str, Any]] = []
    for ev in exec_list:
        if ev.get("event_type") != "order_filled":
            continue
        venue = str(ev.get("fee_venue", "") or ev.get("venue", ""))
        if not _is_paper_venue(venue):
            live_attempts += 1
            non_paper.add(venue or "<unknown>")
            if is_documented_benign_non_paper(ev):
                benign_rows.append(dict(ev))
            else:
                unexplained += 1
                unexplained_rows.append(dict(ev))
    derivation = (
        "count of order_filled events whose fee_venue/venue is not a paper venue "
        "(paper|sim|empty). 0 confirms every fill in the window was simulated; the "
        "paper engine also hard-blocks live_enabled=True at construction "
        "(PaperExecutionEngine), so this is a defence-in-depth count, not the only "
        "guard. live_orders_unexplained excludes ONLY the two forensically "
        "classified pre-epoch fills (see is_documented_benign_non_paper: pinned "
        "fill_id AND order_id AND timestamp < paper epoch start) and is the "
        "tripwire figure that MUST be 0. A 'legacy' label alone no longer excuses "
        "anything."
    )
    return WindowSafety(
        live_orders_attempted=live_attempts,
        live_orders_attempted_derivation=derivation,
        live_orders_unexplained=unexplained,
        entry_mode_blocked=entry_mode_blocked,
        # structurally 0: neither this report nor the edge gate flips entry_mode.
        auto_promotions=0,
        non_paper_venues_seen=sorted(non_paper),
        non_paper_benign_rows=benign_rows,
        non_paper_unexplained_rows=unexplained_rows,
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
        gross_mean_bps=overall.gross_bps_mean,
        gross_median_bps=overall.gross_bps_median,
        p_mu_gross_positive=overall.p_mu_gross_positive,
        cost_roundtrip_bps=(
            overall.fee_bps_mean + overall.spread_bps_mean + overall.slippage_bps_mean
        ),
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
    if safety.live_orders_unexplained > 0:
        notes.append(
            f"*** {safety.live_orders_unexplained} UNEXPLAINED NON-PAPER FILL(S) DETECTED "
            f"({', '.join(safety.non_paper_venues_seen)}) — investigate immediately. "
            "The window is supposed to be paper-only."
        )
    elif safety.live_orders_attempted > 0:
        # STAB-2026-09-01 §1: this sentence used to be a fixed f-string asserting
        # "epoch-fremde Mai-Closes" no matter what the rows contained -- feeding
        # the module a synthetic September BUY produced the same words. It is now
        # DERIVED from the rows, so it can be wrong out loud instead of being
        # unfalsifiable. (It also called both fills "Closes"; one is a buy entry.)
        notes.append(_describe_benign_non_paper(safety))
    if (
        edge.trade_count > 0
        and edge.result_without_best_trade.mean_net_bps < 0 <= edge.mean_net_bps
    ):
        notes.append(
            "OUTLIER WARNING: removing the single best trade turns the mean net "
            "edge NEGATIVE. The apparent edge is carried by one trade, not a process."
        )
    return notes


def _describe_benign_non_paper(safety: WindowSafety) -> str:
    """Operator note for exempted non-paper fills, computed from the rows."""
    rows = safety.non_paper_benign_rows
    n = safety.live_orders_attempted
    venues = ", ".join(safety.non_paper_venues_seen) or "<unknown>"
    if not rows:
        return (
            f"{n} non-paper fill(s) ({venues}) — exempt by classification, but the "
            "underlying rows were not retained for this window; treat as UNVERIFIED."
        )
    stamps = sorted(str(r.get("timestamp_utc") or r.get("filled_at") or "") for r in rows if r)
    sides = sorted({str(r.get("side", "?")).lower() for r in rows})
    symbols = sorted({str(r.get("symbol", "?")) for r in rows})
    first, last = stamps[0][:10], stamps[-1][:10]
    span = first if first == last else f"{first}..{last}"
    return (
        f"{len(rows)} of {n} non-paper fill(s) exempt: pinned pre-epoch rows "
        f"({venues}) dated {span}, {'/'.join(sides)} on {', '.join(symbols)}. "
        f"Classified B (paper fill, mislabelled provenance marker from the "
        f"2026-05-03..05-05 venue-default window); epoch-foreign is a secondary "
        f"property. No exchange_order_id on either row — no live leak."
    )


# === audit-stream IO (thin edge) ===============================================


def _parse_jsonl_lines(lines: Iterable[str], *, source: str = "<lines>") -> list[dict[str, Any]]:
    """Parse an iterable of JSONL text lines into dict rows (tolerant).

    Blank lines are skipped; malformed lines are logged and dropped (never raise);
    non-object rows are ignored. Shared by the streaming file loader and the
    explicit-lines reconstruction path so both parse byte-identically.
    """
    out: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("[evidence_window] skipping malformed audit line in %s", source)
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        logger.warning("[evidence_window] audit file not found: %s", p)
        return []
    # KAI-01: stream the (multi-MB) audit file line-by-line instead of
    # ``read_text().splitlines()`` to avoid the full-file RAM peak on the Pi.
    with p.open(encoding="utf-8") as handle:
        return _parse_jsonl_lines(handle, source=str(p))


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
    implausible_move_threshold: float = DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD,
    source_allowlist: frozenset[str] | None = None,
) -> EvidenceWindowReport:
    """Load both audit files and build the window end-to-end.

    ``since`` / ``until`` (tz-aware UTC) bound the window; rows outside are
    dropped before aggregation. With no bounds the full streams are used.
    ``source_allowlist`` is threaded to :func:`build_evidence_window` (canonical
    edge); ``None`` = full stream.
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
        implausible_move_threshold=implausible_move_threshold,
        source_allowlist=source_allowlist,
    )


def build_window_from_lines(
    *,
    loop_lines: Sequence[str],
    exec_lines: Sequence[str],
    since: datetime | None = None,
    until: datetime | None = None,
    cost_model: CostModel | None = None,
    venue: str = "paper",
    safety_margin_bps: float = 0.0,
    p_threshold_bps: float = 0.0,
    trim_fraction: float = _DEFAULT_TRIM_FRACTION,
    bootstrap_n: int = _DEFAULT_BOOTSTRAP_N,
    min_sample: int = MIN_SAMPLE_FOR_P,
    implausible_move_threshold: float = DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD,
    source_allowlist: frozenset[str] | None = None,
) -> EvidenceWindowReport:
    """Build the window from EXPLICIT JSONL lines (not file paths).

    Same parsing/filtering/aggregation as :func:`build_window_from_audit`, but the
    caller supplies the raw lines. This is the reconstruction entry point for
    verifiable attestation (B5b): a verifier hashes the pinned prefix, then rebuilds
    the exact report from those very lines — decoupled from later appends to the
    live file. For unfiltered inputs it is byte-identical to
    :func:`build_window_from_audit` over the same content.
    """
    loop_events = _filter_window(
        _parse_jsonl_lines(loop_lines),
        ts_keys=("started_at", "completed_at", "timestamp_utc"),
        since=since,
        until=until,
    )
    exec_events = _filter_window(
        _parse_jsonl_lines(exec_lines),
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
        implausible_move_threshold=implausible_move_threshold,
        source_allowlist=source_allowlist,
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
    if w.source_allowlist is not None:
        lines.append(
            f"  edge_source_filter: CANONICAL ({', '.join(w.source_allowlist)}) — "
            f"{w.closes_excluded_by_source} close(s) excluded from edge by source"
        )
    else:
        lines.append(
            "  edge_source_filter: FULL STREAM (all sources, incl. unattributed) — "
            "use `canonical-edge` for the real-generator answer"
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
    lines.append(
        f"  live_orders_unexplained = {s.live_orders_unexplained}   (MUST be 0 — tripwire)"
    )
    lines.append(
        f"  live_orders_attempted = {s.live_orders_attempted}   "
        "(inkl. dokumentiert-benigner legacy-Marker)"
    )
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
