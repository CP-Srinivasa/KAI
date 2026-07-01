"""Directional-news signal evaluator — does acting on a source's news direction pay?

Consumes the side-adjusted outcome pool from :mod:`app.research.news_outcomes`
(each ``fwd`` is already ``+`` when the source's declared direction was right) and
answers, per source and overall, per horizon: mean net-of-cost forward return,
hit rate, autocorrelation-robust ``P(mean>0)``, single-symbol concentration, and a
conservative ACTIONABLE gate.

The gate mirrors the aligned-evidence evaluator exactly (mean clears cost AND
bootstrap-significant AND not a single-symbol monoculture) so verdicts are
comparable across signal families. Read-only; a positive verdict is a HYPOTHESIS,
not a trust-flip — promotion stays operator- AND edge-gated downstream.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.observability.l2_evidence_eval import moving_block_bootstrap_p_mean_positive
from app.research.news_outcomes import NEWS_HORIZONS_S
from app.research.shadow_evidence_eval import (
    DEFAULT_COST_BPS,
    DEFAULT_MAX_CONCENTRATION,
)


def evaluate_cohort(
    outcomes: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = NEWS_HORIZONS_S,
    cost_bps: float = DEFAULT_COST_BPS,
    max_concentration: float = DEFAULT_MAX_CONCENTRATION,
) -> dict[str, Any]:
    """Score one cohort (all outcomes, or one source's) across horizons.

    ``outcomes`` must be time-ordered (as :func:`build_news_outcomes` returns) so the
    moving-block bootstrap preserves autocorrelation.
    """
    horizons_out: dict[int, dict[str, Any]] = {}
    actionable = False
    for h in horizons:
        pairs = [(v, o["symbol"]) for o in outcomes if (v := o["fwd"].get(h)) is not None]
        vals = [v for v, _ in pairs]
        n = len(vals)
        mean = sum(vals) / n if n else 0.0
        hit = sum(1 for v in vals if v > 0) / n if n else 0.0
        p_pos = moving_block_bootstrap_p_mean_positive(vals) if n else None
        sym_share = 0.0
        if pairs:
            top = Counter(sym for _, sym in pairs).most_common(1)[0][1]
            sym_share = top / n
        is_actionable = (
            mean >= cost_bps
            and p_pos is not None
            and p_pos > 0.95
            and sym_share <= max_concentration
        )
        actionable = actionable or is_actionable
        horizons_out[h] = {
            "n": n,
            "mean_bps": round(mean, 2),
            "hit": round(hit, 3),
            "p_positive": p_pos,
            "top_symbol_share": round(sym_share, 3),
            "actionable": is_actionable,
        }
    return {
        "n": len(outcomes),
        "horizons": horizons_out,
        "actionable": actionable,
        "verdict": (
            "ACTIONABLE (hypothesis — operator+edge-gated)"
            if actionable
            else "SHADOW_ONLY (no cost-clearing, confirmed, non-concentrated edge)"
        ),
    }


def evaluate_news(
    outcomes: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = NEWS_HORIZONS_S,
    cost_bps: float = DEFAULT_COST_BPS,
    max_concentration: float = DEFAULT_MAX_CONCENTRATION,
) -> dict[str, Any]:
    """Overall + per-source directional-news evaluation."""
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in outcomes:
        by_source[str(o.get("source", "unknown"))].append(o)
    per_source = {
        src: evaluate_cohort(
            rows,
            horizons=horizons,
            cost_bps=cost_bps,
            max_concentration=max_concentration,
        )
        for src, rows in by_source.items()
    }
    return {
        "cost_bps": round(cost_bps, 2),
        "overall": evaluate_cohort(
            outcomes,
            horizons=horizons,
            cost_bps=cost_bps,
            max_concentration=max_concentration,
        ),
        "per_source": per_source,
    }


def render(
    res: dict[str, Any],
    *,
    horizons: tuple[int, ...] = NEWS_HORIZONS_S,
    min_n: int = 20,
) -> str:
    """Compact markdown: overall table + per-source tables with n>=``min_n``."""
    cost = res.get("cost_bps", DEFAULT_COST_BPS)
    lines = [f"## Directional-news forward return (cost={cost}bps, side-adjusted)"]
    lines.append(_render_cohort("ALL sources", res["overall"], horizons))
    ranked = sorted(res["per_source"].items(), key=lambda kv: kv[1]["n"], reverse=True)
    shown = [(s, c) for s, c in ranked if c["n"] >= min_n]
    for src, cohort in shown:
        lines.append(_render_cohort(src, cohort, horizons))
    hidden = len(ranked) - len(shown)
    if hidden > 0:
        lines.append(f"\n_({hidden} source(s) below min_n={min_n} omitted)_")
    return "\n".join(lines)


def _render_cohort(name: str, cohort: dict[str, Any], horizons: tuple[int, ...]) -> str:
    lines = [f"\n### {name} — n={cohort['n']}  →  {cohort['verdict']}"]
    lines.append("| horizon | n | mean_bps | hit | P(mean>0) | top-sym% | act |")
    lines.append("|---|---|---|---|---|---|---|")
    for h in horizons:
        r = cohort["horizons"][h]
        p = r["p_positive"]
        p_str = f"{p:.3f}" if p is not None else "n/a"
        act = "✅" if r["actionable"] else "—"
        label = _horizon_label(h)
        lines.append(
            f"| {label} | {r['n']} | {r['mean_bps']} | {r['hit']} | "
            f"{p_str} | {r['top_symbol_share']} | {act} |"
        )
    return "\n".join(lines)


def _horizon_label(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds}s"


__all__ = ["evaluate_cohort", "evaluate_news", "render"]
