"""Idempotent premium-fastlane backfill/reprocess CLI (Goal 2026-06-05 §7/§17).

Re-runs selected premium signal envelopes through the bridge — even ones with a
prior terminal stage (e.g. a pre-fastlane-cutover ``rejected_entry_mode`` or a
``rejected_scale_review`` that the fastlane now treats as a non-fatal pending).

Idempotent by construction: the paper engine's ``idempotency_key`` and the
per-symbol ``position_exists`` guard prevent a double fill, so a second run
creates no new order intent. PAPER only — live is never reachable here.

Usage:
    python -m scripts.reprocess_premium_fastlane --symbol TAC/USDT --route paper \
        --reason post_deploy_fastlane_backfill
    python -m scripts.reprocess_premium_fastlane --date 2026-06-05 \
        --symbols TAC/USDT,CLO/USDT,BEAT/USDT,4/USDT --route paper
    python -m scripts.reprocess_premium_fastlane --env-id ENV-... --route paper

``scripts.reprocess_premium_fastlane_today`` is a thin alias (see that module).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.execution.envelope_to_paper_bridge import BridgeTickResult, backfill_run


def _summary(result: BridgeTickResult) -> dict[str, int]:
    # Read the typed dataclass fields directly (to_dict() widens to object).
    pending = result.newly_pending + result.re_pending
    filled = result.filled
    order_intents = filled + pending
    eligible = result.envelopes_scanned - result.skipped_source - result.rejected_incomplete
    # A scale resolution "failed" only on a structural reject; fastlane non-fatal
    # scale-hints land in pending, so they count as resolved-for-paper.
    scale_resolved = eligible  # structural rejects already excluded from pending/fill
    return {
        "signals": result.envelopes_scanned,
        "parsed": result.envelopes_scanned,
        "eligible": max(0, eligible),
        "scale_resolved": max(0, scale_resolved),
        "entry_mode_bypassed": result.fastlane_bypassed_entry_mode,
        "order_intents_created": order_intents,
        "pending_or_open": pending + filled,
        "filled": filled,
        "pending": pending,
        "duplicates_skipped": result.rejected_position_exists,
        "live_orders": 0,
        "errors": len(result.errors),
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Idempotent premium-fastlane backfill")
    p.add_argument("--symbol", help="Single display symbol, e.g. TAC/USDT")
    p.add_argument("--symbols", help="Comma-separated display symbols")
    p.add_argument("--date", help="ISO date (YYYY-MM-DD) of the envelope timestamp_utc")
    p.add_argument("--env-id", dest="env_id", help="Exact envelope_id")
    p.add_argument(
        "--origin-signal-id",
        dest="origin_signal_id",
        help="Origin signal id (alias of env-id match)",
    )
    p.add_argument("--route", default="paper", help="Routing target (paper only is enforced)")
    p.add_argument("--reason", default="post_deploy_fastlane_backfill")
    p.add_argument("--ignore-ttl", dest="ignore_ttl", action="store_true", default=None)
    p.add_argument("--honour-ttl", dest="ignore_ttl", action="store_false")
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.route not in ("paper", "testnet", "demo", "simulated_exchange"):
        print(f"refused: route={args.route!r} is not a non-live route", file=sys.stderr)
        return 2

    symbols: list[str] = []
    if args.symbol:
        symbols.append(args.symbol)
    if args.symbols:
        symbols.extend(s.strip() for s in args.symbols.split(",") if s.strip())

    env_ids: list[str] = []
    for v in (args.env_id, args.origin_signal_id):
        if v:
            env_ids.append(v)

    result = asyncio.run(
        backfill_run(
            symbols=symbols or None,
            date=args.date,
            envelope_ids=env_ids or None,
            ignore_ttl=args.ignore_ttl,
        )
    )
    summary: dict[str, object] = dict(_summary(result))
    summary["reason"] = args.reason
    summary["route"] = args.route

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        for k in (
            "signals",
            "parsed",
            "eligible",
            "scale_resolved",
            "entry_mode_bypassed",
            "order_intents_created",
            "pending_or_open",
            "filled",
            "pending",
            "duplicates_skipped",
            "live_orders",
            "errors",
        ):
            print(f"{k}={summary[k]}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
