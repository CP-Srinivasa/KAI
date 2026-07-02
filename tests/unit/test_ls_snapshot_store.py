"""LongShortRatioSnapshotStore + DiskLongShortRatioAdapter (Goal V5 Phase 3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.market_data.models import LongShortRatioSnapshot
from app.signals.ls_snapshot_store import (
    DiskLongShortRatioAdapter,
    LongShortRatioMultiVenueAdapter,
    LongShortRatioSnapshotStore,
)


def _snap(symbol: str = "BTC/USDT", ratio: float = 0.62) -> LongShortRatioSnapshot:
    return LongShortRatioSnapshot(
        symbol=symbol,
        timestamp_utc=datetime.now(UTC).isoformat(),
        long_account_ratio=ratio,
        source="bybit",
    )


def test_roundtrip(tmp_path: Path) -> None:
    store = LongShortRatioSnapshotStore(tmp_path / "ls.json")
    store.write_many([_snap(ratio=0.62), _snap("ETH/USDT", ratio=0.41)])
    out = store.read_all()
    assert set(out) == {"BTC/USDT", "ETH/USDT"}
    assert out["BTC/USDT"].long_account_ratio == pytest.approx(0.62)
    assert out["ETH/USDT"].long_account_ratio == pytest.approx(0.41)


def test_read_missing_file_returns_empty(tmp_path: Path) -> None:
    assert LongShortRatioSnapshotStore(tmp_path / "absent.json").read_all() == {}


def test_corrupt_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "ls.json"
    p.write_text("{not json", encoding="utf-8")
    assert LongShortRatioSnapshotStore(p).read_all() == {}


def test_atomic_write_no_temp_left(tmp_path: Path) -> None:
    store = LongShortRatioSnapshotStore(tmp_path / "ls.json")
    store.write_many([_snap()])
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".ls_")]
    assert leftovers == []


def test_one_corrupt_row_skipped_not_whole_file(tmp_path: Path) -> None:
    p = tmp_path / "ls.json"
    p.write_text(
        json.dumps(
            {
                "schema": 1,
                "snapshots": {
                    "BTC/USDT": {
                        "symbol": "BTC/USDT",
                        "timestamp_utc": "2026-06-11T00:00:00+00:00",
                        "long_account_ratio": 0.55,
                        "source": "bybit",
                    },
                    "BAD": {"symbol": "BAD"},  # missing fields
                },
            }
        ),
        encoding="utf-8",
    )
    out = LongShortRatioSnapshotStore(p).read_all()
    assert set(out) == {"BTC/USDT"}


@pytest.mark.asyncio
async def test_disk_adapter_reads_warm_snapshot(tmp_path: Path) -> None:
    store = LongShortRatioSnapshotStore(tmp_path / "ls.json")
    store.write_many([_snap(ratio=0.77)])
    adapter = DiskLongShortRatioAdapter(store)
    snap = await adapter.get_long_short_ratio("BTC/USDT")
    assert snap is not None
    assert snap.long_account_ratio == pytest.approx(0.77)


@pytest.mark.asyncio
async def test_disk_adapter_missing_file_returns_none(tmp_path: Path) -> None:
    adapter = DiskLongShortRatioAdapter(LongShortRatioSnapshotStore(tmp_path / "absent.json"))
    assert await adapter.get_long_short_ratio("BTC/USDT") is None


class _StubVenue:
    def __init__(self, result: LongShortRatioSnapshot | None | Exception) -> None:
        self._result = result

    async def get_long_short_ratio(
        self, symbol: str, *, interval: str = "1h"
    ) -> LongShortRatioSnapshot | None:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.mark.asyncio
async def test_multi_venue_falls_through_to_second() -> None:
    adapter = LongShortRatioMultiVenueAdapter([_StubVenue(None), _StubVenue(_snap(ratio=0.58))])
    snap = await adapter.get_long_short_ratio("BTC/USDT")
    assert snap is not None
    assert snap.long_account_ratio == pytest.approx(0.58)


@pytest.mark.asyncio
async def test_multi_venue_swallows_exception_and_falls_through() -> None:
    adapter = LongShortRatioMultiVenueAdapter(
        [_StubVenue(RuntimeError("boom")), _StubVenue(_snap(ratio=0.33))]
    )
    snap = await adapter.get_long_short_ratio("BTC/USDT")
    assert snap is not None
    assert snap.long_account_ratio == pytest.approx(0.33)


@pytest.mark.asyncio
async def test_multi_venue_all_empty_returns_none() -> None:
    adapter = LongShortRatioMultiVenueAdapter([_StubVenue(None), _StubVenue(None)])
    assert await adapter.get_long_short_ratio("BTC/USDT") is None
