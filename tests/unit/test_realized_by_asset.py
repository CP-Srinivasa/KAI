"""Tests für app.execution.portfolio_read.compute_realized_by_asset.

Forensik 2026-05-25: widerlegt die Annahme "Vor Live-Mode keine sinnvolle
Visualisierung". Realized-by-asset ist Paper-only ableitbar aus
position_closed + position_partial_closed Events ohne Backtest-Endpoint
und ohne Exchange-API.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.execution.portfolio_read import compute_realized_by_asset


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _closed(symbol: str, trade_pnl: float, *, ts: str, fee: float = 0.0) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": ts,
        "symbol": symbol,
        "quantity": 1.0,
        "trade_pnl_usd": trade_pnl,
        "fee_usd": fee,
    }


def _partial(symbol: str, trade_pnl: float, *, ts: str, fee: float = 0.0) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "position_partial_closed",
        "timestamp_utc": ts,
        "symbol": symbol,
        "quantity": 0.5,
        "trade_pnl_usd": trade_pnl,
        "fee_usd": fee,
    }


def _filled(symbol: str, ts: str) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": ts,
        "symbol": symbol,
        "side": "buy",
        "quantity": 1.0,
        "fill_price": 100.0,
    }


def test_missing_file_returns_unavailable(tmp_path):
    r = compute_realized_by_asset(tmp_path / "no.jsonl")
    assert r["available"] is False
    assert r["error"] == "audit_file_missing"
    assert r["by_asset"] == []
    assert r["totals"]["closed_trades"] == 0


def test_empty_file_returns_available_empty(tmp_path):
    audit = tmp_path / "empty.jsonl"
    audit.write_text("", encoding="utf-8")
    r = compute_realized_by_asset(audit)
    assert r["available"] is True
    assert r["error"] is None
    assert r["by_asset"] == []
    assert r["totals"]["closed_trades"] == 0
    assert r["top_performer"] is None
    assert r["worst_performer"] is None


def test_basic_aggregation(tmp_path):
    audit = tmp_path / "audit.jsonl"
    _write_jsonl(audit, [
        _filled("BTC/USDT", "2026-05-01T10:00:00+00:00"),
        _closed("BTC/USDT", 100.0, ts="2026-05-01T11:00:00+00:00", fee=1.0),
        _filled("ETH/USDT", "2026-05-02T10:00:00+00:00"),
        _closed("ETH/USDT", -50.0, ts="2026-05-02T11:00:00+00:00", fee=0.5),
        _filled("BTC/USDT", "2026-05-03T10:00:00+00:00"),
        _closed("BTC/USDT", 200.0, ts="2026-05-03T11:00:00+00:00", fee=2.0),
    ])
    r = compute_realized_by_asset(audit)
    assert r["available"] is True
    assert r["totals"]["closed_trades"] == 3
    assert r["totals"]["assets_count"] == 2
    assert r["totals"]["realized_pnl_usd"] == 250.0
    assert r["totals"]["fees_usd_total"] == 3.5
    by_asset = {b["symbol"]: b for b in r["by_asset"]}
    assert by_asset["BTC/USDT"]["realized_pnl_usd"] == 300.0
    assert by_asset["BTC/USDT"]["closed_trades"] == 2
    assert by_asset["BTC/USDT"]["wins"] == 2
    assert by_asset["BTC/USDT"]["losses"] == 0
    assert by_asset["BTC/USDT"]["win_rate_pct"] == 100.0
    assert by_asset["ETH/USDT"]["realized_pnl_usd"] == -50.0
    assert by_asset["ETH/USDT"]["losses"] == 1
    assert by_asset["ETH/USDT"]["win_rate_pct"] == 0.0
    assert r["top_performer"]["symbol"] == "BTC/USDT"
    assert r["worst_performer"]["symbol"] == "ETH/USDT"


def test_partial_closes_are_counted(tmp_path):
    """Codex-Befund 2026-05-25: position_partial_closed darf nicht ignoriert
    werden (Pi hatte 24 partials vs 15 fulls; quality-Endpoint zeigte $759
    statt $2486)."""
    audit = tmp_path / "audit.jsonl"
    _write_jsonl(audit, [
        _filled("BTC/USDT", "2026-05-01T10:00:00+00:00"),
        _partial("BTC/USDT", 50.0, ts="2026-05-01T11:00:00+00:00"),
        _partial("BTC/USDT", 80.0, ts="2026-05-01T12:00:00+00:00"),
        _closed("BTC/USDT", 120.0, ts="2026-05-01T13:00:00+00:00"),
    ])
    r = compute_realized_by_asset(audit)
    by_btc = next(b for b in r["by_asset"] if b["symbol"] == "BTC/USDT")
    assert by_btc["realized_pnl_usd"] == 250.0
    assert by_btc["partial_closes"] == 2
    assert by_btc["full_closes"] == 1
    assert by_btc["closed_trades"] == 3
    assert r["totals"]["partial_close_events"] == 2
    assert r["totals"]["full_close_events"] == 1


def test_legacy_v1_lines_use_entry_exit_quantity(tmp_path):
    """v1-Zeilen ohne trade_pnl_usd: reconstruct aus entry/exit/quantity."""
    audit = tmp_path / "audit.jsonl"
    audit.write_text(json.dumps({
        "event_type": "position_closed",
        "timestamp_utc": "2026-04-01T10:00:00+00:00",
        "symbol": "SOL/USDT",
        "entry_price": 50.0,
        "exit_price": 60.0,
        "quantity": 10.0,
        # no trade_pnl_usd
    }) + "\n", encoding="utf-8")
    r = compute_realized_by_asset(audit)
    assert r["available"] is True
    assert r["totals"]["realized_pnl_usd"] == 100.0  # (60-50)*10


def test_invalid_lines_logged_but_not_fatal(tmp_path):
    audit = tmp_path / "audit.jsonl"
    bad_pnl = json.dumps({
        "event_type": "position_closed",
        "symbol": "BTC/USDT",
        "trade_pnl_usd": "not-a-number",
    })
    no_symbol = json.dumps({"event_type": "position_closed", "trade_pnl_usd": 10.0})
    valid = json.dumps(_closed("ETH/USDT", 42.0, ts="2026-05-01T10:00:00+00:00"))
    audit.write_text(
        "not-json-at-all\n" + bad_pnl + "\n" + no_symbol + "\n" + valid + "\n",
        encoding="utf-8",
    )
    r = compute_realized_by_asset(audit)
    assert r["available"] is True
    assert r["totals"]["realized_pnl_usd"] == 42.0
    assert len(r["invalid_lines"]) >= 3


def test_stress_10k_close_events_sub_second(tmp_path):
    """Stress: 10k position_closed events in einer JSONL parsen.

    Akzeptanz: läuft in <2 Sekunden auf realistischer Hardware durch.
    """
    import time
    audit = tmp_path / "stress.jsonl"
    rows = []
    for i in range(10_000):
        sym = f"COIN{i % 50}/USDT"
        rows.append(_closed(sym, (i % 7 - 3) * 10.0, ts=f"2026-04-01T10:00:{i % 60:02d}+00:00"))
    _write_jsonl(audit, rows)
    t0 = time.time()
    r = compute_realized_by_asset(audit)
    elapsed = time.time() - t0
    assert r["available"] is True
    assert r["totals"]["closed_trades"] == 10_000
    assert r["totals"]["assets_count"] == 50
    assert elapsed < 2.0, f"compute_realized_by_asset too slow: {elapsed:.2f}s for 10k rows"


def test_top_worst_performer_assignment(tmp_path):
    audit = tmp_path / "audit.jsonl"
    _write_jsonl(audit, [
        _closed("A/USDT", 100.0, ts="2026-05-01T10:00:00+00:00"),
        _closed("B/USDT", -200.0, ts="2026-05-01T11:00:00+00:00"),
        _closed("C/USDT", 50.0, ts="2026-05-01T12:00:00+00:00"),
        _closed("D/USDT", 300.0, ts="2026-05-01T13:00:00+00:00"),
    ])
    r = compute_realized_by_asset(audit)
    assert r["top_performer"]["symbol"] == "D/USDT"
    assert r["top_performer"]["realized_pnl_usd"] == 300.0
    assert r["worst_performer"]["symbol"] == "B/USDT"
    assert r["worst_performer"]["realized_pnl_usd"] == -200.0


def test_audit_last_event_utc_is_maximum(tmp_path):
    audit = tmp_path / "audit.jsonl"
    _write_jsonl(audit, [
        _closed("A/USDT", 10.0, ts="2026-05-01T10:00:00+00:00"),
        _filled("B/USDT", "2026-05-10T15:00:00+00:00"),
        _closed("A/USDT", 20.0, ts="2026-05-02T10:00:00+00:00"),
    ])
    r = compute_realized_by_asset(audit)
    # last event ts overall (any event type, including order_filled)
    assert r["audit_last_event_utc"] == "2026-05-10T15:00:00+00:00"
