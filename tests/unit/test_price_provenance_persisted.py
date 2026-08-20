"""Die Herkunft des Preises muss auch im ERFOLGS-Pfad im Audit stehen.

Bis 2026-08-20 trug sie nur `close_price_sanity_rejected` — also ausgerechnet
nicht die Closes, die durchgingen und später forensisch geprüft werden müssen.
Beim Mock-Vorfall vom 11./12.08. ließ sich deshalb nicht *belegen*, dass der
synthetische Fallback gegriffen hatte; es blieb die Rekonstruktion der
Mock-Kurve (DS-20260818-MOCK-EXIT).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.paper_engine import PaperExecutionEngine


@pytest.fixture
def engine(tmp_path: Path) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=10_000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


def _events(engine: PaperExecutionEngine, event_type: str) -> list[dict]:
    path = Path(engine._audit_path)  # noqa: SLF001 - Testzugriff auf den Pfad
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("event_type") == event_type:
            out.append(row)
    return out


def _open_long(engine: PaperExecutionEngine, symbol: str = "AAA/USDT") -> None:
    order = engine.create_order(
        symbol=symbol,
        side="buy",
        quantity=10.0,
        stop_loss=90.0,
        take_profit=110.0,
        idempotency_key=f"open_{symbol}",
    )
    assert engine.fill_order(order, current_price=100.0) is not None


def test_close_ueber_monitor_traegt_die_preisquelle(engine: PaperExecutionEngine) -> None:
    """Der Weg, auf dem der Mock-Vorfall entstand — jetzt nachvollziehbar."""
    _open_long(engine)
    fills = engine.monitor_positions({"AAA/USDT": 120.0}, {"AAA/USDT": "bybit"})
    assert fills, "TP haette ausloesen muessen"

    closed = _events(engine, "position_closed")
    assert closed, "kein position_closed-Event geschrieben"
    assert closed[-1]["price_source"] == "bybit"

    filled = _events(engine, "order_filled")
    assert filled[-1]["price_source"] == "bybit"


def test_synthetische_quelle_ist_im_audit_erkennbar(engine: PaperExecutionEngine) -> None:
    """Genau die Zeile, die am 11./12.08. gefehlt hat."""
    _open_long(engine)
    engine.monitor_positions(
        {"AAA/USDT": 120.0},
        {"AAA/USDT": "mock|synthetic_not_tradeable"},
    )
    closed = _events(engine, "position_closed")
    assert closed[-1]["price_source"] == "mock|synthetic_not_tradeable"


def test_ohne_quellenangabe_bleibt_das_feld_leer(engine: PaperExecutionEngine) -> None:
    """Leer heisst 'nicht erfasst' — nie stillschweigend eine Quelle erfinden."""
    _open_long(engine)
    engine.monitor_positions({"AAA/USDT": 120.0})
    closed = _events(engine, "position_closed")
    assert closed[-1]["price_source"] == ""


def test_entry_fill_erbt_keine_fremde_quelle(engine: PaperExecutionEngine) -> None:
    """Der Tick-Cache darf nicht auf einen spaeteren Entry durchschlagen.

    ``_tick_price_sources`` bleibt nach einem Monitor-Lauf stehen. Wuerde der
    Entry-Pfad daraus lesen, truege er die Quelle eines fremden Symbols aus einem
    fremden Zeitpunkt — schlimmer als ein leeres Feld.
    """
    _open_long(engine)
    engine.monitor_positions({"AAA/USDT": 101.0}, {"AAA/USDT": "bybit"})  # kein Trigger

    order = engine.create_order(
        symbol="BBB/USDT",
        side="buy",
        quantity=1.0,
        stop_loss=90.0,
        take_profit=110.0,
        idempotency_key="open_bbb",
    )
    fill = engine.fill_order(order, current_price=100.0)
    assert fill is not None
    assert fill.price_source == ""


def test_close_position_direkt_nimmt_die_uebergebene_quelle(engine: PaperExecutionEngine) -> None:
    _open_long(engine)
    fill = engine.close_position("AAA/USDT", 105.0, reason="manual", price_source="okx")
    assert fill is not None
    assert fill.price_source == "okx"
    assert _events(engine, "position_closed")[-1]["price_source"] == "okx"
