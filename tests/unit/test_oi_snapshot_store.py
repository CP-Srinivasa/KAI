"""OpenInterestSnapshotStore + DiskOpenInterestAdapter (Goal V5 Phase 2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.market_data.models import OpenInterestSnapshot
from app.signals.oi_snapshot_store import (
    DiskOpenInterestAdapter,
    OpenInterestMultiVenueAdapter,
    OpenInterestSnapshotStore,
)


def _snap(symbol: str = "BTC/USDT", z: float = 1.5) -> OpenInterestSnapshot:
    return OpenInterestSnapshot(
        symbol=symbol,
        timestamp_utc=datetime.now(UTC).isoformat(),
        open_interest=12345.0,
        oi_change_zscore=z,
        source="bybit",
    )


def test_roundtrip(tmp_path: Path) -> None:
    store = OpenInterestSnapshotStore(tmp_path / "oi.json")
    store.write_many([_snap(z=2.1), _snap("ETH/USDT", z=-0.7)])
    out = store.read_all()
    assert set(out) == {"BTC/USDT", "ETH/USDT"}
    assert out["BTC/USDT"].oi_change_zscore == pytest.approx(2.1)
    assert out["ETH/USDT"].oi_change_zscore == pytest.approx(-0.7)


def test_read_missing_file_returns_empty(tmp_path: Path) -> None:
    assert OpenInterestSnapshotStore(tmp_path / "absent.json").read_all() == {}


def test_corrupt_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "oi.json"
    p.write_text("{not json", encoding="utf-8")
    assert OpenInterestSnapshotStore(p).read_all() == {}


def test_atomic_write_no_temp_left(tmp_path: Path) -> None:
    store = OpenInterestSnapshotStore(tmp_path / "oi.json")
    store.write_many([_snap()])
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".oi_")]
    assert leftovers == []


def test_one_corrupt_row_skipped_not_whole_file(tmp_path: Path) -> None:
    p = tmp_path / "oi.json"
    p.write_text(
        json.dumps(
            {
                "schema": 1,
                "snapshots": {
                    "BTC/USDT": {
                        "symbol": "BTC/USDT",
                        "timestamp_utc": "2026-06-11T00:00:00+00:00",
                        "open_interest": 1.0,
                        "oi_change_zscore": 0.5,
                        "source": "bybit",
                    },
                    "BAD": {"symbol": "BAD"},  # missing fields
                },
            }
        ),
        encoding="utf-8",
    )
    out = OpenInterestSnapshotStore(p).read_all()
    assert set(out) == {"BTC/USDT"}


@pytest.mark.asyncio
async def test_disk_adapter_reads_warm_snapshot(tmp_path: Path) -> None:
    store = OpenInterestSnapshotStore(tmp_path / "oi.json")
    store.write_many([_snap(z=3.3)])
    adapter = DiskOpenInterestAdapter(store)
    snap = await adapter.get_open_interest("BTC/USDT")
    assert snap is not None
    assert snap.oi_change_zscore == pytest.approx(3.3)


@pytest.mark.asyncio
async def test_disk_adapter_missing_file_returns_none(tmp_path: Path) -> None:
    adapter = DiskOpenInterestAdapter(OpenInterestSnapshotStore(tmp_path / "absent.json"))
    assert await adapter.get_open_interest("BTC/USDT") is None


class _StubVenue:
    def __init__(self, result: OpenInterestSnapshot | None | Exception) -> None:
        self._result = result

    async def get_open_interest(
        self, symbol: str, *, interval: str = "1h", window: int = 24
    ) -> OpenInterestSnapshot | None:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.mark.asyncio
async def test_multi_venue_falls_through_to_second(tmp_path: Path) -> None:
    adapter = OpenInterestMultiVenueAdapter([_StubVenue(None), _StubVenue(_snap(z=0.9))])
    snap = await adapter.get_open_interest("BTC/USDT")
    assert snap is not None
    assert snap.oi_change_zscore == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_multi_venue_swallows_exception_and_falls_through() -> None:
    adapter = OpenInterestMultiVenueAdapter(
        [_StubVenue(RuntimeError("boom")), _StubVenue(_snap(z=0.1))]
    )
    snap = await adapter.get_open_interest("BTC/USDT")
    assert snap is not None
    assert snap.oi_change_zscore == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_multi_venue_all_empty_returns_none() -> None:
    adapter = OpenInterestMultiVenueAdapter([_StubVenue(None), _StubVenue(None)])
    assert await adapter.get_open_interest("BTC/USDT") is None
