"""Aligned-evidence shadow evaluator (the "V5 way"), lifted into one place.

Some shadow evidence streams (funding, open interest) pre-declare a DIRECTION for
each measurement via ``evidence_direction_aligned`` (+1 = agrees with the
candidate's side, -1 = disagrees, 0 = neutral). This module answers the honest
question "do candidates the evidence AGREES with actually pay, net of cost?" —
outcome-centric, autocorrelation-robust, and conservative about calling anything
tradeable.

  * :func:`index_evidence` / :func:`nearest_aligned` — outcome-centric join: pair
    each outcome with the temporally nearest same-symbol+side evidence within a
    tolerance window.
  * :func:`evaluate_signal` — per horizon, split joined outcomes by aligned +1/-1,
    report mean/hit/spread + moving-block-bootstrap ``P(mean(+1)>0)`` +
    single-symbol concentration, and gate ACTIONABLE conservatively.
  * :func:`render` — the compact operator/markdown table.

Read-only; a learned direction is a HYPOTHESIS. Trust-promotion stays operator-
AND edge-gated downstream — nothing here flips a switch.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from app.observability.l2_evidence_eval import moving_block_bootstrap_p_mean_positive
from app.research.shadow_outcomes import HORIZONS, parse_ts

_SIDES = ("long", "short")

# Conservative defaults: a horizon is ACTIONABLE only if it clears cost, is
# bootstrap-significant, and is not a single-symbol monoculture.
DEFAULT_TOL_S = 300.0
DEFAULT_COST_BPS = 20.0
DEFAULT_MAX_CONCENTRATION = 0.8


def index_evidence(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], list[tuple[datetime, int]]]:
    """Index aligned evidence by (symbol, side), time-sorted, for nearest lookup."""
    by: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)
    for r in records:
        sym = r.get("symbol")
        side = r.get("direction")
        ts = parse_ts(r.get("ts"))
        aligned = r.get("evidence_direction_aligned")
        if not sym or side not in _SIDES or ts is None or aligned is None:
            continue
        by[(str(sym), str(side))].append((ts, int(aligned)))
    for key in by:
        by[key].sort(key=lambda p: p[0])
    return by


def nearest_aligned(
    outcome: dict[str, Any],
    ev_index: dict[tuple[str, str], list[tuple[datetime, int]]],
    *,
    tol_s: float,
) -> int | None:
    """Nearest same-symbol+side evidence within +/- ``tol_s``; its aligned (+1/-1/0)."""
    lst = ev_index.get((outcome["symbol"], outcome["side"]))
    if not lst:
        return None
    ets = outcome["entry_ts"]
    best: int | None = None
    best_dt: float | None = None
    for ts, aligned in lst:
        dt = abs((ts - ets).total_seconds())
        if dt <= tol_s and (best_dt is None or dt < best_dt):
            best_dt, best = dt, aligned
    return best


def evaluate_signal(
    outcomes: list[dict[str, Any]],
    ev_index: dict[tuple[str, str], list[tuple[datetime, int]]],
    *,
    tol_s: float = DEFAULT_TOL_S,
    cost_bps: float = DEFAULT_COST_BPS,
    max_concentration: float = DEFAULT_MAX_CONCENTRATION,
) -> dict[str, Any]:
    """Score one aligned-evidence stream against the (time-ordered) outcome pool."""
    joined: list[tuple[int, dict[int, float | None], str]] = []
    for o in outcomes:  # outcomes already time-sorted → joined preserves order
        a = nearest_aligned(o, ev_index, tol_s=tol_s)
        if a is None or a == 0:
            continue
        joined.append((a, o["fwd"], o["symbol"]))

    horizons: dict[int, dict[str, Any]] = {}
    actionable = False
    for h in HORIZONS:
        plus = [(v, sym) for a, f, sym in joined if a == 1 and (v := f.get(h)) is not None]
        minus = [v for a, f, sym in joined if a == -1 and (v := f.get(h)) is not None]
        plus_vals = [v for v, _ in plus]
        n_plus, n_minus = len(plus_vals), len(minus)
        mean_plus = sum(plus_vals) / n_plus if n_plus else 0.0
        mean_minus = sum(minus) / n_minus if n_minus else 0.0
        hit_plus = sum(1 for v in plus_vals if v > 0) / n_plus if n_plus else 0.0
        hit_minus = sum(1 for v in minus if v > 0) / n_minus if n_minus else 0.0
        p_plus_pos = moving_block_bootstrap_p_mean_positive(plus_vals) if n_plus else None
        sym_share = 0.0
        if plus:
            top = Counter(sym for _, sym in plus).most_common(1)[0][1]
            sym_share = top / n_plus
        is_actionable = (
            mean_plus >= cost_bps
            and p_plus_pos is not None
            and p_plus_pos > 0.95
            and sym_share <= max_concentration
        )
        actionable = actionable or is_actionable
        horizons[h] = {
            "n_plus": n_plus,
            "n_minus": n_minus,
            "mean_plus_bps": round(mean_plus, 2),
            "mean_minus_bps": round(mean_minus, 2),
            "hit_plus": round(hit_plus, 3),
            "hit_minus": round(hit_minus, 3),
            "spread_bps": round(mean_plus - mean_minus, 2),
            "p_plus_positive": p_plus_pos,
            "top_symbol_share": round(sym_share, 3),
            "actionable": is_actionable,
        }
    return {
        "n_joined": len(joined),
        "horizons": horizons,
        "actionable": actionable,
        "verdict": (
            "ACTIONABLE (hypothesis — operator+edge-gated)"
            if actionable
            else "trust=0.5 SHADOW_ONLY (no cost-clearing, confirmed, non-concentrated edge)"
        ),
    }


def render(name: str, res: dict[str, Any]) -> str:
    """Compact markdown table for one evaluated aligned-evidence stream."""
    lines = [f"### {name} — n_joined={res['n_joined']}  →  {res['verdict']}"]
    lines.append(
        "| horizon | n+ | n- | mean+ | mean- | spread | hit+ | P(mean+>0) | top-sym% | act |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for h in HORIZONS:
        r = res["horizons"][h]
        p = r["p_plus_positive"]
        p_str = f"{p:.3f}" if p is not None else "n/a"
        act = "✅" if r["actionable"] else "—"
        lines.append(
            f"| {h}s | {r['n_plus']} | {r['n_minus']} | {r['mean_plus_bps']} | "
            f"{r['mean_minus_bps']} | {r['spread_bps']} | {r['hit_plus']} | "
            f"{p_str} | {r['top_symbol_share']} | {act} |"
        )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_COST_BPS",
    "DEFAULT_MAX_CONCENTRATION",
    "DEFAULT_TOL_S",
    "evaluate_signal",
    "index_evidence",
    "nearest_aligned",
    "render",
]
