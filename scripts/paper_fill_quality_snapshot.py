"""Paper-Fill Quality Snapshot — per-trade detail report from paper audit.

Zweck (DS-20260527-V4): Quality-Snapshot der Paper-Trading-Closures.
Verhindert den falschen Schluss "Gate ≥10 Fills grün = Strategie grün". Pro
Closure eine Detail-Zeile + Aggregates (Win-Rate, Avg-PnL per Symbol/Reason).

Read-only. Kein Live-Mode. Kein Backtest. Nur Aggregation über
position_closed + position_partial_closed events.

Usage:
    python scripts/paper_fill_quality_snapshot.py
    python scripts/paper_fill_quality_snapshot.py --audit-path artifacts/paper_execution_audit.jsonl
    python scripts/paper_fill_quality_snapshot.py --output-md artifacts/paper_quality_20260527.md
    python scripts/paper_fill_quality_snapshot.py --json   # JSON-only for piping
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


def _load_closures(audit_path: Path) -> list[dict[str, object]]:
    """Return all position_closed + position_partial_closed events, in order."""
    if not audit_path.exists():
        return []
    out: list[dict[str, object]] = []
    with audit_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            et = rec.get("event_type")
            if et in ("position_closed", "position_partial_closed"):
                out.append(rec)
    return out


def _trade_pnl(rec: dict[str, object]) -> float:
    """Trade-PnL — fee-adjusted per-trade. Falls back to realized_pnl_usd if v1."""
    if "trade_pnl_usd" in rec:
        v = rec["trade_pnl_usd"]
        return float(v) if isinstance(v, (int, float)) else 0.0
    # legacy v1: realized_pnl_usd is the per-trade PnL (not cumulative)
    v = rec.get("realized_pnl_usd")
    return float(v) if isinstance(v, (int, float)) else 0.0


def _str(rec: dict[str, object], key: str, default: str = "") -> str:
    v = rec.get(key)
    return str(v) if v is not None else default


def _float(rec: dict[str, object], key: str) -> float | None:
    v = rec.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _build_report(closures: list[dict[str, object]]) -> dict[str, object]:
    """Compute aggregates + per-trade detail rows."""
    rows: list[dict[str, object]] = []
    for rec in closures:
        rows.append(
            {
                "ts": _str(rec, "timestamp_utc"),
                "symbol": _str(rec, "symbol", "?"),
                "event_type": _str(rec, "event_type"),
                "reason": _str(rec, "reason", "?"),
                "schema_version": _str(rec, "schema_version", "v1"),
                "side": _str(rec, "position_side", "long"),
                "quantity": _float(rec, "quantity")
                or _float(rec, "quantity_closed")
                or 0.0,
                "entry_price": _float(rec, "entry_price"),
                "exit_price": _float(rec, "exit_price") or _float(rec, "tier_price"),
                "trade_pnl_usd": _trade_pnl(rec),
                "fee_usd": _float(rec, "fee_usd") or 0.0,
                "realized_pnl_usd_cumulative": _float(rec, "realized_pnl_usd"),
            }
        )

    total = len(rows)
    pnls = [r["trade_pnl_usd"] for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    flat = total - wins - losses
    win_rate = (100.0 * wins / total) if total else 0.0

    # Per-symbol aggregates
    by_symbol: dict[str, dict[str, float | int | list[float]]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl_total": 0.0, "pnl_list": []}
    )
    for r in rows:
        sym = r["symbol"]
        d = by_symbol[sym]
        d["trades"] = int(d["trades"]) + 1
        if r["trade_pnl_usd"] > 0:
            d["wins"] = int(d["wins"]) + 1
        elif r["trade_pnl_usd"] < 0:
            d["losses"] = int(d["losses"]) + 1
        d["pnl_total"] = float(d["pnl_total"]) + r["trade_pnl_usd"]
        assert isinstance(d["pnl_list"], list)
        d["pnl_list"].append(r["trade_pnl_usd"])

    by_symbol_out: list[dict[str, object]] = []
    for sym, d in by_symbol.items():
        pnl_list = d["pnl_list"]
        assert isinstance(pnl_list, list)
        n = int(d["trades"])
        by_symbol_out.append(
            {
                "symbol": sym,
                "trades": n,
                "wins": int(d["wins"]),
                "losses": int(d["losses"]),
                "win_rate_pct": (100.0 * int(d["wins"]) / n) if n else 0.0,
                "pnl_total_usd": round(float(d["pnl_total"]), 2),
                "pnl_avg_usd": round(float(d["pnl_total"]) / n, 2) if n else 0.0,
                "pnl_max_usd": round(max(pnl_list), 2) if pnl_list else 0.0,
                "pnl_min_usd": round(min(pnl_list), 2) if pnl_list else 0.0,
            }
        )
    by_symbol_out.sort(key=lambda x: x["pnl_total_usd"], reverse=True)

    # Per-reason aggregates
    reason_counter: Counter[str] = Counter(r["reason"] for r in rows)
    pnl_by_reason: dict[str, float] = defaultdict(float)
    for r in rows:
        pnl_by_reason[r["reason"]] += r["trade_pnl_usd"]
    by_reason_out = [
        {
            "reason": reason,
            "count": cnt,
            "pnl_total_usd": round(pnl_by_reason[reason], 2),
            "pnl_avg_usd": round(pnl_by_reason[reason] / cnt, 2) if cnt else 0.0,
        }
        for reason, cnt in reason_counter.most_common()
    ]

    return {
        "as_of_utc": datetime.now(UTC).isoformat(),
        "totals": {
            "closures": total,
            "wins": wins,
            "losses": losses,
            "flat": flat,
            "win_rate_pct": round(win_rate, 1),
            "pnl_total_usd": round(sum(pnls), 2),
            "pnl_avg_usd": round(statistics.mean(pnls), 2) if pnls else 0.0,
            "pnl_median_usd": round(statistics.median(pnls), 2) if pnls else 0.0,
            "fee_total_usd": round(sum(r["fee_usd"] for r in rows), 2),
        },
        "by_symbol": by_symbol_out,
        "by_reason": by_reason_out,
        "rows": rows,
    }


def _format_markdown(report: dict[str, object]) -> str:
    t = report["totals"]
    assert isinstance(t, dict)
    sym = report["by_symbol"]
    assert isinstance(sym, list)
    reasons = report["by_reason"]
    assert isinstance(reasons, list)
    rows = report["rows"]
    assert isinstance(rows, list)

    md = f"""# Paper-Fill Quality Snapshot — {report["as_of_utc"]}

