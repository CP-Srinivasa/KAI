"""Hypothesis search — compose the engine into an honest edge verdict.

Pipeline per hypothesis (a named decider ``FeatureRow -> side``):

    rule -> net-bps trades (samples) -> summary stats -> time-bucket consistency

Then across the WHOLE hypothesis set, Benjamini-Hochberg controls the false
discovery rate over the one-sided p-values. A hypothesis only "survives" when
ALL of the following hold:

    * BH rejects its null at ``alpha`` (FDR-controlled), AND
    * its mean net return is strictly positive (right direction), AND
    * it has at least ``min_trades`` trades (not a thin fluke), AND
    * it is positive in at least ``min_bucket_consistency`` of its time buckets
      (an edge that holds across sub-periods, not one lucky window).

Note on discipline: the rules here are NOT fitted, so in-sample == out-of-sample
for a *single* rule — the real overfitting risk is SELECTION across many rules,
which the BH gate addresses directly. The time-bucket check is a robustness
filter, deliberately not labelled "walk-forward" (nothing is trained per fold).
The honest outcome of a search may be zero survivors — that is a valid result,
not a failure of the engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.features.feature_matrix import FeatureRow
from app.research.multiple_testing import benjamini_hochberg
from app.research.samples import Decider, decisions_to_trades
from app.research.stats import NetSummary, summarize_net_bps


@dataclass(frozen=True)
class HypothesisResult:
    """Per-hypothesis evaluation, before cross-hypothesis correction."""

    name: str
    summary: NetSummary
    n_buckets: int  # non-empty time buckets
    n_buckets_positive: int


@dataclass(frozen=True)
class SearchVerdict:
    """A hypothesis's result plus its post-correction survival decision."""

    name: str
    result: HypothesisResult
    survives: bool


@dataclass(frozen=True)
class SearchReport:
    """Outcome of evaluating a hypothesis set."""

    verdicts: list[SearchVerdict]
    n_hypotheses: int
    n_survivors: int
    alpha: float


def _bucket_positivity(net_bps: list[float], n_buckets: int) -> tuple[int, int]:
    """(positive_bucket_count, non_empty_bucket_count) over contiguous time slices."""
    n = len(net_bps)
    if n == 0 or n_buckets < 1:
        return (0, 0)
    k = min(n_buckets, n)
    positive = 0
    total = 0
    for b in range(k):
        lo = b * n // k
        hi = (b + 1) * n // k
        chunk = net_bps[lo:hi]
        if not chunk:
            continue
        total += 1
        if sum(chunk) / len(chunk) > 0:
            positive += 1
    return (positive, total)


def evaluate_hypothesis(
    name: str,
    rows: list[FeatureRow],
    forward_bps: list[float | None],
    decide: Decider,
    round_trip_cost_bps: float,
    n_buckets: int = 5,
) -> HypothesisResult:
    """Evaluate one hypothesis into stats + time-bucket consistency."""
    trades = decisions_to_trades(rows, forward_bps, decide, round_trip_cost_bps)
    net = [t.net_bps for t in trades]
    summary = summarize_net_bps(net)
    positive, total = _bucket_positivity(net, n_buckets)
    return HypothesisResult(
        name=name, summary=summary, n_buckets=total, n_buckets_positive=positive
    )


def search_hypotheses(
    hypotheses: list[tuple[str, Decider]],
    rows: list[FeatureRow],
    forward_bps: list[float | None],
    round_trip_cost_bps: float,
    alpha: float = 0.05,
    n_buckets: int = 5,
    min_trades: int = 30,
    min_bucket_consistency: float = 0.6,
) -> SearchReport:
    """Evaluate a hypothesis set and decide survivors under BH FDR control."""
    results = [
        evaluate_hypothesis(name, rows, forward_bps, decide, round_trip_cost_bps, n_buckets)
        for name, decide in hypotheses
    ]
    rejected = benjamini_hochberg([r.summary.p_value for r in results], alpha)

    verdicts: list[SearchVerdict] = []
    survivors = 0
    for result, is_rejected in zip(results, rejected, strict=True):
        consistent = (
            result.n_buckets > 0
            and result.n_buckets_positive / result.n_buckets >= min_bucket_consistency
        )
        survives = bool(
            is_rejected
            and result.summary.mean_bps > 0
            and result.summary.n >= min_trades
            and consistent
        )
        survivors += int(survives)
        verdicts.append(SearchVerdict(name=result.name, result=result, survives=survives))

    return SearchReport(
        verdicts=verdicts,
        n_hypotheses=len(hypotheses),
        n_survivors=survivors,
        alpha=alpha,
    )
