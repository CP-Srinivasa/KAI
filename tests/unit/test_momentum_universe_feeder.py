"""Tests for momentum_universe_feeder (G2) — feed universe → PAPER, cohort-tagged.

Gated (default-off), capped, and gated by the G1 rotation FSM: symbols the FSM
flagged/archived are NOT fed (closes the G1→G2 loop). Every fed cycle is tagged
analysis_source="momentum_universe" so edge-report can isolate the cohort. The
trading loop is injected so tests never touch the real pipeline.

Throughput (2026-06-29): dedup is OPEN-POSITION-AWARE, not once-per-snapshot. A
symbol is skipped only while it has an open position (re-feeding a held symbol
would AVERAGE INTO it, not create a new closeable trade); a cap-rejected symbol
(never opened) is RE-FED on the next tick so the hourly timer keeps competing for
freed slots.
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
        )
        assert res == {"enabled": False}

    async def test_no_universe(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _patch_settings(monkeypatch)
        res = await run_momentum_feeder(
            ledger_path=tmp_path / "missing.jsonl",
            state_path=tmp_path / "s.json",
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
        calls, fake_cycle = _recorder()
        _patch_settings(monkeypatch)

        res = await run_momentum_feeder(
            ledger_path=ledger,
            state_path=state,
            run_cycle=fake_cycle,
            open_symbols_loader=lambda _p: set(),
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
            run_cycle=fake_cycle,
            open_symbols_loader=lambda _p: set(),
        )
        assert res["fed"] == 0
        assert res["skipped_flagged"] == 1
        assert calls == []

    async def test_skips_symbol_with_open_position(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A symbol already holding an open position is skipped — re-feeding it
        would average into the position instead of creating a new closeable trade."""
        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger, _ranked("BTC/USDT", "ETH/USDT"), now=datetime(2026, 6, 26, tzinfo=UTC)
        )
        calls, fake_cycle = _recorder()
        _patch_settings(monkeypatch)
        res = await run_momentum_feeder(
            ledger_path=ledger,
            state_path=tmp_path / "s.json",
            run_cycle=fake_cycle,
            open_symbols_loader=lambda _p: {"BTC/USDT"},  # BTC already held
        )
        assert [c["symbol"] for c in calls] == ["ETH/USDT"]
        assert res["fed"] == 1
        assert res["skipped_open"] == 1

    async def test_refeeds_cap_rejected_symbol_across_runs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The throughput fix: a symbol that never opened (e.g. cap-rejected) has
        no open position, so it is RE-FED on the next tick — NOT permanently
        skipped like the old once-per-snapshot dedup. This is what lets the hourly
        timer keep competing for freed slots."""
        ledger = tmp_path / "u.jsonl"
        append_snapshot(ledger, _ranked("BTC/USDT"), now=datetime(2026, 6, 26, tzinfo=UTC))
        _patch_settings(monkeypatch)
        calls, fake_cycle = _recorder()
        # Cap-rejected → never opens → stays absent from open positions across runs.
        first = await run_momentum_feeder(
            ledger_path=ledger,
            state_path=tmp_path / "s.json",
            run_cycle=fake_cycle,
            open_symbols_loader=lambda _p: set(),
        )
        second = await run_momentum_feeder(
            ledger_path=ledger,
            state_path=tmp_path / "s.json",
            run_cycle=fake_cycle,
            open_symbols_loader=lambda _p: set(),
        )
        assert first["fed"] == 1
        assert second["fed"] == 1  # re-fed, not skipped_already
        assert [c["symbol"] for c in calls] == ["BTC/USDT", "BTC/USDT"]

    async def test_refed_after_position_closes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Open → skipped; closed → re-fed. The symbol re-enters the running once
        it frees its slot."""
        ledger = tmp_path / "u.jsonl"
        append_snapshot(ledger, _ranked("BTC/USDT"), now=datetime(2026, 6, 26, tzinfo=UTC))
        _patch_settings(monkeypatch)
        calls, fake_cycle = _recorder()
        held: set[str] = {"BTC/USDT"}  # currently open

        def loader(_p: Path) -> set[str]:
            return set(held)

        while_open = await run_momentum_feeder(
            ledger_path=ledger,
            state_path=tmp_path / "s.json",
            run_cycle=fake_cycle,
            open_symbols_loader=loader,
        )
        held.clear()  # position closed → slot freed
        after_close = await run_momentum_feeder(
            ledger_path=ledger,
            state_path=tmp_path / "s.json",
            run_cycle=fake_cycle,
            open_symbols_loader=loader,
        )
        assert while_open["fed"] == 0
        assert while_open["skipped_open"] == 1
        assert after_close["fed"] == 1

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
            run_cycle=fake_cycle,
            open_symbols_loader=lambda _p: set(),
        )
        assert res["fed"] == 2
        assert len(calls) == 2
