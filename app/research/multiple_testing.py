"""Benjamini-Hochberg false-discovery-rate control — pure function.

When many hypotheses are tested, some will look significant by chance. BH
controls the expected proportion of false discoveries among the rejected nulls
at level ``alpha``:

    Sort p-values ascending p_(1) <= ... <= p_(m). Let k be the largest rank
    with p_(k) <= (k / m) * alpha. Reject the nulls for ranks 1..k.

This is a *step-up* procedure: a hypothesis can be rejected even if its own
p-value exceeds its threshold, provided a higher-ranked one passes. Without
this gate, a wide feature search manufactures "edge" from pure noise — the exact
failure mode this engine exists to avoid.
"""

from __future__ import annotations


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """Return a rejection mask (True = significant) under BH FDR control.

    Args:
        p_values: one-sided p-values, each in [0, 1].
        alpha: target false-discovery rate, in (0, 1].

    Returns:
        list[bool] aligned to ``p_values``; True where the null is rejected.

    Raises:
        ValueError: alpha not in (0, 1], or any p-value outside [0, 1].
    """
    if not 0.0 < alpha <= 1.0:
        raise ValueError("alpha must be in (0, 1]")
    for p in p_values:
        if not 0.0 <= p <= 1.0:
            raise ValueError("p-values must be in [0, 1]")

    m = len(p_values)
    if m == 0:
        return []

    ranked = sorted(range(m), key=lambda i: p_values[i])  # ascending by p
    max_rank = 0
    for rank, idx in enumerate(ranked, start=1):
        if p_values[idx] <= (rank / m) * alpha:
            max_rank = rank

    rejected = [False] * m
    for rank, idx in enumerate(ranked, start=1):
        if rank <= max_rank:
            rejected[idx] = True
    return rejected
