"""DS-20260529-V1: portfolio_correction event in replay_paper_audit.

Backs out the MATIC phantom PnL via an explicit, auditable delta rather than
rewriting append-only history.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.execution.audit_replay import replay_paper_audit


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def _open_close_rows() -> list[dict]:
    return [
        {
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-29T10:00:00+00:00",
            "symbol": "ETH/USDT",
            "side": "buy",
            "position_side": "long",
            "quantity": 1.0,
            "fill_price": 2000.0,
            "order_id": "o1",
            "filled_at": "2026-05-29T10:00:00+00:00",
            "portfolio_cash": 8000.0,
            "realized_pnl_usd": 0.0,
        },
        {
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-29T11:00:00+00:00",
            "symbol": "ETH/USDT",
            "side": "sell",
            "position_side": "long",
            "quantity": 1.0,
            "fill_price": 2100.0,
            "order_id": "o2",
            "filled_at": "2026-05-29T11:00:00+00:00",
            "portfolio_cash": 10100.0,
            "realized_pnl_usd": 100.0,
        },
    ]


def test_correction_adjusts_realized_and_cash(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    rows = _open_close_rows()
    rows.append(
        {
            "event_type": "portfolio_correction",
            "timestamp_utc": "2026-05-29T12:00:00+00:00",
            "correction_id": "test1",
            "realized_pnl_delta_usd": -40.0,
            "cash_delta_usd": -40.0,
        }
    )
    _write(path, rows)
    result = replay_paper_audit(path)
    assert result.available
    assert result.realized_pnl_usd == 60.0
    assert result.cash_usd == 10060.0
    assert result.positions == {}


def test_two_corrections_sum(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    rows = _open_close_rows()
    rows.append(
        {
            "event_type": "portfolio_correction",
            "timestamp_utc": "2026-05-29T12:00:00+00:00",
            "realized_pnl_delta_usd": -40.0,
            "cash_delta_usd": -40.0,
        }
    )
    rows.append(
        {
            "event_type": "portfolio_correction",
            "timestamp_utc": "2026-05-29T12:05:00+00:00",
            "realized_pnl_delta_usd": -10.0,
            "cash_delta_usd": -5.0,
        }
    )
    _write(path, rows)
    result = replay_paper_audit(path)
    assert result.realized_pnl_usd == 50.0
    assert result.cash_usd == 10055.0


def test_later_fill_overwrites_snapshot_no_double_count(tmp_path: Path) -> None:
    # A real fill after the correction carries the engine's already-corrected
    # cumulative — replay must use that snapshot, not re-add the delta.
    path = tmp_path / "audit.jsonl"
    rows = _open_close_rows()
    rows.append(
        {
            "event_type": "portfolio_correction",
            "timestamp_utc": "2026-05-29T12:00:00+00:00",
            "realized_pnl_delta_usd": -40.0,
            "cash_delta_usd": -40.0,
        }
    )
    # Engine, post-correction (realized=60), opens+closes another trade for +25.
    rows.append(
        {
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-29T13:00:00+00:00",
            "symbol": "SOL/USDT",
            "side": "buy",
            "position_side": "long",
            "quantity": 2.0,
            "fill_price": 100.0,
            "order_id": "o3",
            "filled_at": "2026-05-29T13:00:00+00:00",
            "portfolio_cash": 9860.0,
            "realized_pnl_usd": 60.0,
        },
    )
    rows.append(
        {
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-29T14:00:00+00:00",
            "symbol": "SOL/USDT",
            "side": "sell",
            "position_side": "long",
            "quantity": 2.0,
            "fill_price": 112.5,
            "order_id": "o4",
            "filled_at": "2026-05-29T14:00:00+00:00",
            "portfolio_cash": 10085.0,
            "realized_pnl_usd": 85.0,
        }
    )
    _write(path, rows)
    result = replay_paper_audit(path)
    # 60 (corrected) + 25 (new trade) = 85, taken straight from the last snapshot.
    assert result.realized_pnl_usd == 85.0
    assert result.cash_usd == 10085.0


def test_correction_missing_deltas_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    rows = _open_close_rows()
    rows.append(
        {
            "event_type": "portfolio_correction",
            "timestamp_utc": "2026-05-29T12:00:00+00:00",
        }
    )
    _write(path, rows)
    result = replay_paper_audit(path)
    assert result.realized_pnl_usd == 100.0
    assert result.cash_usd == 10100.0
