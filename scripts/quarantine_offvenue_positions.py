"""Remediation script: flat-close stuck off-venue positions.

Context
-------
ACT/USDT, SLX/USDT, O/USDT were opened from the Bybit-universe momentum feeder
but have NO market on the canonical venue Binance. They sit OPEN, un-markable/
un-closable on Binance, distorting portfolio open-equity and canonical-edge calc.

This script:
- Reads open positions via ``audit_replay.replay_paper_audit``.
- For each target symbol that is currently NET-OPEN, appends TWO audit events:
    1. ``order_filled`` (close-side, side="sell", at avg_entry_price, fee=0)
       → replay sees the position closed on the next rehydration.
    2. ``position_closed`` (reason="quarantine_off_venue_unpriceable")
       → ``corruption_reason`` classifies it corrupt → excluded from canonical edge.
- Realized price-PnL is exactly 0 (exit == entry price). The already-booked
  ENTRY fee remains on the audit as an honest cost (it is NOT reversed).
- IDs are deterministic (``quarantine_fill_<symbol_slug>`` etc.) — no random.
- ``--apply`` flag writes to the audit; default DRY-RUN prints the plan only.
- Idempotent: if a symbol is already net-flat/closed in the replay, it is skipped.

Usage
-----
    python scripts/quarantine_offvenue_positions.py          # dry-run (safe)
    python scripts/quarantine_offvenue_positions.py --apply  # write to audit

DO NOT run against the Pi audit without controller approval.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Default targets (operator-overridable via --targets arg)
# ---------------------------------------------------------------------------
DEFAULT_TARGETS: list[str] = ["ACT/USDT", "SLX/USDT", "O/USDT"]

_AUDIT_PATH = Path("artifacts/paper_execution_audit.jsonl")


def _slug(symbol: str) -> str:
    """Safe filesystem/id slug from a symbol string (e.g. 'SLX/USDT' → 'SLX_USDT')."""
    return symbol.replace("/", "_").replace(":", "_")


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def plan_closes(
    audit_path: Path,
    targets: list[str],
) -> list[dict]:
    """Return the list of planned close descriptors (pure, no I/O).

    Each descriptor has enough information for ``apply_closes`` to write the
    two audit events (order_filled + position_closed).  The list is empty when
    all targets are already net-flat.

    This is the testable pure core; the CLI wires it to I/O.
    """
    from app.execution.audit_replay import replay_paper_audit

    replay = replay_paper_audit(audit_path)

    plans: list[dict] = []
    for symbol in targets:
        pos = replay.positions.get(symbol)
        if pos is None:
            # Already net-flat or was never open — skip (idempotent).
            continue

        avg_entry = pos.avg_entry_price
        qty = pos.quantity
        slug = _slug(symbol)
        order_id = f"quarantine_order_{slug}"
        fill_id = f"quarantine_fill_{slug}"
        # Flat-close: exit == entry → price-PnL == 0.
        trade_pnl_usd = 0.0
        # Cash recovered at exit = qty * avg_entry (the cost we originally deducted).
        recovered_cash = qty * avg_entry
        new_cash = (replay.cash_usd or 0.0) + recovered_cash
        # Cumulative realized_pnl_usd is unchanged (no price gain/loss).
        cumulative_pnl = replay.realized_pnl_usd

        plans.append(
            {
                "symbol": symbol,
                "quantity": qty,
                "entry_price": avg_entry,
                "exit_price": avg_entry,  # flat-close
                "position_side": pos.position_side,
                "close_reason": "quarantine_off_venue_unpriceable",
                "order_id": order_id,
                "fill_id": fill_id,
                "trade_pnl_usd": trade_pnl_usd,
                "cumulative_pnl_usd": cumulative_pnl,
                "new_cash_usd": new_cash,
                "source": pos.source,
                "document_id": pos.document_id,
                "regime": pos.regime,
            }
        )

    return plans


def _build_order_filled(plan: dict, ts: str) -> dict:
    """Build the order_filled audit row for a flat-close."""
    pos_side = plan["position_side"]
    # Closing a long → sell; closing a short → buy.
    close_side = "sell" if pos_side == "long" else "buy"
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": ts,
        "order_id": plan["order_id"],
        "fill_id": plan["fill_id"],
        "symbol": plan["symbol"],
        "side": close_side,
        "position_side": pos_side,
        "quantity": plan["quantity"],
        "fill_price": plan["exit_price"],
        "fee_usd": 0.0,
        "fee_bps_applied": 0.0,
        "fee_venue": "paper",
        "fee_role": "taker",
        "fee_table_version": "quarantine_v1",
        "filled_at": ts,
        "portfolio_cash": plan["new_cash_usd"],
        "realized_pnl_usd": plan["cumulative_pnl_usd"],
        "trade_pnl_usd": plan["trade_pnl_usd"],
        "source": plan.get("source", ""),
        "document_id": plan.get("document_id", ""),
        "regime": plan.get("regime", ""),
    }


def _build_position_closed(plan: dict, ts: str) -> dict:
    """Build the position_closed audit row."""
    return {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": ts,
        "symbol": plan["symbol"],
        "reason": plan["close_reason"],  # "quarantine_off_venue_unpriceable"
        "quantity": plan["quantity"],
        "entry_price": plan["entry_price"],
        "exit_price": plan["exit_price"],
        "fill_id": plan["fill_id"],
        "order_id": plan["order_id"],
        "realized_pnl_usd": plan["cumulative_pnl_usd"],
        "trade_pnl_usd": plan["trade_pnl_usd"],
        "fee_usd": 0.0,
        "position_side": plan["position_side"],
        "signal_source": plan.get("source", ""),
        "document_id": plan.get("document_id", ""),
        "regime": plan.get("regime", ""),
    }


def apply_closes(
    audit_path: Path,
    plans: list[dict],
) -> None:
    """Append the planned close events to the audit file (write mode)."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    ts = _now_utc()
    with audit_path.open("a", encoding="utf-8") as fh:
        for plan in plans:
            filled = _build_order_filled(plan, ts)
            closed = _build_position_closed(plan, ts)
            fh.write(json.dumps(filled, separators=(",", ":")) + "\n")
            fh.write(json.dumps(closed, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flat-close stuck off-venue positions in the paper audit (DRY-RUN by default)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write the remediation events to the audit file. Without this flag: DRY-RUN only.",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=_AUDIT_PATH,
        help=f"Path to paper_execution_audit.jsonl (default: {_AUDIT_PATH})",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        default=DEFAULT_TARGETS,
        help=f"Target symbols (default: {DEFAULT_TARGETS})",
    )
    args = parser.parse_args()

    audit_path: Path = args.audit
    targets: list[str] = args.targets

    plans = plan_closes(audit_path=audit_path, targets=targets)

    if not plans:
        print("No open positions found for targets — nothing to do (already flat or never opened).")
        return

    print(f"{'DRY-RUN' if not args.apply else 'APPLY'}: {len(plans)} position(s) to flat-close:")
    for plan in plans:
        print(
            f"  {plan['symbol']}: qty={plan['quantity']:.6f}  "
            f"entry/exit={plan['entry_price']:.8f}  "
            f"trade_pnl={plan['trade_pnl_usd']:.4f}  "
            f"reason={plan['close_reason']}"
        )

    if args.apply:
        apply_closes(audit_path=audit_path, plans=plans)
        print(f"Written {len(plans) * 2} audit events to {audit_path}")
    else:
        print("\nDRY-RUN: no changes written. Pass --apply to write.")


if __name__ == "__main__":
    main()
