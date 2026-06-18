#!/usr/bin/env python3
"""Track 2.3 — Source × Direction × Horizon edge audit (READ-ONLY CLI).

Reproducible table over artifacts/shadow_candidate_resolved.jsonl. Changes
NOTHING (no boost/downrank/invert/gate/sizing/fetch). 4h/24h and any source not
in the forward-bps stream (news alerts) are reported bps_unavailable, never
estimated. Usage: source_direction_horizon_audit.py [resolved.jsonl] [--min-n N]
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.observability.source_direction_horizon_audit import (
    DEFAULT_COST_BPS,
    UNAVAILABLE_HORIZONS,
    Cohort,
    build_audit,
    load_resolved,
)


def _fmt(v: object, width: int) -> str:
    if v is None:
        return "-".rjust(width)
    if isinstance(v, float):
        return f"{v:.1f}".rjust(width)
    return str(v).rjust(width)


def main() -> int:
    # Robust against cp1252 consoles (the report uses ≥/×/→); never crash on output.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    min_n = 10
    for a in sys.argv[1:]:
        if a.startswith("--min-n"):
            min_n = int(a.split("=")[-1]) if "=" in a else 10
    path = Path(args[0]) if args else Path("artifacts/shadow_candidate_resolved.jsonl")

    rows, canary = load_resolved(path)
    if not rows:
        print(f"no resolved rows at {path} (file missing/empty)")
        return 0
    cohorts = build_audit(rows)

    print("# Track 2.3 — Source × Direction × Asset-Bucket × Horizon (READ-ONLY)")
    print(f"# ledger={path}  rows={len(rows)}  canary_skipped={canary}  cost={DEFAULT_COST_BPS}bps")
    print(
        f"# horizons {', '.join(h for h, _ in [('1m', 0), ('5m', 0), ('15m', 0), ('1h', 0)])} available; "
        f"{', '.join(UNAVAILABLE_HORIZONS)} = bps_unavailable (resolver stops at 1h)"
    )
    print()
    hdr = (
        f"{'source':<20}{'dir':<6}{'bucket':<7}{'horizon':>8}{'n':>6}{'hit%':>7}"
        f"{'WilsonLB':>9}{'IC':>7}{'MED_bps':>9}{'EVnet':>8}{'robEV':>8}{'invEV':>8}  verdict"
    )
    print(hdr)
    print("-" * len(hdr))
    shown = suppressed = 0
    for c in cohorts:
        if c.n < min_n:
            suppressed += 1
            continue
        shown += 1
        first = True
        for label in ("1m", "5m", "15m", "1h"):
            h = c.horizons[label]
            if not h.available:
                continue
            lead = f"{c.source:<20}{c.direction:<6}{c.asset_bucket:<7}" if first else " " * 33
            verdict = f"  {c.verdict}" if first else ""
            print(
                f"{lead}{label:>8}{h.n:>6}{_fmt(h.hit_rate, 7)}{_fmt(h.wilson_lb, 9)}"
                f"{_fmt(h.ic, 7)}{_fmt(h.median_bps, 9)}{_fmt(h.ev_net_bps, 8)}"
                f"{_fmt(h.robust_ev_bps, 8)}{_fmt(h.inverted_ev_bps, 8)}{verdict}"
            )
            first = False
        print(f"{' ' * 33}{'4h/24h':>8}{'  bps_unavailable':>0}")
    print()
    print(f"# {shown} cohorts shown (n>={min_n}), {suppressed} suppressed (thin)")

    # --- the five questions ---
    # All answers use ROBUST (trimmed-mean) EV — the same measure that drives the
    # verdict — so a handful of tail winners cannot fake a "carrier" in the prose.
    print("\n## Antworten (robuste/trimmed EV — Outlier-bereinigt)")

    def _best_rob(c: Cohort) -> float | None:
        return max(
            (
                h.robust_ev_bps
                for h in c.horizons.values()
                if h.available and h.robust_ev_bps is not None
            ),
            default=None,
        )

    def _sort_key(c: Cohort) -> float:
        v = _best_rob(c)
        return -(v if v is not None else -99.0)

    q1 = sorted(
        (c for c in cohorts if c.n >= min_n and c.verdict in ("CARRIER_LONG", "SUPPORTING")),
        key=_sort_key,
    )
    print("1) Welche Source trägt (robuster netto-EV>0, ≥1 Horizont)?")
    for c in q1[:8]:
        print(
            f"   {c.source} × {c.direction} × {c.asset_bucket}: best robEV={_best_rob(c)} ({c.verdict}, n={c.n})"
        )
    if not q1:
        print("   (keine Source mit robust netto-positivem EV über der Kostenhürde)")

    shorts = [c for c in cohorts if c.direction == "short" and c.n >= min_n]
    print("\n2) Ist bearish (short) richtungsgetrieben giftig?")
    for c in shorts:
        print(f"   {c.source} × short × {c.asset_bucket}: best robEV={_best_rob(c)}  → {c.verdict}")

    print("\n3) Gibt es handelbaren invertierten EV (bearish invertiert, robust)?")
    contr = [c for c in shorts if c.verdict == "CONTRARIAN_CANDIDATE"]
    for c in shorts:
        inv = max(
            (
                h.robust_inverted_ev_bps
                for h in c.horizons.values()
                if h.available and h.robust_inverted_ev_bps is not None
            ),
            default=None,
        )
        print(
            f"   {c.source} × short × {c.asset_bucket}: best inverted robEV={inv}{'  ← CONTRARIAN' if c in contr else ''}"
        )

    print("\n4) Auf welchem Horizont verfällt der Edge? (robEV je Horizont, größte Carrier)")
    for c in q1[:5]:
        series = " ".join(
            f"{lab}={c.horizons[lab].robust_ev_bps}"
            for lab in ("1m", "5m", "15m", "1h")
            if c.horizons[lab].available
        )
        print(f"   {c.source} × {c.direction} × {c.asset_bucket}: {series}")

    print("\n5) Welche Asset-Buckets tragen / vergiften?")
    from collections import defaultdict

    bucket_ev: dict[str, list[float]] = defaultdict(list)
    for c in cohorts:
        if c.n >= min_n:
            for h in c.horizons.values():
                if h.available and h.robust_ev_bps is not None:
                    bucket_ev[c.asset_bucket].append(h.robust_ev_bps)
    for b, evs in sorted(bucket_ev.items()):
        avg = sum(evs) / len(evs) if evs else 0.0
        print(f"   {b}: mean robEV über Cohorts/Horizonte = {avg:.1f}bps (n_cells={len(evs)})")

    print(
        "\n# bps_unavailable (kein forward-bps-Stream): News-Quellen (thedefiant, "
        "cointelegraph, decrypt, cryptoslate, bitcoin_magazine), tradingview_webhook —"
    )
    print(
        "# nur hit/miss in alert_outcomes/provenance, KEIN side+horizon-bps. 4h/24h: kein Resolver."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
