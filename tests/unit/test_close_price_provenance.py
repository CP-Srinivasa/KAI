r"""Ein abgewiesener Phantom-Preis muss sagen, WOHER er kam.

Befund 2026-08-18. Zwei ETH-Closes am 11./12.08. buchten den byte-identischen
Exit ``3225.6863500000004`` (+72,11 % / +71,54 %), waehrend ETH im Band
1.775-1.973 handelte. Der Rohpreis war ``3227,30`` -- ``3225,68635`` ist genau
``3227,30 x (1 - 0,0005)``, also derselbe Wert nach Slippage.

Die Wurzel blieb ungeklaert, und der Grund dafuer ist strukturell: die Herkunft
des Preises wird auf dem Weg zum Breaker weggeworfen.

    app/orchestrator/trading_loop.py   prices[symbol] = md.price   <- md.source faellt weg
    app/execution/paper_engine.py      close_price_sanity_rejected <- ohne Anbieter

``MarketDataPoint`` FUEHRT ein ``source``-Feld ("mock" | "binance" | ...), und
der Fallback-Adapter kennt seine Kette. Die Information war also da -- sie kam
nur nie dort an, wo der Befund entsteht. Der Operator konnte darum sehen, DASS
ein unmoeglicher Preis kam, nie WOHER.

Der Cross-Provider-Check in ``FallbackMarketDataAdapter.get_market_data_point``
sagt selbst, wo seine Grenze liegt: *"Single-provider symbols can't be
cross-checked and are returned best-effort, unchanged."* Faellt ein Anbieter
kurzzeitig aus, ist auch ein Major wie ETH einen Tick lang Single-Provider --
und genau dann traegt nur noch der Breaker, dessen Befund bisher anonym blieb.

Dieser Test haelt fest: der naechste Vorfall benennt den Anbieter.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.execution.paper_engine import PaperExecutionEngine


def _engine(tmp_path: Path) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=10_000.0,
        fee_pct=0.1,
        slippage_pct=0.0,
        live_enabled=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


def _rejections(tmp_path: Path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("event_type") == "close_price_sanity_rejected":
            out.append(row)
    return out


def _open_eth(engine: PaperExecutionEngine) -> None:
    """Position wie am 11.08.: ETH long, Einstieg ~1874, TP unerreichbar hoch."""
    order = engine.create_order(
        symbol="ETH/USDT",
        side="buy",
        quantity=1.0,
        risk_check_id="rc-test",
        take_profit=3225.0,
    )
    engine.fill_order(order, 1874.25)


def test_rejected_phantom_close_names_the_provider(tmp_path: Path) -> None:
    """Der Realfall: 3227,30 kommt herein, wird abgewiesen -- mit Anbieter."""
    engine = _engine(tmp_path)
    _open_eth(engine)

    engine.monitor_positions({"ETH/USDT": 3227.30}, {"ETH/USDT": "bybit"})

    rejected = _rejections(tmp_path)
    assert rejected, "Phantom-Preis wurde nicht abgewiesen"
    assert rejected[-1]["price_source"] == "bybit"
    assert rejected[-1]["symbol"] == "ETH/USDT"
    # Die Position bleibt offen -- der Breaker weist den CLOSE ab, nicht den Trade.
    assert "ETH/USDT" in engine.portfolio.positions


def test_missing_provenance_is_named_unknown_not_dropped(tmp_path: Path) -> None:
    """Alte Aufrufer geben keine Quelle mit. Dann steht das ausdruecklich da,
    statt dass das Feld fehlt -- ein fehlendes Feld liest sich wie ein
    Schema-Fehler, 'unknown' liest sich als das, was es ist."""
    engine = _engine(tmp_path)
    _open_eth(engine)

    engine.monitor_positions({"ETH/USDT": 3227.30})

    rejected = _rejections(tmp_path)
    assert rejected
    assert rejected[-1]["price_source"] == "unknown"


def test_provenance_does_not_leak_between_ticks(tmp_path: Path) -> None:
    """Der zweite Tick darf nicht die Quelle des ersten erben."""
    engine = _engine(tmp_path)
    _open_eth(engine)

    engine.monitor_positions({"ETH/USDT": 1875.0}, {"ETH/USDT": "coingecko"})
    engine.monitor_positions({"ETH/USDT": 3227.30}, {})

    rejected = _rejections(tmp_path)
    assert rejected
    assert rejected[-1]["price_source"] == "unknown"


def test_plausible_price_still_closes_normally(tmp_path: Path) -> None:
    """Gegenprobe: die Provenienz-Erfassung aendert nichts am normalen Ablauf."""
    engine = _engine(tmp_path)
    order = engine.create_order(
        symbol="ETH/USDT",
        side="buy",
        quantity=1.0,
        risk_check_id="rc-test",
        take_profit=1900.0,
    )
    engine.fill_order(order, 1874.25)

    fills = engine.monitor_positions({"ETH/USDT": 1905.0}, {"ETH/USDT": "bybit"})

    assert fills, "legitimer Take-Profit muss weiterhin schliessen"
    assert _rejections(tmp_path) == []
