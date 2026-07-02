"""Paper-fill quality snapshot — couple the ≥10-fill re-entry gate with
an honest PnL/win-rate view.

The 2026-05-26 daily-strategy review showed an 11-fill gate-grün state
sitting on top of a cumulative realized PnL of -349.79 USD, with the
three most-recent closures (ETH stop -276.67, HYPE take +53.25, BTC
stop -126.37) tilted negative. Without a coupled-view CLI, "≥10
fills" looked like a green light when the underlying quality was poor.

This module is intentionally read-only. It iterates the paper-execution
audit JSONL, picks position_closed events, and produces both an
aggregate and per-symbol / per-reason cuts.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_AUDIT = Path("artifacts/paper_execution_audit.jsonl")
_CLOSE_EVENTS = ("position_closed", "position_partial_closed")


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PaperQualitySnapshot:
    closures_total: int
    window_last_n: int
    window_closures: tuple[dict[str, object], ...]
    win_rate: float
    sum_trade_pnl_usd: float
    avg_trade_pnl_usd: float
    latest_realized_pnl_usd: float | None
    by_symbol: dict[str, dict[str, float]]
    by_reason: dict[str, dict[str, float]]
    audit_path: str


@dataclass
class _SymCounter:
    count: int = 0
    wins: int = 0
    losses: int = 0
    sum_pnl: float = 0.0


def build_paper_quality_snapshot(
    *,
    audit_path: str | Path = _DEFAULT_AUDIT,
    last_n: int = 25,
) -> PaperQualitySnapshot:
    if last_n < 1:
        raise ValueError("last_n must be >= 1")

    path = Path(audit_path)
    closures: list[dict[str, object]] = []
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("event_type") in _CLOSE_EVENTS:
                closures.append(rec)

    total = len(closures)
    window = closures[-last_n:]
    wins = 0
    losses = 0
    sum_pnl = 0.0
    latest_realized: float | None = None
    by_symbol: dict[str, _SymCounter] = defaultdict(_SymCounter)
    by_reason: dict[str, _SymCounter] = defaultdict(_SymCounter)

    for rec in window:
        pnl = _coerce_float(rec.get("trade_pnl_usd"))
        if pnl is None:
            pnl = _coerce_float(rec.get("realized_pnl_usd")) or 0.0
        sum_pnl += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        symbol = str(rec.get("symbol", "?"))
        sc = by_symbol[symbol]
        sc.count += 1
        sc.sum_pnl += pnl
        if pnl > 0:
            sc.wins += 1
        elif pnl < 0:
            sc.losses += 1
        reason = str(rec.get("reason", "?"))
        rc = by_reason[reason]
        rc.count += 1
        rc.sum_pnl += pnl
        if pnl > 0:
            rc.wins += 1
        elif pnl < 0:
            rc.losses += 1

    # Latest realized_pnl_usd — operator's running cumulative value.
    # Source-of-truth comes from the most recent closure that carries
    # realized_pnl_usd (per NEO-P-101-r2 the field is cumulative).
    for rec in reversed(window):
        cum = _coerce_float(rec.get("realized_pnl_usd"))
        if cum is not None:
            latest_realized = cum
            break

    decided = wins + losses
    win_rate = (wins / decided) if decided > 0 else 0.0
    avg_pnl = (sum_pnl / len(window)) if window else 0.0

    return PaperQualitySnapshot(
        closures_total=total,
        window_last_n=last_n,
        window_closures=tuple(dict(rec) for rec in window),
        win_rate=win_rate,
        sum_trade_pnl_usd=sum_pnl,
        avg_trade_pnl_usd=avg_pnl,
        latest_realized_pnl_usd=latest_realized,
        by_symbol={
            sym: {
                "count": float(sc.count),
                "wins": float(sc.wins),
                "losses": float(sc.losses),
                "sum_pnl_usd": sc.sum_pnl,
            }
            for sym, sc in by_symbol.items()
        },
        by_reason={
            reason: {
                "count": float(rc.count),
                "wins": float(rc.wins),
                "losses": float(rc.losses),
                "sum_pnl_usd": rc.sum_pnl,
            }
            for reason, rc in by_reason.items()
        },
        audit_path=str(path),
    )


__all__ = ["PaperQualitySnapshot", "build_paper_quality_snapshot"]
