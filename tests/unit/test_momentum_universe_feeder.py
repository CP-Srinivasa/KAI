"""Tests for momentum_universe_feeder (G2) — feed universe → PAPER, cohort-tagged.

Gated (default-off), capped, deduped, and gated by the G1 rotation FSM: symbols
the FSM flagged/archived are NOT fed (closes the G1→G2 loop). Every fed cycle is
tagged analysis_source="momentum_universe" so edge-report can isolate the cohort.
The trading loop is injected so tests never touch the real pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.learning.asset_lifecycle import AssetStatus
from app.learning.asset_rotation_shadow import AssetRotationState, save_state
from app.observability.momentum_universe import RankedSymbol
from app.observability.momentum_universe_feeder import COHORT, run_momentum_feeder
from app.observability.momentum_universe_ledger import append_snapshot


class _FeedCfg:
    def __init__(self, *, enabled: bool = True, top_n: int = 15, max_per_run: int = 5) -> None:
        self.enabled = enabled
        self.top_n = top_n
        self.max_per_run = max_per_run


class _Settings:
    def __init__(self, cfg: _FeedCfg) -> None:
        self.momentum_universe_feed = cfg


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **over: Any) -> None:
    monkeypatch.setattr(
        "app.observability.momentum_universe_feeder.get_settings",
        lambda: _Settings(_FeedCfg(**over)),
    )


def _ranked(*symbols: str) -> list[RankedSymbol]:
    return [RankedSymbol(s, 0.9, 0.9, 0.9, i + 1, {}) for i, s in enumerate(symbols)]


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    calls: list[dict[str, Any]] = []

    async def fake_cycle(**kwargs: Any) -> None:
        calls.append(kwargs)

    return calls, fake_cycle


class TestMomentumFeeder:
    async def test_disabled_is_no_op(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _patch_settings(monkeypatch, enabled=False)
        res = await run_momentum_feeder(
            ledger_path=tmp_path / "u.jsonl",
            state_path=tmp_path / "s.json",
            fed_path=tmp_path / "f.jsonl",
        )
        assert res == {"enabled": False}

    async def test_no_universe(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _patch_settings(monkeypatch)
        res = await run_momentum_feeder(
            ledger_path=tmp_path / "missing.jsonl",
            state_path=tmp_path / "s.json",
            fed_path=tmp_path / "f.jsonl",
        )
        assert res["fed"] == 0
        assert res["reason"] == "no_universe"

    async def test_feeds_and_tags_cohort_skipping_flagged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger,
            _ranked("BTC/USDT", "DOGE/USDT", "SOL/USDT"),
            now=datetime(2026, 6, 26, tzinfo=UTC),
        )
        state = tmp_path / "s.json"
        save_state(state, {"DOGE/USDT": AssetRotationState(AssetStatus.ROTATION_FLAGGED, 3)})
        fed = tmp_path / "f.jsonl"
        calls, fake_cycle = _recorder()
        _patch_settings(monkeypatch)

        res = await run_momentum_feeder(
            ledger_path=ledger, state_path=state, fed_path=fed, run_cycle=fake_cycle
        )
        assert [c["symbol"] for c in calls] == ["BTC/USDT", "SOL/USDT"]  # DOGE flagged → skipped
        assert all(c["analysis_source"] == COHORT for c in calls)
        assert all(c["mode"] == "paper" for c in calls)
        assert res["fed"] == 2
        assert res["skipped_flagged"] == 1

    async def test_archived_also_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ledger = tmp_path / "u.jsonl"
        append_snapshot(ledger, _ranked("OLD/USDT"), now=datetime(2026, 6, 26, tzinfo=UTC))
        state = tmp_path / "s.json"
        save_state(state, {"OLD/USDT": AssetRotationState(AssetStatus.ARCHIVED, 0)})
        calls, fake_cycle = _recorder()
        _patch_settings(monkeypatch)
        res = await run_momentum_feeder(
            ledger_path=ledger,
            state_path=state,
            fed_path=tmp_path / "f.jsonl",
            run_cycle=fake_cycle,
        )
        assert res["fed"] == 0
        assert res["skipped_flagged"] == 1
        assert calls == []

    async def test_dedup_across_runs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        ledger = tmp_path / "u.jsonl"
        append_snapshot(ledger, _ranked("BTC/USDT"), now=datetime(2026, 6, 26, tzinfo=UTC))
        fed = tmp_path / "f.jsonl"
        _patch_settings(monkeypatch)
        calls, fake_cycle = _recorder()
        first = await run_momentum_feeder(
            ledger_path=ledger, state_path=tmp_path / "s.json", fed_path=fed, run_cycle=fake_cycle
        )
        second = await run_momentum_feeder(
            ledger_path=ledger, state_path=tmp_path / "s.json", fed_path=fed, run_cycle=fake_cycle
        )
        assert first["fed"] == 1
        assert second["fed"] == 0
        assert second["skipped_already"] == 1

    async def test_max_per_run_caps(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger,
            _ranked("A/USDT", "B/USDT", "C/USDT", "D/USDT"),
            now=datetime(2026, 6, 26, tzinfo=UTC),
        )
        _patch_settings(monkeypatch, max_per_run=2)
        calls, fake_cycle = _recorder()
        res = await run_momentum_feeder(
            ledger_path=ledger,
            state_path=tmp_path / "s.json",
            fed_path=tmp_path / "f.jsonl",
            run_cycle=fake_cycle,
        )
        assert res["fed"] == 2
        assert len(calls) == 2
