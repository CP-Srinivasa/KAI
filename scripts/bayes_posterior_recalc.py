#!/usr/bin/env python3
"""Recompute Bayes-Posterior state from paper_execution_audit.

Goal-pin 2026-05-16 V4 (operator-spec'd):
- Hit definition: trade_pnl_usd > fee_usd → hit, < -fee_usd → miss, else inconclusive.
- Decay: lifetime + rolling-90d parallel.
- Granularity: per (source, symbol, direction).
- Source linkage: FIFO match of position_closed events to the most recent
  preceding order_filled (side="buy" for long entries, side="sell" for short
  entries) of the same symbol. The order_filled.source is the canonical
  source label; missing source → ``UNSOURCED_LABEL`` ("tradingloop").

Output:
- ``artifacts/bayes_posterior_state.json`` — snapshot of all buckets with
  lifetime + rolling-90d posteriors.
- ``artifacts/bayes_posterior_audit.jsonl`` — append-only per-outcome log
  (one line per classified trade). Operator-readable trail of "which trade
  moved which bucket".

Read-only: this script DOES NOT update the live Bayes-Confidence-Engine
(it stays SHADOW_ONLY). Eligibility and sizing do not yet read the new
state file. Integration is a separate Operator-signed PR after track-record.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.learning.bayes_posterior import (  # noqa: E402
    UNSOURCED_LABEL,
    TradeOutcome,
    build_posterior_report,
    classify_outcome,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("bayes-posterior-recalc")


def _open_side_for_position(position_side: str) -> str:
    """Long positions open with buy, short positions open with sell."""
    return "buy" if (position_side or "").lower() == "long" else "sell"


def _load_outcomes_from_audit(audit_path: Path) -> list[TradeOutcome]:
    """Walk paper_execution_audit.jsonl in order; match opens→closes by symbol FIFO.

    Why FIFO-by-symbol instead of IDs: the position_closed event records the
    CLOSE order_id (a sell-back order for longs), not the open. There is no
    direct link to the original open in the audit schema. KAI's paper engine
    only ever holds one position per symbol at a time (max_open_positions
    gate + position_exists guard), so a FIFO queue per symbol is unambiguous.

    For each position_closed we pop the most recent matching open and use
    its ``source`` field. If the open had no source (typical for old
    TradingLoop runs pre-source-stamping), we label it
    ``UNSOURCED_LABEL`` so the bucket is visible but separable.
    """
    if not audit_path.exists():
        logger.error("paper_execution_audit.jsonl missing at %s", audit_path)
        return []

    opens_by_symbol: dict[str, list[dict[str, Any]]] = {}
    outcomes: list[TradeOutcome] = []

    with audit_path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            etype = event.get("event_type")
            if etype == "order_filled":
                # Open-fills queue by symbol. We don't know yet which side
                # the position will be — just stash for later match.
                symbol = event.get("symbol")
                if not symbol:
                    continue
                opens_by_symbol.setdefault(symbol, []).append(event)
            elif etype == "position_closed":
                symbol = event.get("symbol")
                # The position_closed schema v2 carries trade_pnl_usd
                # (per-trade, fees-netted) and fee_usd (close-side fee).
                # Older v1 rows only had realized_pnl_usd (cumulative) —
                # those are unusable for per-trade Bayes update and skipped.
                if "trade_pnl_usd" not in event:
                    continue
                position_side = (event.get("position_side") or "").lower()
                if position_side not in {"long", "short"}:
                    continue

                # Pop the most recent matching open fill for this symbol.
                # The expected match is an order_filled with side matching
                # the position-open direction (buy for long, sell for short).
                open_side = _open_side_for_position(position_side)
                queue = opens_by_symbol.get(symbol, [])
                matched_open: dict[str, Any] | None = None
                # Walk queue from most-recent backwards looking for a matching
                # open side. Any non-matching opens stay (could be no-op fills
                # from a different scenario; safer to leave them queued).
                for i in range(len(queue) - 1, -1, -1):
                    if (queue[i].get("side") or "").lower() == open_side:
                        matched_open = queue.pop(i)
                        break

                source_raw = (matched_open or {}).get("source") or ""
                source = source_raw.strip() if isinstance(source_raw, str) else ""
                if not source:
                    source = UNSOURCED_LABEL

                trade_pnl = float(event.get("trade_pnl_usd") or 0.0)
                fee_usd = float(event.get("fee_usd") or 0.0)
                outcome_label = classify_outcome(trade_pnl_usd=trade_pnl, fee_usd=fee_usd)

                outcomes.append(
                    TradeOutcome(
                        fill_id=str(event.get("fill_id") or ""),
                        timestamp_utc=str(event.get("timestamp_utc") or ""),
                        source=source,
                        symbol=str(symbol),
                        direction=position_side,  # type: ignore[arg-type]
                        trade_pnl_usd=trade_pnl,
                        fee_usd=fee_usd,
                        outcome=outcome_label,
                        reason=str(event.get("reason") or ""),
                    )
                )

    return outcomes


def _write_audit_stream(out_path: Path, outcomes: list[TradeOutcome]) -> None:
    """Atomic write of the per-outcome audit JSONL."""
    tmp_path = out_path.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        for tc in outcomes:
            row = {
                "schema_version": "v1",
                "report_type": "bayes_posterior_outcome",
                "fill_id": tc.fill_id,
                "timestamp_utc": tc.timestamp_utc,
                "source": tc.source,
                "symbol": tc.symbol,
                "direction": tc.direction,
                "trade_pnl_usd": tc.trade_pnl_usd,
                "fee_usd": tc.fee_usd,
                "outcome": tc.outcome,
                "reason": tc.reason,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_path.replace(out_path)


def main() -> int:
    audit_path = _REPO_ROOT / "artifacts" / "paper_execution_audit.jsonl"
    outcomes = _load_outcomes_from_audit(audit_path)

    state = build_posterior_report(outcomes)
    state_path = _REPO_ROOT / "artifacts" / "bayes_posterior_state.json"
    tmp_path = state_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(state_path)

    audit_out = _REPO_ROOT / "artifacts" / "bayes_posterior_audit.jsonl"
    _write_audit_stream(audit_out, outcomes)

    n_hits = sum(1 for o in outcomes if o.outcome == "hit")
    n_miss = sum(1 for o in outcomes if o.outcome == "miss")
    n_inc = sum(1 for o in outcomes if o.outcome == "inconclusive")
    logger.info(
        "wrote %s buckets=%d outcomes=%d (hits=%d miss=%d inconclusive=%d)",
        state_path,
        state["n_buckets"],
        len(outcomes),
        n_hits,
        n_miss,
        n_inc,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
