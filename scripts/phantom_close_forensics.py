"""Phantom-close forensics (DS-20260529-V1) — read-only.

Scans ``artifacts/paper_execution_audit.jsonl`` for position closes whose
implied per-trade return exceeds a sanity cap. These are the signature of the
2026-05-28 MATIC incident: a stale/wrong price source (BitMEX's delisted MATIC
ticker at 0.40875 vs the real ~0.088) closed positions at a phantom +364% every
cycle, and the fake profit compounded the next position's size.

The script NEVER mutates the audit (append-only integrity). It reports:
  - phantom closes per symbol (entry, exit, implied return, booked trade_pnl)
  - total phantom realized PnL
  - the corrected cumulative book (raw cumulative minus phantom)

Use the output to (a) exclude phantom rows from the paper-quality report and
(b) size the reviewed ``portfolio_correction`` event once the price-source guard
is deployed (so the monitor cannot re-corrupt the book between fix and cleanup).

    python -m scripts.phantom_close_forensics [--threshold-pct 200] [--path P]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_DEFAULT_PATH = Path("artifacts/paper_execution_audit.jsonl")
_CLOSE_EVENTS = {"position_closed", "position_partial_closed"}


def _implied_return(entry: float, exit_: float, side: str) -> float | None:
    if entry <= 0 or exit_ <= 0:
        return None
    if side == "short":
        return entry / exit_ - 1.0
    return exit_ / entry - 1.0


def scan(path: Path, threshold: float) -> dict:
    phantom: list[dict] = []
    phantom_pnl = 0.0
    last_cumulative = 0.0
    n_closes = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event_type") not in _CLOSE_EVENTS:
            continue
        n_closes += 1
        cum = rec.get("realized_pnl_usd")
        if isinstance(cum, (int, float)):
            last_cumulative = cum
        entry = rec.get("entry_price")
        exit_ = rec.get("exit_price")
        side = rec.get("position_side", "long")
        if not isinstance(entry, (int, float)) or not isinstance(exit_, (int, float)):
            continue
        r = _implied_return(float(entry), float(exit_), side)
        if r is None or abs(r) <= threshold:
            continue
        trade_pnl = rec.get("trade_pnl_usd")
        trade_pnl = float(trade_pnl) if isinstance(trade_pnl, (int, float)) else 0.0
        phantom_pnl += trade_pnl
        phantom.append(
            {
                "ts": rec.get("timestamp_utc"),
                "symbol": rec.get("symbol"),
                "entry": float(entry),
                "exit": float(exit_),
                "implied_return_pct": round(r * 100.0, 1),
                "trade_pnl_usd": round(trade_pnl, 2),
            }
        )
    return {
        "closes_total": n_closes,
        "phantom_count": len(phantom),
        "phantom_pnl_usd": round(phantom_pnl, 2),
        "raw_cumulative_realized_usd": round(last_cumulative, 2),
        "corrected_cumulative_realized_usd": round(last_cumulative - phantom_pnl, 2),
        "phantom_closes": phantom,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", type=Path, default=_DEFAULT_PATH)
    ap.add_argument(
        "--threshold-pct",
        type=float,
        default=200.0,
        help="Implied per-trade return magnitude above which a close is phantom.",
    )
    args = ap.parse_args()
    if not args.path.exists():
        print(f"audit not found: {args.path}")
        return
    report = scan(args.path, args.threshold_pct / 100.0)
    print(json.dumps({k: v for k, v in report.items() if k != "phantom_closes"}, indent=2))
    print("\nphantom closes:")
    for c in report["phantom_closes"]:
        print(
            f"  {c['ts']} {c['symbol']} entry={c['entry']:.6g} exit={c['exit']:.6g} "
            f"ret={c['implied_return_pct']}% pnl={c['trade_pnl_usd']}"
        )


if __name__ == "__main__":
    main()
