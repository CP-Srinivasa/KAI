"""Read-only capacity / de-stau report for the trading book.

Why this module exists
----------------------
The proven blocker for env ENV-TG-001275462917-23879-502ef70a was
``max_open_positions_reached:6>=6`` — the book was full, so every new premium
signal (and loop cycle: XRP, DOGE) was risk-rejected. Operators need a fast,
read-only view of *why* capacity is exhausted and *which* pending orders are
provably stale, without ever touching a live position.

Safety
------
This module is strictly READ-ONLY. It derives open positions from the paper
audit (the source of truth via ``replay_paper_audit``) and pending orders from
the bridge log. It NEVER closes or deletes anything. It only *flags* pending
orders whose age exceeds the configured TTL as archival candidates — the actual
archival is an explicit operator action documented in the runbook, gated on the
order being provably non-open.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.execution.audit_replay import replay_paper_audit

_DEFAULT_AUDIT = Path("artifacts/paper_execution_audit.jsonl")
_DEFAULT_PENDING = Path("artifacts/bridge_pending_orders.jsonl")

# Terminal bridge stages — once an envelope reaches one of these it is no longer
# a live pending order.
_TERMINAL_STAGES = frozenset(
    {
        "filled",
        "filled_duplicate_suppressed",
        "expired",
        "rejected_fill",
        "rejected_incomplete",
        "rejected_position_exists",
        "rejected_risk",
        "rejected_scale_review",
        "rejected_size",
        "skipped_source",
    }
)


@dataclass
class StalePending:
    envelope_id: str
    symbol: str | None
    last_stage: str
    received_utc: str | None
    age_hours: float | None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


@dataclass
class CapacityReport:
    now_utc: str
    max_open_positions: int
    open_count: int
    slots_free: int
    open_symbols: list[str]
    book_full: bool
    pending_count: int
    stale_pending: list[StalePending] = field(default_factory=list)
    portfolio_available: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "now_utc": self.now_utc,
            "max_open_positions": self.max_open_positions,
            "open_count": self.open_count,
            "slots_free": self.slots_free,
            "open_symbols": list(self.open_symbols),
            "book_full": self.book_full,
            "pending_count": self.pending_count,
            "stale_pending": [s.to_dict() for s in self.stale_pending],
            "portfolio_available": self.portfolio_available,
            "notes": list(self.notes),
        }


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _latest_pending(pending_path: Path) -> dict[str, dict[str, Any]]:
    """Return the LAST bridge row per envelope_id."""
    latest: dict[str, dict[str, Any]] = {}
    if not pending_path.exists():
        return latest
    try:
        lines = pending_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return latest
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        env_id = rec.get("envelope_id")
        if isinstance(env_id, str) and env_id:
            latest[env_id] = rec
    return latest


def build_capacity_report(
    *,
    max_open_positions: int,
    audit_path: str | Path = _DEFAULT_AUDIT,
    pending_path: str | Path = _DEFAULT_PENDING,
    ttl_hours: float = 24.0,
    now: datetime | None = None,
) -> CapacityReport:
    """Build a read-only capacity report. Never mutates state."""
    now_dt = now or datetime.now(UTC)
    replay = replay_paper_audit(Path(audit_path))
    open_symbols = sorted(replay.positions.keys())
    open_count = len(open_symbols)
    slots_free = max(0, max_open_positions - open_count)

    report = CapacityReport(
        now_utc=now_dt.isoformat(),
        max_open_positions=max_open_positions,
        open_count=open_count,
        slots_free=slots_free,
        open_symbols=open_symbols,
        book_full=open_count >= max_open_positions,
        pending_count=0,
        portfolio_available=replay.available,
    )
    if not replay.available:
        report.notes.append(f"portfolio replay unavailable: {replay.error}")

    latest = _latest_pending(Path(pending_path))
    pending_rows = [
        rec
        for rec in latest.values()
        if str(rec.get("stage")) not in _TERMINAL_STAGES
    ]
    report.pending_count = len(pending_rows)

    cutoff = now_dt - timedelta(hours=ttl_hours)
    for rec in pending_rows:
        received = _parse_ts(rec.get("origin_envelope_timestamp")) or _parse_ts(
            rec.get("timestamp_utc")
        )
        age_hours = (now_dt - received).total_seconds() / 3600.0 if received else None
        if received is not None and received < cutoff:
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            symbol = rec.get("symbol") or (payload or {}).get("display_symbol")
            report.stale_pending.append(
                StalePending(
                    envelope_id=str(rec.get("envelope_id")),
                    symbol=symbol if isinstance(symbol, str) else None,
                    last_stage=str(rec.get("stage")),
                    received_utc=received.isoformat(),
                    age_hours=round(age_hours, 2) if age_hours is not None else None,
                )
            )

    if report.book_full:
        report.notes.append(
            "book full — new entries are risk-rejected (REJECT_MAX_OPEN_POSITIONS). "
            "Raise RISK_MAX_OPEN_POSITIONS or close/let-expire a position. "
            "NEVER delete an open live position; only archive provably-expired pendings."
        )
    if report.stale_pending:
        report.notes.append(
            f"{len(report.stale_pending)} pending order(s) older than {ttl_hours}h — "
            "archival candidates (operator action per runbook)."
        )
    return report


def render_text(report: CapacityReport) -> str:
    lines = [
        f"Capacity report @ {report.now_utc}",
        f"  open positions : {report.open_count}/{report.max_open_positions}"
        f"  (slots free: {report.slots_free}, book_full={report.book_full})",
        f"  open symbols   : {', '.join(report.open_symbols) or '(none)'}",
        f"  pending orders : {report.pending_count}",
        f"  stale pending  : {len(report.stale_pending)}",
    ]
    for s in report.stale_pending:
        lines.append(
            f"    - {s.symbol or '?'} {s.envelope_id} "
            f"age={s.age_hours}h stage={s.last_stage}"
        )
    for note in report.notes:
        lines.append(f"  ! {note}")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Read-only trading-book capacity report")
    ap.add_argument("--max-open-positions", type=int, required=True)
    ap.add_argument("--audit", default=str(_DEFAULT_AUDIT))
    ap.add_argument("--pending", default=str(_DEFAULT_PENDING))
    ap.add_argument("--ttl-hours", type=float, default=24.0)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    report = build_capacity_report(
        max_open_positions=args.max_open_positions,
        audit_path=args.audit,
        pending_path=args.pending,
        ttl_hours=args.ttl_hours,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())


__all__ = [
    "CapacityReport",
    "StalePending",
    "build_capacity_report",
    "render_text",
]
