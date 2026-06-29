"""Tests für das append-only Eligibility-Audit-Ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.observability.symbol_eligibility_ledger import (
    append_eligibility_snapshot,
    read_latest_eligibility,
)
from app.trading.symbol_eligibility import EligibilityVerdict


def _verdicts() -> list[EligibilityVerdict]:
    return [
        EligibilityVerdict("BTC/USDT", True, []),
        EligibilityVerdict("SLX/USDT", False, ["no_canonical_venue_data"]),
    ]


def test_append_and_read_latest_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "elig.jsonl"
    now = datetime(2026, 6, 29, tzinfo=UTC)
    rec = append_eligibility_snapshot(p, _verdicts(), now=now)
    assert rec["count"] == 2
    assert rec["eligible_count"] == 1
    latest = read_latest_eligibility(p)
    assert latest is not None
    assert latest["count"] == 2
    syms = {row["symbol"]: row for row in latest["verdicts"]}
    assert syms["BTC/USDT"]["eligible"] is True
    assert syms["SLX/USDT"]["reasons"] == ["no_canonical_venue_data"]


def test_append_is_append_only(tmp_path: Path) -> None:
    p = tmp_path / "elig.jsonl"
    append_eligibility_snapshot(p, _verdicts(), now=datetime(2026, 6, 29, tzinfo=UTC))
    append_eligibility_snapshot(p, _verdicts()[:1], now=datetime(2026, 6, 30, tzinfo=UTC))
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 2
    latest = read_latest_eligibility(p)
    assert latest is not None and latest["count"] == 1  # newest wins


def test_read_latest_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_latest_eligibility(tmp_path / "nope.jsonl") is None