Read-only Aggregation aller `position_closed` + `position_partial_closed`
Events aus dem Paper-Execution-Audit. Trade-PnL ist fee-adjusted per Closure
(siehe Memory `paper_audit_pnl_field_semantics`).

## Totals

| Metrik | Wert |
|---|---|
| Closures (gesamt) | {t["closures"]} |
| Wins | {t["wins"]} |
| Losses | {t["losses"]} |
| Flat | {t["flat"]} |
| Win-Rate | {t["win_rate_pct"]}% |
| Trade-PnL gesamt | {t["pnl_total_usd"]} USD |
| Trade-PnL ⌀ | {t["pnl_avg_usd"]} USD |
| Trade-PnL Median | {t["pnl_median_usd"]} USD |
| Fees gesamt | {t["fee_total_usd"]} USD |

## Per Symbol

| Symbol | Trades | W | L | Win-Rate | PnL gesamt | PnL ⌀ | PnL max | PnL min |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
"""
    for s in sym:
        assert isinstance(s, dict)
        md += (
            f"| {s['symbol']} | {s['trades']} | {s['wins']} | {s['losses']} | "
            f"{s['win_rate_pct']:.1f}% | {s['pnl_total_usd']} | {s['pnl_avg_usd']} | "
            f"{s['pnl_max_usd']} | {s['pnl_min_usd']} |\n"
        )

    md += "\n## Per Reason\n\n| Reason | Count | PnL gesamt | PnL ⌀ |\n|---|---:|---:|---:|\n"
    for r in reasons:
        assert isinstance(r, dict)
        md += f"| {r['reason']} | {r['count']} | {r['pnl_total_usd']} | {r['pnl_avg_usd']} |\n"

    md += "\n## Per Trade (jüngste 30, neueste zuerst)\n\n"
    md += "| Time UTC | Symbol | Reason | Qty | Entry | Exit | Trade-PnL | Fee |\n"
    md += "|---|---|---|---:|---:|---:|---:|---:|\n"
    for r in list(rows)[-30:][::-1]:
        assert isinstance(r, dict)
        q = r["quantity"]
        e = r["entry_price"]
        x = r["exit_price"]
        p = r["trade_pnl_usd"]
        f = r["fee_usd"]
        md += (
            f"| {r['ts'][:19]} | {r['symbol']} | {r['reason']} | "
            f"{q:.4f} | {e if e else '—'} | {x if x else '—'} | "
            f"{p:+.2f} | {f:.2f} |\n"
        )

    return md


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument(
        "--audit-path",
        default="artifacts/paper_execution_audit.jsonl",
        help="Path to paper_execution_audit.jsonl (default: artifacts/...)",
    )
    p.add_argument(
        "--output-md",
        default=None,
        help="If set, write the markdown report to this path (else stdout)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of markdown (rows truncated to last 30)",
    )
    args = p.parse_args(argv)

    closures = _load_closures(Path(args.audit_path))
    report = _build_report(closures)

    if args.json:
        # Truncate to last 30 rows for stdout-friendliness
        report_copy = dict(report)
        rows = report_copy["rows"]
        assert isinstance(rows, list)
        report_copy["rows"] = rows[-30:]
        sys.stdout.write(json.dumps(report_copy, indent=2, default=str))
        sys.stdout.write("\n")
        return 0

    md = _format_markdown(report)
    if args.output_md:
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(md, encoding="utf-8")
        sys.stderr.write(f"wrote: {args.output_md}\n")
    else:
        sys.stdout.write(md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
