#!/usr/bin/env python3
"""Evaluate V5 funding / open-interest evidence vs realized outcomes (re-runnable).

Encapsulates the previously ad-hoc 2026-06-19 V5 evaluation
(``artifacts/v5_evidence_outcome_eval_20260619.md``) into a deterministic,
repeatable script so each "Tag N/7" re-eval is ONE command instead of a manual
notebook pass. Read-only; ``source_trust``-promotion stays operator- AND
edge-gated downstream — a direction learned here is a HYPOTHESIS, never an
auto-flip.

Method (faithful to the 06-19 runbook):
  * Outcomes = resolved shadow candidates
    (``artifacts/shadow_candidate_resolved.jsonl``) with the SIDE-ADJUSTED forward
    returns ``fwd_{60,300,900,3600}s_bps`` (>0 ⇒ the candidate's OWN direction was
    profitable). Entry time = candidate ``ts_utc`` (from
    ``shadow_candidate_ledger.jsonl``; falls back to the ISO timestamp embedded in
    ``tech-<SYM>-<iso>`` candidate ids). Sentinel / no-data rows
    (``|fwd| >= --max-abs-bps``, default 5000 ≈ delisted/garbage) are dropped.
  * Evidence = ``funding_evidence_shadow.jsonl`` / ``oi_evidence_shadow.jsonl``,
    each carrying ``{ts, symbol, direction, evidence_direction_aligned (+1/-1/0)}``.
  * Outcome-centric join: each outcome is paired with the temporally NEAREST
    evidence record of the SAME symbol+side within ``+/- --tol-s`` seconds
    (default 300). Outcomes with no qualifying evidence (or aligned==0) are
    dropped — honest: not every outcome has contemporaneous evidence.
  * Per horizon, split joined outcomes by ``evidence_direction_aligned`` (+1 vs
    -1) → mean fwd_bps, hit-rate, and the spread ``mean(+1) - mean(-1)``. The
    actionable ``aligned+1`` cohort's TIME-ORDERED fwd series is bootstrapped
    (moving-block, autocorrelation-robust, reused from ``l2_evidence_eval``) for
    ``P(mean>0)`` — the honest significance of "do candidates the evidence agrees
    with actually pay?". A concentration metric flags single-symbol monoculture
    (the BTC-95%-share trap from 06-19).

Verdict is conservative: a signal is ACTIONABLE only if some horizon has
``mean(+1) >= --cost-bps`` AND ``P(mean(+1)>0) > 0.95`` AND top-symbol share
``<= --max-concentration``. Otherwise it stays ``trust=0.5 SHADOW_ONLY``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.l2_evidence_eval import moving_block_bootstrap_p_mean_positive

HORIZONS: tuple[int, ...] = (60, 300, 900, 3600)
_SIDES = ("long", "short")


# --------------------------------------------------------------------------- #
# IO helpers
# --------------------------------------------------------------------------- #
def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _parse_ts(ts: object) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #
def load_entry_times(ledger: list[dict[str, Any]]) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for r in ledger:
        cid = r.get("candidate_id")
        ts = _parse_ts(r.get("ts_utc"))
        if cid and ts is not None:
            out[str(cid)] = ts
    return out


def entry_ts_for(cand: dict[str, Any], entry_times: dict[str, datetime]) -> datetime | None:
    """Entry time of a candidate: ledger ts_utc, else the ISO ts embedded in a
    ``tech-<SYM>-<iso>`` id (autonomous_generator ``cyc_*`` ids carry no time)."""
    cid = str(cand.get("candidate_id", ""))
    if cid in entry_times:
        return entry_times[cid]
    parts = cid.split("-", 2)
    if len(parts) == 3:
        return _parse_ts(parts[2])
    return None


def build_outcomes(
    resolved: list[dict[str, Any]],
    entry_times: dict[str, datetime],
    *,
    max_abs_bps: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in resolved:
        sym = c.get("symbol")
        side = c.get("side")
        ets = entry_ts_for(c, entry_times)
        if not sym or side not in _SIDES or ets is None:
            continue
        fwd: dict[int, float | None] = {}
        sentinel = False
        for h in HORIZONS:
            v = c.get(f"fwd_{h}s_bps")
            if v is None:
                fwd[h] = None
                continue
            fv = float(v)
            if abs(fv) >= max_abs_bps:  # delisted / no-data sentinel, not signal
                sentinel = True
                break
            fwd[h] = fv
        if sentinel or all(fwd.get(h) is None for h in HORIZONS):
            continue
        out.append({"symbol": str(sym), "side": str(side), "entry_ts": ets, "fwd": fwd})
    out.sort(key=lambda o: o["entry_ts"])  # time-ordered → autocorr-preserving bootstrap
    return out


# --------------------------------------------------------------------------- #
# Evidence join
# --------------------------------------------------------------------------- #
def index_evidence(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], list[tuple[datetime, int]]]:
    by: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)
    for r in records:
        sym = r.get("symbol")
        side = r.get("direction")
        ts = _parse_ts(r.get("ts"))
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
    """Nearest same-symbol+side evidence within +/- tol_s; its aligned (+1/-1/0)."""
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


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def evaluate_signal(
    outcomes: list[dict[str, Any]],
    ev_index: dict[tuple[str, str], list[tuple[datetime, int]]],
    *,
    tol_s: float,
    cost_bps: float,
    max_concentration: float,
) -> dict[str, Any]:
    joined: list[tuple[int, dict[int, float | None], str]] = []
    for o in outcomes:  # outcomes already time-sorted → joined preserves order
        a = nearest_aligned(o, ev_index, tol_s=tol_s)
        if a is None or a == 0:
            continue
        joined.append((a, o["fwd"], o["symbol"]))

    horizons: dict[int, dict[str, Any]] = {}
    actionable = False
    for h in HORIZONS:
        plus = [(f[h], sym) for a, f, sym in joined if a == 1 and f.get(h) is not None]
        minus = [f[h] for a, f, sym in joined if a == -1 and f.get(h) is not None]
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


def render(name: str, res: dict[str, Any], *, cost_bps: float) -> str:
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate V5 funding/OI evidence vs outcomes.")
    ap.add_argument("--resolved", default="artifacts/shadow_candidate_resolved.jsonl")
    ap.add_argument("--ledger", default="artifacts/shadow_candidate_ledger.jsonl")
    ap.add_argument("--funding", default="artifacts/funding_evidence_shadow.jsonl")
    ap.add_argument("--oi", default="artifacts/oi_evidence_shadow.jsonl")
    ap.add_argument("--tol-s", type=float, default=300.0)
    ap.add_argument("--cost-bps", type=float, default=20.0)
    ap.add_argument("--max-abs-bps", type=float, default=5000.0)
    ap.add_argument("--max-concentration", type=float, default=0.8)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    resolved = _read_jsonl(Path(args.resolved))
    entry_times = load_entry_times(_read_jsonl(Path(args.ledger)))
    outcomes = build_outcomes(resolved, entry_times, max_abs_bps=args.max_abs_bps)

    funding_idx = index_evidence(_read_jsonl(Path(args.funding)))
    oi_idx = index_evidence(_read_jsonl(Path(args.oi)))

    fr = evaluate_signal(
        outcomes,
        funding_idx,
        tol_s=args.tol_s,
        cost_bps=args.cost_bps,
        max_concentration=args.max_concentration,
    )
    oir = evaluate_signal(
        outcomes,
        oi_idx,
        tol_s=args.tol_s,
        cost_bps=args.cost_bps,
        max_concentration=args.max_concentration,
    )

    if args.json:
        print(
            json.dumps({"funding": fr, "open_interest": oir, "n_outcomes": len(outcomes)}, indent=2)
        )
        return 0

    print(
        f"v5-eval: {len(resolved)} resolved, {len(outcomes)} valid outcomes "
        f"(sentinel/|fwd|>={args.max_abs_bps:.0f}bps dropped); tol={args.tol_s:.0f}s "
        f"cost={args.cost_bps:.0f}bps"
    )
    print()
    print(render("funding", fr, cost_bps=args.cost_bps))
    print()
    print(render("open_interest", oir, cost_bps=args.cost_bps))
    print()
    print(
        "v5-eval: a learned direction is a HYPOTHESIS — trust-promotion stays "
        "operator- AND edge-gated (no auto-activation)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
