"""Outcome-Linking: Loop-Audit + Execution-Audit → decision_id → 0/1."""

from __future__ import annotations

import json
from pathlib import Path

from app.learning.outcome_linking import build_outcome_map_from_audit


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _loop_row(decision_id: str, order_id: str, fill_simulated: bool = True) -> dict:
    return {
        "cycle_id": f"cy_{decision_id}",
        "decision_id": decision_id,
        "order_id": order_id,
        "symbol": "BTC/USDT",
        "status": "completed",
        "fill_simulated": fill_simulated,
    }


def _close_row(order_id: str, pnl: float, event: str = "position_closed") -> dict:
    return {"event": event, "order_id": order_id, "trade_pnl_usd": pnl}


def test_empty_audits_yield_empty_map(tmp_path: Path) -> None:
    out = build_outcome_map_from_audit(
        loop_audit_path=tmp_path / "loop.jsonl",
        exec_audit_path=tmp_path / "exec.jsonl",
    )
    assert out == {}


def test_winning_trade_maps_to_one(tmp_path: Path) -> None:
    loop = tmp_path / "loop.jsonl"
    exec_ = tmp_path / "exec.jsonl"
    _write_jsonl(loop, [_loop_row("dec_001", "ord_A")])
    _write_jsonl(exec_, [_close_row("ord_A", 12.5)])
    out = build_outcome_map_from_audit(loop_audit_path=loop, exec_audit_path=exec_)
    assert out == {"dec_001": 1}


def test_losing_trade_maps_to_zero(tmp_path: Path) -> None:
    loop = tmp_path / "loop.jsonl"
    exec_ = tmp_path / "exec.jsonl"
    _write_jsonl(loop, [_loop_row("dec_002", "ord_B")])
    _write_jsonl(exec_, [_close_row("ord_B", -8.3)])
    out = build_outcome_map_from_audit(loop_audit_path=loop, exec_audit_path=exec_)
    assert out == {"dec_002": 0}


def test_tier_partial_closes_aggregate_to_cumulative_pnl(tmp_path: Path) -> None:
    loop = tmp_path / "loop.jsonl"
    exec_ = tmp_path / "exec.jsonl"
    _write_jsonl(loop, [_loop_row("dec_003", "ord_C")])
    # Drei Tier-Closes: +5, +3, -2 = +6 → 1
    _write_jsonl(
        exec_,
        [
            _close_row("ord_C", 5.0, event="position_partial_closed"),
            _close_row("ord_C", 3.0, event="position_partial_closed"),
            _close_row("ord_C", -2.0, event="position_closed"),
        ],
    )
    out = build_outcome_map_from_audit(loop_audit_path=loop, exec_audit_path=exec_)
    assert out == {"dec_003": 1}


def test_orders_without_decision_id_are_dropped(tmp_path: Path) -> None:
    loop = tmp_path / "loop.jsonl"
    exec_ = tmp_path / "exec.jsonl"
    _write_jsonl(loop, [])  # keine Loop-Cycles
    _write_jsonl(exec_, [_close_row("ord_orphan", 5.0)])
    out = build_outcome_map_from_audit(loop_audit_path=loop, exec_audit_path=exec_)
    assert out == {}


def test_loop_cycles_without_fill_are_ignored(tmp_path: Path) -> None:
    loop = tmp_path / "loop.jsonl"
    exec_ = tmp_path / "exec.jsonl"
    _write_jsonl(loop, [_loop_row("dec_004", "ord_D", fill_simulated=False)])
    _write_jsonl(exec_, [_close_row("ord_D", 5.0)])
    out = build_outcome_map_from_audit(loop_audit_path=loop, exec_audit_path=exec_)
    assert out == {}


def test_zero_pnl_counts_as_loss(tmp_path: Path) -> None:
    loop = tmp_path / "loop.jsonl"
    exec_ = tmp_path / "exec.jsonl"
    _write_jsonl(loop, [_loop_row("dec_005", "ord_E")])
    _write_jsonl(exec_, [_close_row("ord_E", 0.0)])
    out = build_outcome_map_from_audit(loop_audit_path=loop, exec_audit_path=exec_)
    assert out == {"dec_005": 0}


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    loop = tmp_path / "loop.jsonl"
    exec_ = tmp_path / "exec.jsonl"
    _write_jsonl(loop, [_loop_row("dec_006", "ord_F")])
    with exec_.open("w", encoding="utf-8") as fh:
        fh.write("{not-json}\n")
        fh.write(json.dumps(_close_row("ord_F", 4.0)) + "\n")
    out = build_outcome_map_from_audit(loop_audit_path=loop, exec_audit_path=exec_)
    assert out == {"dec_006": 1}


def test_nested_payload_format_supported(tmp_path: Path) -> None:
    """Schema-v2 wrapper: event + payload nested."""
    loop = tmp_path / "loop.jsonl"
    exec_ = tmp_path / "exec.jsonl"
    _write_jsonl(loop, [_loop_row("dec_007", "ord_G")])
    _write_jsonl(
        exec_,
        [
            {
                "schema_version": "v2",
                "event": "position_closed",
                "payload": {"order_id": "ord_G", "trade_pnl_usd": 7.0},
            }
        ],
    )
    out = build_outcome_map_from_audit(loop_audit_path=loop, exec_audit_path=exec_)
    assert out == {"dec_007": 1}
