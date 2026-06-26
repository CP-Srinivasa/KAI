"""Tests for momentum_universe_ledger — append-only JSONL snapshots (read-only Sicht)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.observability.momentum_universe import RankedSymbol
from app.observability.momentum_universe_ledger import (
    append_snapshot,
    read_latest,
    snapshot_record,
)


def _ranked() -> list[RankedSymbol]:
    return [
        RankedSymbol("A/USDT", 0.9, 0.8, 1.0, 1, {"volume_score": 0.8}),
        RankedSymbol("B/USDT", 0.4, 0.2, 0.6, 2, {}),
    ]


class TestLedger:
    def test_snapshot_shape(self) -> None:
        rec = snapshot_record(_ranked(), now=datetime(2026, 6, 1, tzinfo=UTC))
        assert rec["count"] == 2
        universe = rec["universe"]
        assert isinstance(universe, list)
        assert universe[0]["symbol"] == "A/USDT"
        assert universe[0]["rank"] == 1
        assert "universe_score" in universe[0]

    def test_append_then_read_latest(self, tmp_path: Path) -> None:
        p = tmp_path / "u.jsonl"
        append_snapshot(p, _ranked(), now=datetime(2026, 6, 1, tzinfo=UTC))
        latest = read_latest(p)
        assert latest is not None
        assert latest["count"] == 2

    def test_read_latest_returns_newest(self, tmp_path: Path) -> None:
        p = tmp_path / "u.jsonl"
        append_snapshot(p, _ranked()[:1], now=datetime(2026, 6, 1, tzinfo=UTC))
        append_snapshot(p, _ranked(), now=datetime(2026, 6, 2, tzinfo=UTC))
        latest = read_latest(p)
        assert latest is not None
        assert latest["count"] == 2
        assert str(latest["ts"]).startswith("2026-06-02")

    def test_read_latest_missing_file(self, tmp_path: Path) -> None:
        assert read_latest(tmp_path / "nope.jsonl") is None

    def test_read_latest_skips_malformed(self, tmp_path: Path) -> None:
        p = tmp_path / "u.jsonl"
        append_snapshot(p, _ranked(), now=datetime(2026, 6, 1, tzinfo=UTC))
        with p.open("a", encoding="utf-8") as fh:
            fh.write("NOT JSON\n")
        latest = read_latest(p)
        assert latest is not None
        assert latest["count"] == 2

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        p = tmp_path / "nested" / "dir" / "u.jsonl"
        append_snapshot(p, _ranked(), now=datetime(2026, 6, 1, tzinfo=UTC))
        assert p.exists()
