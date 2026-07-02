"""Tests for asset_rotation_shadow — the G1 shadow evaluator (measurement-only).

Maps per-symbol paper stats → verdict → rotation policy, guards FSM legality,
carries the hysteresis state across runs, and writes a shadow log. NOTHING acts
on the decisions (no feed/sizing) — this is the "rotiert nachvollziehbar" Sicht.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.learning.asset_lifecycle import AssetStatus
from app.learning.asset_rotation_shadow import (
    AssetRotationState,
    evaluate_rotations,
    load_state,
    run_rotation_shadow,
    save_state,
)


def _by_symbol(**rows: tuple[float, int, int]) -> dict[str, dict[str, float]]:
    # rows: symbol -> (net_pnl_usd, closes, wins)
    return {
        sym: {
            "count": float(closes),
            "wins": float(wins),
            "losses": float(closes - wins),
            "sum_pnl_usd": net,
        }
        for sym, (net, closes, wins) in rows.items()
    }


class TestEvaluateRotations:
    def test_unknown_symbol_defaults_probation_then_promotes_on_healthy(self) -> None:
        by_symbol = _by_symbol(**{"BTC/USDT": (120.0, 10, 9)})
        decisions, state = evaluate_rotations(by_symbol, {})
        d = decisions[0]
        assert d["from"] == "probation"
        assert d["to"] == "active"
        assert d["changed"] is True
        assert state["BTC/USDT"].status == AssetStatus.ACTIVE

    def test_sustained_weak_flags_via_carried_counter(self) -> None:
        by_symbol = _by_symbol(**{"DOGE/USDT": (-90.0, 12, 3)})
        prior = {"DOGE/USDT": AssetRotationState(AssetStatus.ACTIVE, 1)}
        decisions, state = evaluate_rotations(by_symbol, prior)
        assert state["DOGE/USDT"].status == AssetStatus.ROTATION_FLAGGED
        assert decisions[0]["reason"] == "flag_sustained_weak"

    def test_insufficient_holds(self) -> None:
        by_symbol = _by_symbol(**{"XRP/USDT": (-5.0, 2, 0)})
        prior = {"XRP/USDT": AssetRotationState(AssetStatus.ACTIVE, 0)}
        decisions, state = evaluate_rotations(by_symbol, prior)
        assert decisions[0]["verdict"] == "insufficient"
        assert decisions[0]["changed"] is False
        assert state["XRP/USDT"].status == AssetStatus.ACTIVE

    def test_illegal_target_falls_back_to_prior(self) -> None:
        # ARCHIVED can only go to CANDIDATE; a healthy verdict wants ACTIVE → illegal,
        # so the status must not change (FSM guard), even though the policy proposed it.
        by_symbol = _by_symbol(**{"OLD/USDT": (50.0, 8, 7)})
        prior = {"OLD/USDT": AssetRotationState(AssetStatus.ARCHIVED, 0)}
        decisions, state = evaluate_rotations(by_symbol, prior)
        assert state["OLD/USDT"].status == AssetStatus.ARCHIVED
        assert decisions[0]["changed"] is False


class TestStateIO:
    def test_state_round_trips(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        state = {
            "BTC/USDT": AssetRotationState(AssetStatus.ACTIVE, 0),
            "DOGE/USDT": AssetRotationState(AssetStatus.ROTATION_FLAGGED, 2),
        }
        save_state(p, state)
        loaded = load_state(p)
        assert loaded["BTC/USDT"].status == AssetStatus.ACTIVE
        assert loaded["DOGE/USDT"].status == AssetStatus.ROTATION_FLAGGED
        assert loaded["DOGE/USDT"].flagged_runs == 2

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        assert load_state(tmp_path / "nope.json") == {}


class TestRunRotationShadow:
    def _write_audit(self, path: Path, rows: list[tuple[str, float]]) -> None:
        with path.open("w", encoding="utf-8") as fh:
            for sym, pnl in rows:
                fh.write(
                    json.dumps(
                        {"event_type": "position_closed", "symbol": sym, "trade_pnl_usd": pnl}
                    )
                    + "\n"
                )

    def test_run_writes_shadow_log_and_state(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        # 6 winning BTC closes → healthy → promote from default probation.
        self._write_audit(audit, [("BTC/USDT", 10.0)] * 6)
        state_path = tmp_path / "state.json"
        shadow_log = tmp_path / "rot_shadow.jsonl"
        record = run_rotation_shadow(
            audit_path=audit,
            state_path=state_path,
            shadow_log_path=shadow_log,
            last_n=50,
            now=datetime(2026, 6, 26, tzinfo=UTC),
        )
        assert record["evaluated"] == 1
        assert shadow_log.exists()
        assert state_path.exists()
        loaded = load_state(state_path)
        assert loaded["BTC/USDT"].status == AssetStatus.ACTIVE
