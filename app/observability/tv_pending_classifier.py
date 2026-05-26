"""TradingView-pending breakdown — classify the unresolved-queue tail
by age, ticker, and source idempotency-key.

The 2026-05-26 daily-strategy review showed 75 unpromoted TV events
with the freshest entry from 2026-05-11. Without a structured
breakdown the operator could not tell whether the tail was the same
ticker repeated, the same external_event_id replayed, or a real
fan-out — all three need different remediations.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_AUDIT = Path("artifacts/tradingview_pending_signals.jsonl")
_TIMESTAMP_FIELDS = (
    "received_at",
    "promoted_at",
    "created_at",
    "timestamp_utc",
    "event_timestamp",
)


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _age_bucket(age_days: float) -> str:
    if age_days < 1:
        return "<1d"
    if age_days < 7:
        return "1-7d"
    if age_days < 14:
        return "7-14d"
    return ">14d"


@dataclass(frozen=True)
class TvPendingBreakdown:
    total: int
    by_age_bucket: dict[str, int]
    by_ticker: list[tuple[str, int]]
    by_external_event_id: list[tuple[str, int]]
    audit_path: str
    oldest_iso: str | None = None
    newest_iso: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "by_age_bucket": self.by_age_bucket,
            "by_ticker": self.by_ticker,
            "by_external_event_id": self.by_external_event_id,
            "oldest_iso": self.oldest_iso,
            "newest_iso": self.newest_iso,
            "audit_path": self.audit_path,
        }


def build_tv_pending_breakdown(
    *,
    audit_path: str | Path = _DEFAULT_AUDIT,
    now_utc: datetime | None = None,
) -> TvPendingBreakdown:
    path = Path(audit_path)
    by_age: Counter[str] = Counter()
    tickers: Counter[str] = Counter()
    externals: Counter[str] = Counter()
    timestamps: list[datetime] = []
    total = 0
    now = now_utc if now_utc is not None else datetime.now(UTC)

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
            total += 1
            ticker = str(rec.get("ticker", "?")) or "?"
            tickers[ticker] += 1
            ext = str(rec.get("external_event_id", "?")) or "?"
            externals[ext] += 1
            ts: datetime | None = None
            for field_name in _TIMESTAMP_FIELDS:
                ts = _parse_iso(rec.get(field_name))
                if ts is not None:
                    break
            if ts is None:
                by_age["unknown"] += 1
                continue
            timestamps.append(ts)
            age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
            by_age[_age_bucket(age_days)] += 1

    oldest = min(timestamps).isoformat() if timestamps else None
    newest = max(timestamps).isoformat() if timestamps else None
    return TvPendingBreakdown(
        total=total,
        by_age_bucket=dict(by_age),
        by_ticker=tickers.most_common(),
        by_external_event_id=externals.most_common(),
        audit_path=str(path),
        oldest_iso=oldest,
        newest_iso=newest,
    )


__all__ = ["TvPendingBreakdown", "build_tv_pending_breakdown"]
