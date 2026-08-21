"""Provenienz-Stufe 2: welcher Snapshot, wann beobachtet, wie alt, welcher Tick.

Stufe 1 (#737) hielt nur fest, WOHER ein Preis kam. Das beweist noch nicht,
welcher Snapshot es war und wie alt er beim Füllen war — genau die Angaben, die
ein Close-Verifier braucht, um ein enges Venue-Zeitfenster zu prüfen statt einer
ganzen Stunde. Dazu der Rohpreis vor Slippage, an dem sich die Rekonstruktion
ohne Rücklaufrechnung prüfen lässt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.models import PriceEvidence
from app.execution.paper_engine import PaperExecutionEngine

EVIDENCE = PriceEvidence(
    source="bybit",
    observed_at_utc="2026-08-20T09:15:00+00:00",
    age_ms=1400.0,
    is_stale=False,
)


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
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("event_type") == event_type:
            rows.append(row)
    return rows


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


def test_close_traegt_snapshot_zeit_und_alter(engine: PaperExecutionEngine) -> None:
    _open_long(engine)
    fills = engine.monitor_positions(
        {"AAA/USDT": 120.0},
        {"AAA/USDT": "bybit"},
        price_evidence={"AAA/USDT": EVIDENCE},
    )
    assert fills

    closed = _events(engine, "position_closed")[-1]
    assert closed["price_source"] == "bybit"
    assert closed["price_observed_at_utc"] == "2026-08-20T09:15:00+00:00"
    # Seit Stage 2.1 trennt der Fill die beiden Groessen: `market_data_age_ms` ist
    # der Abstand Beobachtung -> FUELLEN, der vom Adapter beim Abruf gemeldete
    # Wert steht daneben. Vorher trug ein Feld beide Bedeutungen.
    assert closed["market_data_age_ms_at_collection"] == 1400.0
    assert closed["market_data_age_ms"] > 1400.0


def test_rohpreis_macht_die_slippage_direkt_pruefbar(engine: PaperExecutionEngine) -> None:
    """``fill_price == raw * (1 - slippage)`` ohne Ruecklaufrechnung."""
    _open_long(engine)
    engine.monitor_positions(
        {"AAA/USDT": 120.0},
        {"AAA/USDT": "bybit"},
        price_evidence={"AAA/USDT": EVIDENCE},
    )
    closed = _events(engine, "position_closed")[-1]
    raw = closed["raw_market_price"]
    assert raw == 120.0
    # Sell-Fill: der gebuchte Exit ist der Rohpreis minus Slippage — bit-exakt.
    assert closed["exit_price"] == raw * (1 - 0.0005)


def test_alle_closes_eines_ticks_teilen_eine_id(engine: PaperExecutionEngine) -> None:
    """Die Signatur, die beim Mock-Vorfall ueber Zeitstempel rekonstruiert werden musste."""
    _open_long(engine, "AAA/USDT")
    _open_long(engine, "BBB/USDT")
    fills = engine.monitor_positions(
        {"AAA/USDT": 120.0, "BBB/USDT": 120.0},
        {"AAA/USDT": "bybit", "BBB/USDT": "bybit"},
        price_evidence={"AAA/USDT": EVIDENCE, "BBB/USDT": EVIDENCE},
    )
    assert len(fills) == 2
    tick_ids = {f.monitor_tick_id for f in fills}
    assert len(tick_ids) == 1
    assert next(iter(tick_ids)).startswith("tick_")


def test_zwei_laeufe_tragen_verschiedene_tick_ids(engine: PaperExecutionEngine) -> None:
    _open_long(engine, "AAA/USDT")
    first = engine.monitor_positions(
        {"AAA/USDT": 120.0}, {"AAA/USDT": "bybit"}, price_evidence={"AAA/USDT": EVIDENCE}
    )
    _open_long(engine, "BBB/USDT")
    second = engine.monitor_positions(
        {"BBB/USDT": 120.0}, {"BBB/USDT": "bybit"}, price_evidence={"BBB/USDT": EVIDENCE}
    )
    assert first and second
    assert first[0].monitor_tick_id != second[0].monitor_tick_id


def test_entry_fill_erbt_keine_tick_id(engine: PaperExecutionEngine) -> None:
    """Dieselbe Falle wie beim Tick-Cache der Preisquelle."""
    _open_long(engine, "AAA/USDT")
    engine.monitor_positions(
        {"AAA/USDT": 101.0}, {"AAA/USDT": "bybit"}, price_evidence={"AAA/USDT": EVIDENCE}
    )  # kein Trigger

    order = engine.create_order(
        symbol="CCC/USDT",
        side="buy",
        quantity=1.0,
        stop_loss=90.0,
        take_profit=110.0,
        idempotency_key="open_ccc",
    )
    fill = engine.fill_order(order, current_price=100.0)
    assert fill is not None
    assert fill.monitor_tick_id == ""
    assert fill.price_observed_at_utc == ""
    assert fill.market_data_age_ms is None


def test_fehlende_evidenz_wird_ausgewiesen_nicht_geraten(engine: PaperExecutionEngine) -> None:
    _open_long(engine)
    engine.monitor_positions({"AAA/USDT": 120.0}, {"AAA/USDT": "bybit"})
    closed = _events(engine, "position_closed")[-1]
    assert closed["price_source"] == "bybit"
    assert closed["price_observed_at_utc"] == ""
    assert closed["market_data_age_ms"] is None
    # Der Rohpreis kommt aus dem Engine selbst und ist deshalb IMMER da.
    assert closed["raw_market_price"] == 120.0


def test_evidence_quelle_schlaegt_den_kurzform_parameter(engine: PaperExecutionEngine) -> None:
    """Wenn beide gesetzt sind, gilt die reichere Angabe."""
    _open_long(engine)
    fill = engine.close_position(
        "AAA/USDT",
        105.0,
        reason="manual",
        price_source="alt",
        price_evidence=PriceEvidence(source="okx", observed_at_utc="2026-08-20T10:00:00+00:00"),
    )
    assert fill is not None
    assert fill.price_source == "okx"
    assert fill.price_observed_at_utc == "2026-08-20T10:00:00+00:00"
