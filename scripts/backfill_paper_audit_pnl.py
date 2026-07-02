#!/usr/bin/env python
"""Backfill paper_execution_audit.jsonl (v1) -> v2 with per-trade PnL.

Reconstructs per-trade NETTO PnL from the v1 audit format (which only
carries cumulative realized_pnl_usd) into the v2 schema introduced by
NEO-P-101-r2. Reads v1 read-only, writes a separate v2 file additively.
The v1 file is never mutated.

Usage:
    python scripts/backfill_paper_audit_pnl.py
    python scripts/backfill_paper_audit_pnl.py --dry-run

v2 schema additions (per line):
    schema_version  = v2
    position_side   = long  (default for historical pre-V5 trades)

For order_filled events:
    pnl_usd = 0.0                                if side == buy
    pnl_usd = (exit-entry)*qty - sell_fee_usd    if side == sell

For position_closed events:
    trade_pnl_usd              = (exit-entry)*qty - sell_fee_usd
    fee_usd                    = sell_fee_usd  (from matching order_filled)
    portfolio_realized_pnl_usd = cumulative (preserved as new explicit field)
    realized_pnl_usd           = unchanged cumulative (legacy alias, frozen)

Idempotency: deterministic. Same v1 input -> byte-identical v2 output.

Implementation: NEO-P-104 (depends on NEO-P-101-r2).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "artifacts" / "paper_execution_audit.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "paper_execution_audit_v2.jsonl"

SCHEMA_VERSION = "v2"
DEFAULT_POSITION_SIDE = "long"


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file into a list of dicts. Skips blank lines."""
    if not path.exists():
        raise FileNotFoundError(f"input audit not found: {path}")
    out: list[dict] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"json decode error at line {line_no}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"non-object json at line {line_no}")
        out.append(payload)
    return out


def _index_sell_fills_by_id(events: list[dict]) -> dict[str, dict]:
    """Build {fill_id: order_filled-event} index for sell-side fills only."""
    out: dict[str, dict] = {}
    for ev in events:
        if ev.get("event_type") != "order_filled":
            continue
        if ev.get("side") != "sell":
            continue
        fill_id = ev.get("fill_id")
        if isinstance(fill_id, str) and fill_id:
            out[fill_id] = ev
    return out


def _index_closes_by_fill_id(events: list[dict]) -> dict[str, dict]:
    """Build {fill_id: position_closed-event} index."""
    out: dict[str, dict] = {}
    for ev in events:
        if ev.get("event_type") != "position_closed":
            continue
        fill_id = ev.get("fill_id")
        if isinstance(fill_id, str) and fill_id:
            out[fill_id] = ev
    return out


def _compute_trade_pnl(close_ev: dict, sell_fill: dict | None) -> tuple[float, float]:
    """Return (trade_pnl_usd, fee_usd) for a position_closed event.

    Mirrors paper_engine.fill_order sell-branch:
        pnl = (fill_price - avg_entry) * qty - fee
    where fee is the sell-side fee only (entry fees were already paid out
    of cash when the position was opened, same convention as the live engine).
    """
    entry = float(close_ev.get("entry_price") or 0.0)
    exit_price = float(close_ev.get("exit_price") or 0.0)
    qty = float(close_ev.get("quantity") or 0.0)
    fee = float((sell_fill or {}).get("fee_usd") or 0.0)
    return (exit_price - entry) * qty - fee, fee


def _enrich_event(
    ev: dict,
    sell_index: dict[str, dict],
    close_index: dict[str, dict],
) -> dict:
    """Return a v2-enriched copy of a single audit event."""
    out = dict(ev)
    out["schema_version"] = SCHEMA_VERSION
    out["position_side"] = ev.get("position_side") or DEFAULT_POSITION_SIDE

    etype = ev.get("event_type")
    if etype == "order_filled":
        side = ev.get("side")
        if side == "sell":
            fill_id = ev.get("fill_id")
            close_ev = close_index.get(fill_id) if isinstance(fill_id, str) else None
            if close_ev is not None:
                trade_pnl, _fee = _compute_trade_pnl(close_ev, ev)
                out["pnl_usd"] = trade_pnl
            else:
                out["pnl_usd"] = 0.0
        else:
            out["pnl_usd"] = 0.0
    elif etype == "position_closed":
        fill_id = ev.get("fill_id")
        sell_fill = sell_index.get(fill_id) if isinstance(fill_id, str) else None
        trade_pnl, fee = _compute_trade_pnl(ev, sell_fill)
        out["trade_pnl_usd"] = trade_pnl
        out["fee_usd"] = fee
        out["portfolio_realized_pnl_usd"] = float(ev.get("realized_pnl_usd") or 0.0)
    return out


def backfill(events: list[dict]) -> tuple[list[dict], dict]:
    """Return (v2_events, stats) from a v1 events list."""
    sell_index = _index_sell_fills_by_id(events)
    close_index = _index_closes_by_fill_id(events)

    v2: list[dict] = [_enrich_event(ev, sell_index, close_index) for ev in events]

    per_trade_pnl: list[tuple[str, float]] = []
    closes = 0
    cumulative = 0.0
    for ev in v2:
        if ev.get("event_type") == "position_closed":
            closes += 1
            tp = float(ev.get("trade_pnl_usd") or 0.0)
            per_trade_pnl.append((str(ev.get("symbol")), tp))
            cumulative += tp

    last_portfolio = 0.0
    for ev in reversed(v2):
        if ev.get("event_type") == "position_closed":
            last_portfolio = float(ev.get("portfolio_realized_pnl_usd") or 0.0)
            break

    stats = {
        "events_total": len(v2),
        "position_closed_count": closes,
        "per_trade_pnl": per_trade_pnl,
        "reconstructed_net_pnl_usd": cumulative,
        "v1_final_cumulative_pnl_usd": last_portfolio,
        "drift_usd": cumulative - last_portfolio,
    }
    return v2, stats


def _write_jsonl(path: Path, events: list[dict]) -> None:
    """Write events to JSONL deterministically (compact line, sorted keys)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(ev, sort_keys=True, separators=(", ", ": ")) for ev in events]
    payload = chr(10).join(lines) + (chr(10) if lines else "")
    path.write_text(payload, encoding="utf-8")


def _print_stats(stats: dict, *, dry_run: bool) -> None:
    print("events_total              = {events_total}".format(**stats))
    print("position_closed_count     = {position_closed_count}".format(**stats))
    print("reconstructed_net_pnl_usd = {reconstructed_net_pnl_usd:.4f}".format(**stats))
    print("v1_final_cumulative_pnl   = {v1_final_cumulative_pnl_usd:.4f}".format(**stats))
    print("drift_usd                 = {drift_usd:+.4f}".format(**stats))
    print("per_trade_pnl_usd:")
    for sym, tp in stats["per_trade_pnl"]:
        print(f"  {sym:<12} {tp:+.4f}")
    if dry_run:
        print("[dry-run] no output file written")


def run(input_path: Path, output_path: Path, *, dry_run: bool) -> int:
    try:
        events = _read_jsonl(input_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    v2, stats = backfill(events)
    _print_stats(stats, dry_run=dry_run)

    if not dry_run:
        _write_jsonl(output_path, v2)
        print(f"wrote {output_path} ({len(v2)} events)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill v1 paper_execution_audit.jsonl into v2 schema with "
            "per-trade NETTO trade_pnl_usd."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"v1 audit JSONL (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"v2 audit JSONL (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report stats only; do not write output file"
    )
    args = parser.parse_args()
    return run(args.input, args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
