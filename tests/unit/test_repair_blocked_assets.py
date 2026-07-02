"""Tests for the D-227 blocked_assets repair (scripts.repair_blocked_assets).

Covers the pure ``repair_records`` transform: only empty + in-window +
ticker-mapped + resolvable records are filled, everything else passes through
unchanged. The script mutates nothing in these tests — pure logic only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.repair_blocked_assets import repair_records

NOW = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)


def _rec(doc_id: str, blocked_assets=None, age_days: float = 1.0, **extra) -> dict:
    rec = {
        "document_id": doc_id,
        "block_reason": "low_directional_confidence",
        "blocked_at": (NOW - timedelta(days=age_days)).isoformat(),
        "sentiment_label": "bullish",
    }
    if blocked_assets is not None:
        rec["blocked_assets"] = blocked_assets
    rec.update(extra)
    return rec


def test_fills_empty_recent_record_from_tickers() -> None:
    records = [_rec("d1")]
    out, stats = repair_records(records, {"d1": ["BTC/USDT", "ETH/USDT"]}, since_days=5, now=NOW)
    assert stats.repaired == 1
    assert out[0]["blocked_assets"] == ["BTC/USDT", "ETH/USDT"]
    assert out[0]["blocked_assets_repaired"] is True


def test_skips_already_populated_record() -> None:
    records = [_rec("d1", blocked_assets=["BTC/USDT"])]
    out, stats = repair_records(records, {"d1": ["ETH/USDT"]}, since_days=5, now=NOW)
    assert stats.already_populated == 1
    assert stats.repaired == 0
    # untouched — no provenance marker, original value preserved
    assert out[0]["blocked_assets"] == ["BTC/USDT"]
    assert "blocked_assets_repaired" not in out[0]


def test_skips_out_of_window_record() -> None:
    records = [_rec("d1", age_days=10)]
    out, stats = repair_records(records, {"d1": ["BTC/USDT"]}, since_days=5, now=NOW)
    assert stats.out_of_window == 1
    assert stats.repaired == 0
    assert "blocked_assets" not in out[0]


def test_skips_when_no_ticker_match() -> None:
    records = [_rec("d1")]
    out, stats = repair_records(records, {}, since_days=5, now=NOW)
    assert stats.no_ticker_match == 1
    assert stats.repaired == 0


def test_skips_when_tickers_unresolvable() -> None:
    records = [_rec("d1")]
    # naked + unknown -> resolve_eligible_symbols returns [] -> not repaired
    out, stats = repair_records(records, {"d1": ["BTC", "NOTACOIN/USDT"]}, since_days=5, now=NOW)
    assert stats.unresolvable == 1
    assert stats.repaired == 0
    assert "blocked_assets" not in out[0]


def test_passthrough_preserves_order_and_count() -> None:
    records = [_rec("d1"), _rec("d2", blocked_assets=["ETH/USDT"]), _rec("d3", age_days=99)]
    out, stats = repair_records(records, {"d1": ["BTC/USDT"]}, since_days=5, now=NOW)
    assert [r["document_id"] for r in out] == ["d1", "d2", "d3"]
    assert stats.scanned == 3
    assert stats.repaired == 1
