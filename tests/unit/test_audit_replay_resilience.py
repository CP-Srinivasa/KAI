"""Audit-Replay-Resilience-Tests — Forensik 2026-05-25.

Verifiziert, dass historische Race-Conditions in paper_execution_audit.jsonl
nicht mehr das gesamte Portfolio unsichtbar machen. Vor diesem Sprint hat
ein einziges out-of-order close das Replay mit available=False abgebrochen
und das Dashboard auf $0 gefreezt (MATIC/USDT 2026-05-10 Zeile 75).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.execution.audit_replay import replay_paper_audit


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _order_created(symbol: str, side: str, qty: float, order_id: str) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "order_created",
        "timestamp_utc": "2026-05-10T19:00:00+00:00",
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "order_type": "market",
        "limit_price": None,
        "stop_loss": None,
        "take_profit": None,
        "created_at": "2026-05-10T19:00:00+00:00",
        "idempotency_key": f"idem_{order_id}",
        "status": "pending",
        "risk_check_id": "rck_x",
        "position_side": "long",
        "venue": "paper",
        "correlation_id": "",
        "leverage": None,
        "source": "",
    }


def _order_filled(symbol: str, side: str, qty: float, price: float, order_id: str) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": "2026-05-10T19:00:01+00:00",
        "fill_id": f"fill_{order_id}",
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "fill_price": price,
        "fee_usd": 0.0,
        "filled_at": "2026-05-10T19:00:01+00:00",
        "slippage_pct": 0.0,
        "pnl_usd": 0.0,
        "position_side": "long",
        "fee_venue": "paper",
        "fee_role": "taker",
        "fee_bps_applied": 0.0,
        "fee_table_version": "1.0.0",
        "correlation_id": "",
        "portfolio_cash": 10000.0,
        "realized_pnl_usd": 0.0,
    }


def _position_closed(symbol: str, qty: float, trade_pnl: float) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": "2026-05-10T19:00:02+00:00",
        "symbol": symbol,
        "quantity": qty,
        "trade_pnl_usd": trade_pnl,
        "realized_pnl_usd": trade_pnl,  # cumulative snapshot legacy field
        "portfolio_cash": 10100.0,
    }


def test_replay_continues_after_duplicate_close(tmp_path):
    """Out-of-order close darf nicht das gesamte Portfolio unsichtbar machen.

    Reproduktion des MATIC-Bugs vom 2026-05-10:
      L1 order_created MATIC buy 100
      L2 order_filled MATIC buy 100 @ 1.0
      L3 order_created MATIC sell 100
      L4 order_filled MATIC sell 100 @ 1.05
      L5 position_closed MATIC pnl=5
      L6 order_filled MATIC sell 100 @ 1.05  ← duplicate, no position!
      L7 position_closed MATIC pnl=5         ← second close
      L8 order_created BTC buy 1
      L9 order_filled BTC buy 1 @ 70000

    Erwartung NACH Fix: BTC ist sichtbar als offene Position, available=True,
    skipped_events meldet L6 transparent.
    """
    audit = tmp_path / "audit.jsonl"
    _write_jsonl(
        audit,
        [
            _order_created("MATIC/USDT", "buy", 100.0, "ord_1"),
            _order_filled("MATIC/USDT", "buy", 100.0, 1.0, "ord_1"),
            _order_created("MATIC/USDT", "sell", 100.0, "ord_2"),
            _order_filled("MATIC/USDT", "sell", 100.0, 1.05, "ord_2"),
            _position_closed("MATIC/USDT", 100.0, 5.0),
            _order_filled("MATIC/USDT", "sell", 100.0, 1.05, "ord_3"),  # duplicate
            _position_closed("MATIC/USDT", 100.0, 5.0),  # duplicate
            _order_created("BTC/USDT", "buy", 1.0, "ord_4"),
            _order_filled("BTC/USDT", "buy", 1.0, 70000.0, "ord_4"),
        ],
    )

    r = replay_paper_audit(audit)

    assert r.available is True, f"Replay should not crash. error={r.error}"
    assert r.error is None
    assert "BTC/USDT" in r.positions, "BTC must survive past MATIC duplicate"
    assert "MATIC/USDT" not in r.positions, "MATIC was closed (no remaining qty)"
    assert len(r.skipped_events) >= 1
    # The duplicate sell on closed MATIC should appear in skipped_events.
    skip_reasons = [reason for _, reason in r.skipped_events]
    assert any("audit_close_without_position" in s for s in skip_reasons)


def test_replay_handles_position_side_conflict(tmp_path):
    """Konflikt long-vs-short im gleichen Symbol darf nicht crashen.

    Realer Fall: short-Order auf bestehende long-Position. Vor Fix: fataler
    Abbruch. Nach Fix: skipt die conflict-Zeile, lässt long stehen.
    """
    audit = tmp_path / "audit.jsonl"
    events = [
        _order_created("ETH/USDT", "buy", 1.0, "ord_1"),
        _order_filled("ETH/USDT", "buy", 1.0, 3000.0, "ord_1"),
    ]
    # Force conflict: short open with same symbol
    conflict = _order_filled("ETH/USDT", "sell", 1.0, 3000.0, "ord_2")
    conflict["position_side"] = "short"
    events.append(conflict)
    events.append(_order_created("BTC/USDT", "buy", 1.0, "ord_3"))
    events.append(_order_filled("BTC/USDT", "buy", 1.0, 70000.0, "ord_3"))

    _write_jsonl(audit, events)
    r = replay_paper_audit(audit)

    assert r.available is True
    assert r.error is None
    assert "ETH/USDT" in r.positions  # original long survives
    assert "BTC/USDT" in r.positions
    skip_reasons = [reason for _, reason in r.skipped_events]
    assert any("audit_position_side_conflict" in s for s in skip_reasons)


def test_replay_handles_invalid_fill_payload(tmp_path):
    """Fill mit negative quantity oder fehlendem price wird geskippt, nicht aborted."""
    audit = tmp_path / "audit.jsonl"
    bad_fill = _order_filled("BTC/USDT", "buy", 1.0, 70000.0, "ord_bad")
    bad_fill["quantity"] = -1.0  # invalid

    _write_jsonl(
        audit,
        [
            _order_created("BTC/USDT", "buy", 1.0, "ord_bad"),
            bad_fill,
            _order_created("ETH/USDT", "buy", 1.0, "ord_ok"),
            _order_filled("ETH/USDT", "buy", 1.0, 3000.0, "ord_ok"),
        ],
    )
    r = replay_paper_audit(audit)
    assert r.available is True
    assert "ETH/USDT" in r.positions
    assert "BTC/USDT" not in r.positions  # the bad fill never opened it
    skip_reasons = [reason for _, reason in r.skipped_events]
    assert any("audit_fill_validation_error" in s for s in skip_reasons)


def test_replay_empty_file_returns_available(tmp_path):
    """Leere JSONL-Datei: Replay liefert leeres Portfolio, available=True."""
    audit = tmp_path / "audit.jsonl"
    audit.write_text("", encoding="utf-8")
    r = replay_paper_audit(audit)
    assert r.available is True
    assert r.error is None
    assert r.positions == {}
    assert r.cash_usd == 0.0
    assert r.skipped_events == ()


def test_replay_missing_file_returns_available(tmp_path):
    """Fehlende JSONL-Datei: Replay liefert leeres Portfolio, available=True."""
    r = replay_paper_audit(tmp_path / "does_not_exist.jsonl")
    assert r.available is True
    assert r.error is None
    assert r.positions == {}


def test_replay_stress_many_duplicate_closes(tmp_path):
    """Stress: 500 duplicate closes nach single open dürfen nicht crashen."""
    audit = tmp_path / "audit.jsonl"
    events = [
        _order_created("BTC/USDT", "buy", 10.0, "ord_open"),
        _order_filled("BTC/USDT", "buy", 10.0, 70000.0, "ord_open"),
        _order_created("BTC/USDT", "sell", 10.0, "ord_close"),
        _order_filled("BTC/USDT", "sell", 10.0, 71000.0, "ord_close"),
        _position_closed("BTC/USDT", 10.0, 10000.0),
    ]
    # 500 spurious duplicate closes
    for i in range(500):
        events.append(_order_filled("BTC/USDT", "sell", 10.0, 71000.0, f"dup_{i}"))

    _write_jsonl(audit, events)
    r = replay_paper_audit(audit)
    assert r.available is True
    assert r.error is None
    assert "BTC/USDT" not in r.positions
    assert len(r.skipped_events) == 500
