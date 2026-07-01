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

import math
from collections import Counter, defaultdict
from typing import Any

from app.observability.l2_evidence_eval import moving_block_bootstrap_p_mean_positive
from app.research.news_outcomes import NEWS_HORIZONS_S
from app.research.news_stories import DEFAULT_STORY_WINDOW_S, cluster_stories, dedup_stats
from app.research.shadow_evidence_eval import (
    DEFAULT_COST_BPS,
    DEFAULT_MAX_CONCENTRATION,
)

# A source contributes to the cross-source pooled estimate only with this many
# observations at the horizon — below that its variance estimate is junk.
MIN_POOL_N = 8


def evaluate_cohort(
    outcomes: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = NEWS_HORIZONS_S,
    cost_bps: float = DEFAULT_COST_BPS,
    max_concentration: float = DEFAULT_MAX_CONCENTRATION,
    cost_by_symbol: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Score one cohort (all outcomes, or one source's) across horizons.

    ``outcomes`` must be time-ordered (as :func:`build_news_outcomes` returns) so the
    moving-block bootstrap preserves autocorrelation. With ``cost_by_symbol`` the
    cost bar is the cohort's mean PER-SYMBOL cost (venue floor + liquidity tier)
    instead of one flat number — reported per horizon as ``cost_ref_bps``.
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
        if cost_by_symbol and pairs:
            cost_ref = sum(cost_by_symbol.get(sym, cost_bps) for _, sym in pairs) / n
        else:
            cost_ref = cost_bps
        is_actionable = (
            mean >= cost_ref
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
            "cost_ref_bps": round(cost_ref, 2),
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


def pool_sources(
    by_source: dict[str, list[dict[str, Any]]],
    horizon: int,
    *,
    min_pool_n: int = MIN_POOL_N,
) -> dict[str, Any] | None:
    """Inverse-variance-weighted fixed-effect pool of per-source means at one horizon.

    Per-source cohorts are structurally underpowered (a few dozen events each);
    pooling across sources answers the question the per-source tables cannot:
    *does news direction carry ANY forward return at this horizon, using all
    sources' evidence together?* Returns pooled mean/se, a normal-approximation
    ``P(mean>0)``, and Cochran's Q / I² so cross-source heterogeneity (one source
    driving everything) is visible instead of averaged away. ``None`` when fewer
    than two sources qualify.
    """
    stats: list[tuple[str, float, float, int]] = []  # (source, mean, se, n)
    for src, rows in by_source.items():
        vals = [v for o in rows if (v := o["fwd"].get(horizon)) is not None]
        n = len(vals)
        if n < min_pool_n:
            continue
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / (n - 1)
        if var <= 0:
            continue
        stats.append((src, mean, math.sqrt(var / n), n))
    if len(stats) < 2:
        return None
    weights = [1.0 / se**2 for _, _, se, _ in stats]
    w_total = sum(weights)
    pooled_mean = sum(w * m for w, (_, m, _, _) in zip(weights, stats, strict=True)) / w_total
    pooled_se = math.sqrt(1.0 / w_total)
    z = pooled_mean / pooled_se
    p_positive = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    q = sum(w * (m - pooled_mean) ** 2 for w, (_, m, _, _) in zip(weights, stats, strict=True))
    df = len(stats) - 1
    i_squared = max(0.0, (q - df) / q) if q > 0 else 0.0
    return {
        "k_sources": len(stats),
        "n_total": sum(n for _, _, _, n in stats),
        "pooled_mean_bps": round(pooled_mean, 2),
        "pooled_se_bps": round(pooled_se, 2),
        "z": round(z, 2),
        "p_positive_normal": round(p_positive, 4),
        "i_squared": round(i_squared, 3),
        "sources": [s for s, _, _, _ in stats],
    }


def evaluate_news(
    outcomes: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = NEWS_HORIZONS_S,
    cost_bps: float = DEFAULT_COST_BPS,
    max_concentration: float = DEFAULT_MAX_CONCENTRATION,
    cost_by_symbol: dict[str, float] | None = None,
    story_window_s: float = DEFAULT_STORY_WINDOW_S,
) -> dict[str, Any]:
    """Overall + story-level + per-source + cross-source-pooled evaluation.

    ``stories`` is the CLUSTER-ROBUST headline: cross-source coverage of the same
    (symbol, side) within ``story_window_s`` collapses to one observation (first
    event), so syndicated news cannot inflate the effective sample. ``overall``/
    ``pooled`` stay on raw outcomes for comparison — the gap between the two IS
    the duplication pressure, reported in ``stories_meta``.
    """
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in outcomes:
        by_source[str(o.get("source", "unknown"))].append(o)
    per_source = {
        src: evaluate_cohort(
            rows,
            horizons=horizons,
            cost_bps=cost_bps,
            max_concentration=max_concentration,
            cost_by_symbol=cost_by_symbol,
        )
        for src, rows in by_source.items()
    }
    pooled = {h: pool_sources(by_source, h) for h in horizons}
    stories = cluster_stories(outcomes, window_s=story_window_s)
    return {
        "cost_bps": round(cost_bps, 2),
        "overall": evaluate_cohort(
            outcomes,
            horizons=horizons,
            cost_bps=cost_bps,
            max_concentration=max_concentration,
            cost_by_symbol=cost_by_symbol,
        ),
        "stories": evaluate_cohort(
            stories,
            horizons=horizons,
            cost_bps=cost_bps,
            max_concentration=max_concentration,
            cost_by_symbol=cost_by_symbol,
        ),
        "stories_meta": {**dedup_stats(outcomes, stories), "window_s": story_window_s},
        "per_source": per_source,
        "pooled": pooled,
    }


def render(
    res: dict[str, Any],
    *,
    horizons: tuple[int, ...] = NEWS_HORIZONS_S,
    min_n: int = 20,
) -> str:
    """Compact markdown: overall table + per-source tables with n>=``min_n``."""
    cost = res.get("cost_bps", DEFAULT_COST_BPS)
    lines = [f"## Directional-news forward return (base cost={cost}bps, side-adjusted)"]
    lines.append(_render_cohort("ALL sources", res["overall"], horizons))
    if "stories" in res:
        sm = res.get("stories_meta", {})
        lines.append(
            _render_cohort(
                f"STORIES (deduped: {sm.get('n_raw', '?')} raw -> "
                f"{sm.get('n_stories', '?')} stories, ratio {sm.get('dedup_ratio', '?')})",
                res["stories"],
                horizons,
            )
        )
    pooled = res.get("pooled") or {}
    pooled_lines = [
        f"| {_horizon_label(h)} | {p['k_sources']} | {p['n_total']} | "
        f"{p['pooled_mean_bps']}±{p['pooled_se_bps']} | {p['z']} | "
        f"{p['p_positive_normal']} | {p['i_squared']} |"
        for h in horizons
        if (p := pooled.get(h)) is not None
    ]
    if pooled_lines:
        lines.append("\n### Pooled across sources (IVW fixed-effect)")
        lines.append("| horizon | k | n | mean±se (bps) | z | P(mean>0) | I² |")
        lines.append("|---|---|---|---|---|---|---|")
        lines.extend(pooled_lines)
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
    lines.append("| horizon | n | mean_bps | hit | P(mean>0) | top-sym% | cost | act |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for h in horizons:
        r = cohort["horizons"][h]
        p = r["p_positive"]
        p_str = f"{p:.3f}" if p is not None else "n/a"
        act = "✅" if r["actionable"] else "—"
        label = _horizon_label(h)
        cost_ref = r.get("cost_ref_bps", "-")
        lines.append(
            f"| {label} | {r['n']} | {r['mean_bps']} | {r['hit']} | "
            f"{p_str} | {r['top_symbol_share']} | {cost_ref} | {act} |"
        )
    return "\n".join(lines)


def _horizon_label(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}min"
    return f"{seconds}s"


__all__ = ["MIN_POOL_N", "evaluate_cohort", "evaluate_news", "pool_sources", "render"]
