"""Net-bps sample summary — pure, deterministic statistics.

Summarizes a list of per-trade net returns (in bps) into the numbers a
hypothesis search needs: sample size, mean, hit-rate, t-statistic, and a
one-sided p-value for H0: mean <= 0 (i.e. "no positive edge").

The p-value uses a normal approximation of the t-statistic's right tail via
``math.erfc`` — deterministic, no SciPy, no RNG. It is an approximation (valid
for n large; for crypto-bar samples n is typically >= 30) and is intentionally
conservative for tiny samples: with fewer than 2 trades no significance can be
claimed (p = 1.0). This summary serves the research/search layer; the live
trade-ledger path keeps its own bootstrap estimate in ``observability``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class NetSummary:
    """Summary statistics of a net-bps sample."""

    n: int
    mean_bps: float
    std_bps: float
    hit_rate: float
    t_stat: float
    p_value: float  # one-sided, H0: mean <= 0


def summarize_net_bps(net_bps: list[float]) -> NetSummary:
    """Summarize per-trade net-bps returns.

    Args:
        net_bps: realized net returns in basis points (one per trade).

    Returns:
        NetSummary. Empty input and single-sample input both yield a
        non-significant p-value of 1.0 (insufficient evidence).
    """
    n = len(net_bps)
    if n == 0:
        return NetSummary(n=0, mean_bps=0.0, std_bps=0.0, hit_rate=0.0, t_stat=0.0, p_value=1.0)

    mean = sum(net_bps) / n
    hit_rate = sum(1 for x in net_bps if x > 0) / n

    if n < 2:
        # One observation: cannot estimate dispersion -> no significance claim.
        return NetSummary(
            n=n, mean_bps=mean, std_bps=0.0, hit_rate=hit_rate, t_stat=0.0, p_value=1.0
        )

    var = sum((x - mean) ** 2 for x in net_bps) / (n - 1)
    std = math.sqrt(var)

    if std == 0.0:
        # Degenerate: every trade identical. Significant iff strictly positive.
        t_stat = math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0)
        p_value = 0.0 if mean > 0 else 1.0
        return NetSummary(
            n=n, mean_bps=mean, std_bps=0.0, hit_rate=hit_rate, t_stat=t_stat, p_value=p_value
        )

    se = std / math.sqrt(n)
    t_stat = mean / se
    # One-sided right-tail p-value via normal approximation: 0.5 * erfc(t/sqrt2).
    p_value = 0.5 * math.erfc(t_stat / math.sqrt(2.0))
    p_value = min(1.0, max(0.0, p_value))
    return NetSummary(
        n=n, mean_bps=mean, std_bps=std, hit_rate=hit_rate, t_stat=t_stat, p_value=p_value
    )
