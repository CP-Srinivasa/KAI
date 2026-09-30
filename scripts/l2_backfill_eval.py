#!/usr/bin/env python3
"""L2-Rueckschau ausfuehren: vorregistrierter Test gegen den kanonischen Pool.

Regeln und Begruendung: :mod:`app.research.l2_backfill` (Modul-Docstring). Nur
lesend. Ausgabe als Text; ``--json-out`` schreibt den vollstaendigen Bericht.

    python scripts/l2_backfill_eval.py [--l1 ...] [--resolved ...] [--ledger ...] [--json-out f]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.research import l2_backfill as bf
from app.research.shadow_outcomes import DEFAULT_LEDGER_PATH, DEFAULT_RESOLVED_PATH


def _fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L2-Rueckschau (vorregistriert).")
    ap.add_argument("--l1", default=str(bf.L1_PATH))
    ap.add_argument("--resolved", default=str(DEFAULT_RESOLVED_PATH))
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    ap.add_argument("--json-out", default="")
    args = ap.parse_args(argv)

    report = bf.run(
        l1_path=Path(args.l1), resolved_path=Path(args.resolved), ledger_path=Path(args.ledger)
    )
    pre = report["preregistration"]
    print(
        f"l2-rueckschau: Horizont {pre['horizon_s']} s, Fenster {pre['window']}, "
        f"Mindesthistorie {pre['min_window']}, TTL {pre['ttl_s']:.0f} s, Seed {pre['seed']}"
    )
    print(
        f"  L1-Saetze {report['l1_rows']} · Pool mit Ergebnis {report['population_with_outcome']} · "
        f"Paare {report['pairs']} · Cluster {report['clusters']}"
    )
    for label, key in (("Kandidaten", "base_rate_candidates"), ("Cluster", "base_rate_clusters")):
        br = report[key]
        print(
            f"  Basisrate {label}: n={br['population']} Mittel={_fmt(br['mean_bps'])} bps "
            f"positiv={br['numerator']}/{br['denominator']} ({_fmt(br['share_positive'], 3)})"
        )
    for v in report["verdicts"]:
        c, k = v["candidate_assessment"], v["cluster_assessment"]
        print(f"  {v['feature']}: {v['verdict']}")
        for label, r in (("Kandidaten", c), ("Cluster", k)):
            print(
                f"      {label}: {r['direction']} n_high={r['n_high']} n_low={r['n_low']} "
                f"mean_high={_fmt(r['mean_high'])} mean_low={_fmt(r['mean_low'])} "
                f"p_high+={r['p_high_positive']} p_low+={r['p_low_positive']}"
            )
        h1, h2 = v["by_half_spread_bps"]
        print(f"      Spanne hoch-niedrig je Zeithaelfte (Cluster): {_fmt(h1)} / {_fmt(h2)} bps")
    for d in report["descriptive"]:
        parts = [f"{k} n={n} {_fmt(s)}" for k, (n, s) in d["by_source_spread_bps"].items()]
        parts += [f"{k} n={n} {_fmt(s)}" for k, (n, s) in d["by_side_spread_bps"].items()]
        print(f"  nur beschreibend {d['feature']} Spanne: " + " · ".join(parts))
    print(
        "  Ein bestaetigtes Urteil ist Entscheidungsgrundlage fuer den Operator, keine Aktivierung."
    )
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
