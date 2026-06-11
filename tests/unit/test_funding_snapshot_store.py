"""FundingSnapshotStore + DiskFundingAdapter + MultiVenue + Shadow-Log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.market_data.models import FundingRateSnapshot
from app.signals.funding_snapshot_store import (
    DiskFundingAdapter,
    FundingMultiVenueAdapter,
    FundingSnapshotStore,
    append_funding_shadow_log,
)


def _snap(
    symbol: str = "BTC/USDT", rate: float = 0.0001, source: str = "bybit"
) -> FundingRateSnapshot:
    return FundingRateSnapshot(
        symbol=symbol,
        timestamp_utc="2026-06-11T12:00:00+00:00",
        rate=rate,
        mark_price=65000.0,
        index_price=64999.0,
        next_funding_time_utc="2026-06-11T16:00:00+00:00",
        source=source,
    )


# ── Store roundtrip ───────────────────────────────────────────────────────────


def test_store_write_then_read_roundtrip(tmp_path: Path) -> None:
    store = FundingSnapshotStore(tmp_path / "funding.json")
    n = store.write_many([_snap("BTC/USDT", 0.0001), _snap("ETH/USDT", -0.0002, "binance")])
    assert n == 2
    out = store.read_all()
    assert set(out) == {"BTC/USDT", "ETH/USDT"}
    assert out["BTC/USDT"].rate == pytest.approx(0.0001)
    assert out["ETH/USDT"].rate == pytest.approx(-0.0002)
    assert out["ETH/USDT"].source == "binance"


def test_store_read_missing_file_returns_empty(tmp_path: Path) -> None:
    store = FundingSnapshotStore(tmp_path / "nope.json")
    assert store.read_all() == {}
    assert store.read("BTC/USDT") is None


def test_store_read_corrupt_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "funding.json"
    p.write_text("{not json", encoding="utf-8")
    store = FundingSnapshotStore(p)
    assert store.read_all() == {}


def test_store_write_is_atomic_leaves_no_tmp(tmp_path: Path) -> None:
    store = FundingSnapshotStore(tmp_path / "funding.json")
    store.write_many([_snap()])
    leftovers = list(tmp_path.glob(".funding_*.tmp"))
    assert leftovers == []


def test_store_skips_corrupt_row_keeps_valid(tmp_path: Path) -> None:
    p = tmp_path / "funding.json"
    payload = {
        "schema": 1,
        "written_at_utc": "2026-06-11T12:00:00+00:00",
        "snapshots": {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "timestamp_utc": "x",
                "rate": 0.0001,
                "source": "bybit",
            },
            "BAD": {"symbol": "BAD", "rate": "not-a-float"},  # garbage → skipped
        },
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    out = FundingSnapshotStore(p).read_all()
    assert "BTC/USDT" in out
    assert "BAD" not in out


# ── DiskFundingAdapter ────────────────────────────────────────────────────────


async def test_disk_adapter_reads_warm_snapshot(tmp_path: Path) -> None:
    store = FundingSnapshotStore(tmp_path / "funding.json")
    store.write_many([_snap("BTC/USDT", 0.0003)])
    adapter = DiskFundingAdapter(store)
    snap = await adapter.get_funding_rate("BTC/USDT")
    assert snap is not None
    assert snap.rate == pytest.approx(0.0003)


async def test_disk_adapter_missing_file_returns_none(tmp_path: Path) -> None:
    adapter = DiskFundingAdapter(FundingSnapshotStore(tmp_path / "nope.json"))
    assert await adapter.get_funding_rate("BTC/USDT") is None


# ── MultiVenue fallback ───────────────────────────────────────────────────────


class _StubVenue:
    def __init__(self, snap=None, exc=None):
        self._snap = snap
        self._exc = exc
        self.calls = 0

    async def get_funding_rate(self, symbol: str):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._snap


async def test_multivenue_first_wins() -> None:
    a = _StubVenue(snap=_snap(source="bybit"))
    b = _StubVenue(snap=_snap(source="binance"))
    adapter = FundingMultiVenueAdapter([a, b])
    snap = await adapter.get_funding_rate("BTC/USDT")
    assert snap is not None and snap.source == "bybit"
    assert b.calls == 0  # fallback not consulted


async def test_multivenue_falls_through_on_none() -> None:
    a = _StubVenue(snap=None)
    b = _StubVenue(snap=_snap(source="binance"))
    snap = await FundingMultiVenueAdapter([a, b]).get_funding_rate("BTC/USDT")
    assert snap is not None and snap.source == "binance"
    assert a.calls == 1 and b.calls == 1


async def test_multivenue_swallows_exception_and_falls_through() -> None:
    a = _StubVenue(exc=RuntimeError("boom"))
    b = _StubVenue(snap=_snap(source="binance"))
    snap = await FundingMultiVenueAdapter([a, b]).get_funding_rate("BTC/USDT")
    assert snap is not None and snap.source == "binance"


async def test_multivenue_all_empty_returns_none() -> None:
    adapter = FundingMultiVenueAdapter([_StubVenue(snap=None), _StubVenue(exc=ValueError())])
    assert await adapter.get_funding_rate("BTC/USDT") is None


# ── Shadow-Log ────────────────────────────────────────────────────────────────


def test_shadow_log_appends_jsonl(tmp_path: Path) -> None:
    log = tmp_path / "sub" / "funding_shadow.jsonl"
    append_funding_shadow_log(
        log,
        symbol="BTC/USDT",
        rate=0.0001,
        direction="long",
        source="bybit",
        source_trust=0.5,
        evidence_value=0.4,
        evidence_direction_aligned=-1,
    )
    append_funding_shadow_log(
        log,
        symbol="ETH/USDT",
        rate=-0.0002,
        direction="short",
        source="binance",
        source_trust=0.5,
        evidence_value=0.6,
        evidence_direction_aligned=1,
    )
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["symbol"] == "BTC/USDT"
    assert rec["evidence_direction_aligned"] == -1
    assert rec["source_trust"] == 0.5


def test_shadow_log_failure_is_swallowed(tmp_path: Path) -> None:
    # Point at a path whose parent is a file → mkdir/open will fail; must not raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    bad = blocker / "child" / "log.jsonl"
    append_funding_shadow_log(
        bad,
        symbol="BTC/USDT",
        rate=0.0001,
        direction="long",
        source="bybit",
        source_trust=0.5,
        evidence_value=0.4,
        evidence_direction_aligned=-1,
    )  # no exception expected
