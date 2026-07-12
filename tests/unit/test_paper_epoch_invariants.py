"""Portfolio-Epoche v2 (Weg B+): Pflicht-Invarianten vor dem attestierten Reset.

Operator-Direktive 2026-07-12 (kai_paper_epoch_reset_directive_20260712):
Reset auf 10.000 USD ERST nach Schließung der Accounting-Ursachen. Diese Tests
sind die verbindliche Abnahme dafür:

1.  Cash=0 wird korrekt wiederhergestellt (falsy-zero-Recovery-Falle).
2.  Restart ändert Cash nicht.
3.  Restart dupliziert keine Position.
4.  Long Buy belastet Cash exakt 1×.
5.  Long Sell schreibt Erlös exakt 1× gut.
6.  Short Entry bucht Erlös exakt 1×.
7.  Short Exit belastet Cash exakt 1×.
8.  Fees werden exakt 1× gebucht.
9.  Doppeltes Event-Replay ändert den Zustand nur 1×.
10. Snapshot + Replay reproduzieren denselben Endzustand.

Plus Epoche-v2-Semantik: ``portfolio_epoch_reset`` startet ein frisches Buch
(Positionen leer, Cash = new_starting_cash_usd, Ledger-Modus statt blinder
Snapshot-Übernahme) und das Freeze-/Unverified-Gate blockiert Mutationen
fail-closed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.audit_replay import replay_paper_audit
from app.execution.paper_engine import (
    PaperExecutionEngine,
    PaperMutationBlockedError,
)

INITIAL = 10_000.0
FEE_PCT = 1.0  # 1% legacy-venue fee → deterministic assertions
EPS = 1e-6


def _engine(tmp_path: Path, name: str = "audit.jsonl", **kwargs: object) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=INITIAL,
        fee_pct=FEE_PCT,
        slippage_pct=0.0,
        audit_log_path=str(tmp_path / name),
        **kwargs,  # type: ignore[arg-type]
    )


def _fill(
    engine: PaperExecutionEngine,
    *,
    symbol: str,
    side: str,
    position_side: str,
    quantity: float,
    price: float,
    idem: str,
):
    order = engine.create_order(
        symbol=symbol,
        side=side,
        quantity=quantity,
        position_side=position_side,
        idempotency_key=idem,
        venue="legacy",
    )
    return engine.fill_order(order, price)


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _epoch_row(ts: str = "2026-07-12T18:00:00+00:00", cash: float = INITIAL) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "portfolio_epoch_reset",
        "timestamp_utc": ts,
        "old_epoch_id": "legacy_contaminated",
        "new_epoch_id": "paper_v2_attested",
        "reason": "historical_accounting_contamination",
        "old_book_performance_valid": False,
        "new_starting_cash_usd": cash,
        "operator_approved": True,
    }


def _buy_row(
    *,
    ts: str = "2026-07-12T18:10:00+00:00",
    fill_id: str = "fill_dup_1",
    qty: float = 1.0,
    price: float = 1000.0,
    fee: float = 10.0,
    portfolio_cash: float | None = 8990.0,
    cash_delta: float | None = -1010.0,
) -> dict:
    row = {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": ts,
        "fill_id": fill_id,
        "order_id": "ord_dup_1",
        "symbol": "ETH/USDT",
        "side": "buy",
        "position_side": "long",
        "quantity": qty,
        "fill_price": price,
        "fee_usd": fee,
        "filled_at": ts,
        "pnl_usd": 0.0,
        "realized_pnl_usd": 0.0,
    }
    if portfolio_cash is not None:
        row["portfolio_cash"] = portfolio_cash
    if cash_delta is not None:
        row["cash_delta_usd"] = cash_delta
    return row


# ---------------------------------------------------------------------------
# Invariante 1 — Cash=0 korrekt wiederhergestellt (falsy-zero-Falle)
# ---------------------------------------------------------------------------


def test_cash_zero_is_restored_on_rehydrate(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    _write_rows(
        path,
        [
            {
                "event_type": "order_filled",
                "timestamp_utc": "2026-07-12T10:00:00+00:00",
                "fill_id": "f_zero",
                "order_id": "o_zero",
                "symbol": "BTC/USDT",
                "side": "buy",
                "position_side": "long",
                "quantity": 1.0,
                "fill_price": 9900.0,
                "fee_usd": 100.0,
                "filled_at": "2026-07-12T10:00:00+00:00",
                "portfolio_cash": 0.0,
                "realized_pnl_usd": 0.0,
            }
        ],
    )
    engine = PaperExecutionEngine(
        initial_equity=INITIAL, fee_pct=FEE_PCT, slippage_pct=0.0, audit_log_path=str(path)
    )
    assert engine.rehydrate_from_audit() is True
    assert engine.portfolio.cash == pytest.approx(0.0, abs=EPS)


def test_replay_without_cash_evidence_reports_unobserved(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    _write_rows(
        path,
        [
            {
                "event_type": "order_created",
                "timestamp_utc": "2026-07-12T10:00:00+00:00",
                "order_id": "o1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": 1.0,
            }
        ],
    )
    result = replay_paper_audit(path)
    assert result.available is True
    assert result.cash_observed is False
    # Ohne Cash-Evidenz behält der Engine sein Startkapital.
    engine = PaperExecutionEngine(
        initial_equity=INITIAL, fee_pct=FEE_PCT, slippage_pct=0.0, audit_log_path=str(path)
    )
    assert engine.rehydrate_from_audit() is True
    assert engine.portfolio.cash == pytest.approx(INITIAL, abs=EPS)


# ---------------------------------------------------------------------------
# Invarianten 2+3 — Restart ändert Cash nicht und dupliziert keine Position
# ---------------------------------------------------------------------------


def test_restart_preserves_cash_and_positions(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert _fill(
        engine,
        symbol="BTC/USDT",
        side="buy",
        position_side="long",
        quantity=2.0,
        price=1000.0,
        idem="k1",
    )
    assert _fill(
        engine,
        symbol="BTC/USDT",
        side="sell",
        position_side="long",
        quantity=1.0,
        price=1100.0,
        idem="k2",
    )
    assert _fill(
        engine,
        symbol="SOL/USDT",
        side="sell",
        position_side="short",
        quantity=10.0,
        price=50.0,
        idem="k3",
    )

    cash_before = engine.portfolio.cash
    positions_before = {
        sym: (pos.quantity, pos.avg_entry_price, pos.position_side)
        for sym, pos in engine.portfolio.positions.items()
    }

    restarted = _engine(tmp_path)
    assert restarted.rehydrate_from_audit() is True
    assert restarted.portfolio.cash == pytest.approx(cash_before, abs=1e-6)
    positions_after = {
        sym: (pos.quantity, pos.avg_entry_price, pos.position_side)
        for sym, pos in restarted.portfolio.positions.items()
    }
    assert positions_after == positions_before

    # Zweiter Restart-Zyklus: idempotent, keine Drift, keine Duplikate.
    again = _engine(tmp_path)
    assert again.rehydrate_from_audit() is True
    assert again.portfolio.cash == pytest.approx(cash_before, abs=1e-6)
    assert {
        sym: (pos.quantity, pos.avg_entry_price, pos.position_side)
        for sym, pos in again.portfolio.positions.items()
    } == positions_before


# ---------------------------------------------------------------------------
# Invarianten 4–8 — Notional + Fees je exakt 1×
# ---------------------------------------------------------------------------


def test_long_buy_debits_cash_exactly_once(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    cash_before = engine.portfolio.cash
    fill = _fill(
        engine,
        symbol="BTC/USDT",
        side="buy",
        position_side="long",
        quantity=2.0,
        price=1000.0,
        idem="k1",
    )
    assert fill is not None
    expected_fee = 2000.0 * FEE_PCT / 100.0
    assert fill.fee_usd == pytest.approx(expected_fee, abs=EPS)
    assert cash_before - engine.portfolio.cash == pytest.approx(2000.0 + expected_fee, abs=EPS)


def test_long_sell_credits_proceeds_exactly_once(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _fill(
        engine,
        symbol="BTC/USDT",
        side="buy",
        position_side="long",
        quantity=2.0,
        price=1000.0,
        idem="k1",
    )
    cash_before = engine.portfolio.cash
    realized_before = engine.portfolio.realized_pnl_usd
    fill = _fill(
        engine,
        symbol="BTC/USDT",
        side="sell",
        position_side="long",
        quantity=1.0,
        price=1100.0,
        idem="k2",
    )
    assert fill is not None
    expected_fee = 1100.0 * FEE_PCT / 100.0
    assert engine.portfolio.cash - cash_before == pytest.approx(1100.0 - expected_fee, abs=EPS)
    assert engine.portfolio.realized_pnl_usd - realized_before == pytest.approx(
        (1100.0 - 1000.0) * 1.0 - expected_fee, abs=EPS
    )


def test_short_entry_credits_proceeds_exactly_once(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    cash_before = engine.portfolio.cash
    fill = _fill(
        engine,
        symbol="SOL/USDT",
        side="sell",
        position_side="short",
        quantity=10.0,
        price=50.0,
        idem="k1",
    )
    assert fill is not None
    expected_fee = 500.0 * FEE_PCT / 100.0
    assert engine.portfolio.cash - cash_before == pytest.approx(500.0 - expected_fee, abs=EPS)


def test_short_exit_debits_cash_exactly_once(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _fill(
        engine,
        symbol="SOL/USDT",
        side="sell",
        position_side="short",
        quantity=10.0,
        price=50.0,
        idem="k1",
    )
    cash_before = engine.portfolio.cash
    realized_before = engine.portfolio.realized_pnl_usd
    fill = _fill(
        engine,
        symbol="SOL/USDT",
        side="buy",
        position_side="short",
        quantity=10.0,
        price=40.0,
        idem="k2",
    )
    assert fill is not None
    expected_fee = 400.0 * FEE_PCT / 100.0
    assert cash_before - engine.portfolio.cash == pytest.approx(400.0 + expected_fee, abs=EPS)
    assert engine.portfolio.realized_pnl_usd - realized_before == pytest.approx(
        (50.0 - 40.0) * 10.0 - expected_fee, abs=EPS
    )


def test_fees_are_booked_exactly_once(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    fill1 = _fill(
        engine,
        symbol="BTC/USDT",
        side="buy",
        position_side="long",
        quantity=1.0,
        price=1000.0,
        idem="k1",
    )
    fill2 = _fill(
        engine,
        symbol="BTC/USDT",
        side="sell",
        position_side="long",
        quantity=1.0,
        price=1000.0,
        idem="k2",
    )
    assert fill1 is not None and fill2 is not None
    assert engine.portfolio.total_fees_usd == pytest.approx(fill1.fee_usd + fill2.fee_usd, abs=EPS)
    # Cash-Konsistenz: Start − Endstand == Summe der Fees (Kauf+Verkauf zum
    # selben Preis heben sich auf, nur die Gebühren bleiben).
    assert INITIAL - engine.portfolio.cash == pytest.approx(fill1.fee_usd + fill2.fee_usd, abs=EPS)


def test_fill_events_carry_cash_delta_forensics(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    cash_before = engine.portfolio.cash
    fill = _fill(
        engine,
        symbol="BTC/USDT",
        side="buy",
        position_side="long",
        quantity=1.0,
        price=1000.0,
        idem="k1",
    )
    assert fill is not None
    rows = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fill_rows = [r for r in rows if r.get("event_type") == "order_filled"]
    assert len(fill_rows) == 1
    row = fill_rows[0]
    assert row["cash_before_usd"] == pytest.approx(cash_before, abs=EPS)
    assert row["cash_delta_usd"] == pytest.approx(engine.portfolio.cash - cash_before, abs=EPS)
    assert row["portfolio_cash"] == pytest.approx(engine.portfolio.cash, abs=EPS)


# ---------------------------------------------------------------------------
# Invariante 9 — Doppeltes Event-Replay ändert Zustand nur 1×
# ---------------------------------------------------------------------------


def test_duplicate_fill_row_is_applied_only_once_ledger_mode(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    row = _buy_row()
    _write_rows(path, [_epoch_row(), row, dict(row)])
    result = replay_paper_audit(path)
    assert result.available is True
    assert result.positions["ETH/USDT"].quantity == pytest.approx(1.0, abs=EPS)
    assert result.cash_usd == pytest.approx(INITIAL - 1010.0, abs=EPS)
    assert any("duplicate_fill" in reason for _, reason in result.skipped_events)


def test_duplicate_fill_row_is_applied_only_once_legacy_mode(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    row = _buy_row()
    _write_rows(path, [row, dict(row)])
    result = replay_paper_audit(path)
    assert result.available is True
    assert result.positions["ETH/USDT"].quantity == pytest.approx(1.0, abs=EPS)


# ---------------------------------------------------------------------------
# Invariante 10 — Snapshot + Replay reproduzieren denselben Endzustand
# ---------------------------------------------------------------------------


def test_replay_reproduces_engine_end_state(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _fill(
        engine,
        symbol="BTC/USDT",
        side="buy",
        position_side="long",
        quantity=2.0,
        price=1000.0,
        idem="k1",
    )
    _fill(
        engine,
        symbol="BTC/USDT",
        side="sell",
        position_side="long",
        quantity=1.0,
        price=1100.0,
        idem="k2",
    )
    _fill(
        engine,
        symbol="SOL/USDT",
        side="sell",
        position_side="short",
        quantity=10.0,
        price=50.0,
        idem="k3",
    )

    result = replay_paper_audit(tmp_path / "audit.jsonl")
    assert result.available is True
    assert result.cash_usd == pytest.approx(engine.portfolio.cash, abs=1e-6)
    assert result.realized_pnl_usd == pytest.approx(engine.portfolio.realized_pnl_usd, abs=1e-6)
    assert set(result.positions) == set(engine.portfolio.positions)
    for sym, pos in engine.portfolio.positions.items():
        assert result.positions[sym].quantity == pytest.approx(pos.quantity, abs=1e-9)
        assert result.positions[sym].avg_entry_price == pytest.approx(pos.avg_entry_price, abs=1e-9)
        assert result.positions[sym].position_side == pos.position_side

    # Determinismus: zweiter Replay == erster Replay.
    result2 = replay_paper_audit(tmp_path / "audit.jsonl")
    assert result2.cash_usd == pytest.approx(result.cash_usd, abs=EPS)
    assert {s: p.quantity for s, p in result2.positions.items()} == {
        s: p.quantity for s, p in result.positions.items()
    }


def test_replay_reproduces_engine_end_state_post_epoch(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    _write_rows(path, [_epoch_row()])
    engine = _engine(tmp_path)
    assert engine.rehydrate_from_audit() is True
    assert engine.portfolio.cash == pytest.approx(INITIAL, abs=EPS)
    _fill(
        engine,
        symbol="BTC/USDT",
        side="buy",
        position_side="long",
        quantity=1.0,
        price=2000.0,
        idem="k1",
    )
    _fill(
        engine,
        symbol="SOL/USDT",
        side="sell",
        position_side="short",
        quantity=4.0,
        price=100.0,
        idem="k2",
    )

    result = replay_paper_audit(path)
    assert result.epoch_id == "paper_v2_attested"
    assert result.cash_usd == pytest.approx(engine.portfolio.cash, abs=1e-6)
    assert set(result.positions) == {"BTC/USDT", "SOL/USDT"}
    # Ledger-Modus: kein Discontinuity-Warning, Engine-Snapshots stimmen mit
    # den Ledger-Deltas überein.
    assert not result.integrity_warnings


# ---------------------------------------------------------------------------
# Epoche v2 — portfolio_epoch_reset Semantik
# ---------------------------------------------------------------------------


def test_epoch_reset_clears_positions_and_seeds_cash(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    legacy_fill = _buy_row(
        ts="2026-07-01T00:00:00+00:00", fill_id="f_legacy", portfolio_cash=3333.0, cash_delta=None
    )
    _write_rows(path, [legacy_fill, _epoch_row()])
    result = replay_paper_audit(path)
    assert result.available is True
    assert result.positions == {}
    assert result.cash_usd == pytest.approx(INITIAL, abs=EPS)
    assert result.cash_observed is True
    assert result.realized_pnl_usd == pytest.approx(0.0, abs=EPS)
    assert result.total_fees_usd == pytest.approx(0.0, abs=EPS)
    assert result.epoch_id == "paper_v2_attested"
    assert result.epoch_started_at_utc == "2026-07-12T18:00:00+00:00"

    engine = PaperExecutionEngine(
        initial_equity=INITIAL, fee_pct=FEE_PCT, slippage_pct=0.0, audit_log_path=str(path)
    )
    assert engine.rehydrate_from_audit() is True
    assert engine.portfolio.cash == pytest.approx(INITIAL, abs=EPS)
    assert engine.portfolio.positions == {}


def test_post_epoch_ledger_flags_foreign_cash_snapshot(tmp_path: Path) -> None:
    """Multi-Writer-Schutz: ein stale portfolio_cash-Snapshot darf den
    Ledger-Stand nicht mehr überschreiben — er wird nur als Warnung gemeldet."""
    path = tmp_path / "audit.jsonl"
    row = _buy_row(portfolio_cash=55_555.0)  # absichtlich falscher Snapshot
    _write_rows(path, [_epoch_row(), row])
    result = replay_paper_audit(path)
    assert result.cash_usd == pytest.approx(INITIAL - 1010.0, abs=EPS)
    assert any("cash_chain_discontinuity" in reason for _, reason in result.integrity_warnings)


def test_post_epoch_ledger_derives_delta_when_field_missing(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    row = _buy_row(portfolio_cash=None, cash_delta=None)
    _write_rows(path, [_epoch_row(), row])
    result = replay_paper_audit(path)
    # Abgeleitetes Delta: -(qty*price + fee) = -(1000 + 10)
    assert result.cash_usd == pytest.approx(INITIAL - 1010.0, abs=EPS)


# ---------------------------------------------------------------------------
# Fail-closed: Freeze-Gate + unverifizierter Replay-Zustand
# ---------------------------------------------------------------------------


def test_frozen_engine_refuses_all_mutations(tmp_path: Path) -> None:
    helper = _engine(tmp_path, name="helper.jsonl")
    order = helper.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=1.0,
        position_side="long",
        idempotency_key="k1",
        venue="legacy",
    )

    frozen = _engine(tmp_path, frozen=True)
    with pytest.raises(PaperMutationBlockedError):
        frozen.create_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            position_side="long",
            idempotency_key="k2",
            venue="legacy",
        )
    assert frozen.fill_order(order, 1000.0) is None
    assert frozen.monitor_positions({"BTC/USDT": 1000.0}) == []
    assert frozen.close_position("BTC/USDT", 1000.0) is None
    assert frozen.adjust_position("BTC/USDT", stop_loss=900.0) is False
    assert frozen.set_position_tp_tiers("BTC/USDT", [(1100.0, 0.5)]) is False
    # Frozen-Buch: keinerlei Audit-Zeilen geschrieben.
    assert not (tmp_path / "audit.jsonl").exists()


def test_realized_by_asset_excludes_pre_epoch_closes(tmp_path: Path) -> None:
    from app.execution.portfolio_read import compute_realized_by_asset

    path = tmp_path / "audit.jsonl"
    legacy_close = {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": "2026-07-01T00:00:00+00:00",
        "symbol": "ETH/USDT",
        "reason": "take",
        "quantity": 1.0,
        "entry_price": 2000.0,
        "exit_price": 2100.0,
        "trade_pnl_usd": 100.0,
        "fee_usd": 2.0,
        "position_side": "long",
    }
    new_close = dict(legacy_close)
    new_close["timestamp_utc"] = "2026-07-12T19:00:00+00:00"
    new_close["symbol"] = "BTC/USDT"
    new_close["trade_pnl_usd"] = 42.0
    _write_rows(path, [legacy_close, _epoch_row(), new_close])

    result = compute_realized_by_asset(path)
    assert result["available"] is True
    assert result["epoch_id"] == "paper_v2_attested"
    assert result["pre_epoch_closes_excluded"] == 1
    totals = result["totals"]
    assert isinstance(totals, dict)
    assert totals["closed_trades"] == 1
    assert totals["realized_pnl_usd"] == pytest.approx(42.0, abs=EPS)
    symbols = [b["symbol"] for b in result["by_asset"]]  # type: ignore[index]
    assert symbols == ["BTC/USDT"]


def test_last_epoch_reset_info_returns_latest(tmp_path: Path) -> None:
    from app.execution.audit_replay import last_epoch_reset_info

    path = tmp_path / "audit.jsonl"
    assert last_epoch_reset_info(path) is None
    _write_rows(path, [_buy_row(), _epoch_row(ts="2026-07-12T18:00:00+00:00")])
    assert last_epoch_reset_info(path) == ("paper_v2_attested", "2026-07-12T18:00:00+00:00")


def test_failed_rehydrate_blocks_mutations_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text('{"event_type": "order_filled"\nBROKEN-JSON-LINE\n', encoding="utf-8")

    engine = PaperExecutionEngine(
        initial_equity=INITIAL, fee_pct=FEE_PCT, slippage_pct=0.0, audit_log_path=str(path)
    )
    assert engine.rehydrate_from_audit() is False
    with pytest.raises(PaperMutationBlockedError):
        engine.create_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            position_side="long",
            idempotency_key="k1",
            venue="legacy",
        )
    helper = _engine(tmp_path, name="helper.jsonl")
    order = helper.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=1.0,
        position_side="long",
        idempotency_key="k2",
        venue="legacy",
    )
    assert engine.fill_order(order, 1000.0) is None
