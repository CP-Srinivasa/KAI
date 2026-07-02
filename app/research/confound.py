"""Confound controls for the edge-discovery harness (beta/drift de-confounding).

Alts can trend (often down) over a window, so an ALWAYS-in-the-same-direction
strategy profits from *drift*, not from a signal's *timing*. A plain BH-FDR
survival screen does not separate the two, so a drifting alt can masquerade as a
signal edge. These helpers isolate the timing component so the harness cannot
mistake alt-beta for edge — operationalising the red-team confound requirement
behind the NORTH_STAR truth-pivot (ADR 0012).

Two complementary controls:

* :func:`beta_neutral_forward_returns` — de-mean a symbol's forward-return labels
  by the symbol's own window drift, so running the SAME decider against the
  de-confounded labels in the SAME BH-FDR batch tests "does the timing beat the
  drift?" rather than "is the asset falling?".
* :func:`timing_alpha` / :func:`timing_alpha_report` — the explicit per-symbol
  diagnostic: always-side net vs signal-timed-side net; ``timing_alpha`` ~ 0
  across symbols ⇒ the apparent edge is drift/beta, not timing.

IMPORTANT — in-sample baseline (read before trusting a survivor): the drift
baseline is the FULL-window mean of the symbol's forward returns. That is a
legitimate CONFOUND DIAGNOSTIC ("did the timing beat this window's drift?") but
NOT a tradeable label — the window mean is unknown at trade time. A beta-neutral
survivor is evidence AGAINST the alt-beta confound, not a live edge; a tradeable
claim needs an expanding-window / out-of-sample baseline and the full validation
gate. Pure, deterministic, no I/O.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.analysis.features.feature_matrix import FeatureRow
from app.analysis.features.forward_returns import compute_forward_return_bps

logger = logging.getLogger(__name__)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def beta_neutral_forward_returns(labels: Sequence[float | None]) -> list[float | None]:
    """Drift-de-mean a symbol's forward-return labels (in-sample confound control).

    Subtracts the mean of all non-None labels from each non-None label; None stays
    None (warm-up preserved, alignment intact). With no valid labels the input is
    returned unchanged. The result removes the symbol's window drift so a
    same-direction decider only profits from TIMING, not from the asset falling.

    NOTE: in-sample (full-window mean) — a confound diagnostic, not a tradeable
    label. See module docstring.
    """
    valid = [x for x in labels if x is not None]
    if not valid:
        return list(labels)
    mu = _mean(valid)
    return [None if x is None else x - mu for x in labels]


def timing_alpha(
    rows: Sequence[FeatureRow],
    closes: Sequence[float],
    horizon: int,
    cost_bps: float,
    *,
    timed: Callable[[FeatureRow], bool],
    side: int,
) -> dict[str, Any] | None:
    """Always-side net vs signal-timed-side net (cost-adjusted) for ONE symbol.

    ``side`` is +1 (long) or -1 (short); ``timed(row)`` selects the bars the signal
    would act on. Net per bar = ``side * fwd_return_bps - cost_bps``. Returns a dict
    with ``always_net_bps`` (the beta), ``timed_net_bps`` and ``timing_alpha_bps``
    (timed − always; >0 means the timing helped), or None if either set is empty.
    """
    labels = compute_forward_return_bps(list(closes), horizon)
    all_fwd = [x for x in labels if x is not None]
    timed_fwd = [x for i, r in enumerate(rows) if timed(r) and (x := labels[i]) is not None]
    if not all_fwd or not timed_fwd:
        return None
    always_net = side * _mean(all_fwd) - cost_bps
    timed_net = side * _mean(timed_fwd) - cost_bps
    return {
        "n_timed": len(timed_fwd),
        "always_net_bps": round(always_net, 2),
        "timed_net_bps": round(timed_net, 2),
        "timing_alpha_bps": round(timed_net - always_net, 2),
    }


def timing_alpha_report(
    per_symbol: Mapping[str, tuple[Sequence[FeatureRow], Sequence[float], int]],
    horizon: int,
    cost_bps: float,
    *,
    timed: Callable[[FeatureRow], bool],
    side: int,
    label: str = "timed",
) -> list[dict[str, Any]]:
    """Per-symbol :func:`timing_alpha`; logs each row. Symbols with no timed/forward
    bars are skipped. ``label`` only names the log line (e.g. "unlock-timed short").
    """
    out: list[dict[str, Any]] = []
    logger.info("CONFOUND (always vs %s net bps, side=%+d, h=%d):", label, side, horizon)
    for symbol, (rows, closes, _gap) in per_symbol.items():
        res = timing_alpha(rows, closes, horizon, cost_bps, timed=timed, side=side)
        if res is None:
            continue
        res = {"symbol": symbol, **res}
        out.append(res)
        logger.info(
            "  %-10s always=%+.1f  %s=%+.1f  timing_alpha=%+.1f (n=%d)",
            symbol,
            res["always_net_bps"],
            label,
            res["timed_net_bps"],
            res["timing_alpha_bps"],
            res["n_timed"],
        )
    return out
