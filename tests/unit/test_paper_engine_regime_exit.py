"""WP-A (regime-edge-capture 2026-06-15): regime-konditionierter Time-Stop.

Befund (Edge-Attribution n=670): der Richtungs-Edge ist in chop_quiet nach
~300s aufgezehrt/revertiert, breakout_up läuft länger. Der Time-Stop schließt
NUR Positionen, deren Regime-at-Entry ein konfiguriertes Max-Hold hat — und
NUR wenn das Feature explizit aktiviert ist (default-off).
"""

from __future__ import annotations

from pathlib import Path

from app.execution.paper_engine import PaperExecutionEngine


def _engine(tmp_path: Path, regime_max_hold: dict[str, int] | None) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=10_000.0,
        live_enabled=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
        regime_max_hold_seconds=regime_max_hold,
    )


def _open(eng: PaperExecutionEngine, symbol: str, regime: str, price: float = 100.0) -> None:
    order = eng.create_order(
        symbol=symbol,
        side="buy",
        quantity=1.0,
        order_type="market",
        idempotency_key=f"k-{symbol}-{regime}",
        position_side="long",
        regime=regime,
    )
    eng.fill_order(order, current_price=price)


def test_time_stop_closes_configured_regime(tmp_path: Path) -> None:
    # max_hold=0 ⇒ jede chop_quiet-Position ist sofort über dem Limit.
    eng = _engine(tmp_path, {"chop_quiet": 0})
    _open(eng, "AAA/USDT", "chop_quiet")
    assert "AAA/USDT" in eng.portfolio.positions
    # Preis == Entry: weder Stop noch TP feuern → nur der Time-Stop kann schließen.
    eng.monitor_positions({"AAA/USDT": 100.0})
    assert "AAA/USDT" not in eng.portfolio.positions


def test_time_stop_ignores_unconfigured_regime(tmp_path: Path) -> None:
    eng = _engine(tmp_path, {"chop_quiet": 0})
    _open(eng, "BBB/USDT", "breakout_up")
    eng.monitor_positions({"BBB/USDT": 100.0})
    assert "BBB/USDT" in eng.portfolio.positions  # nicht in der Map → kein Time-Stop


def test_time_stop_off_by_default(tmp_path: Path) -> None:
    eng = _engine(tmp_path, None)  # Feature aus
    _open(eng, "CCC/USDT", "chop_quiet")
    eng.monitor_positions({"CCC/USDT": 100.0})
    assert "CCC/USDT" in eng.portfolio.positions  # default-off → kein Time-Stop


def test_time_stop_respects_large_threshold(tmp_path: Path) -> None:
    # Frisch geöffnete Position (~0s alt) bleibt unter einem großen Limit offen.
    eng = _engine(tmp_path, {"chop_quiet": 86_400})
    _open(eng, "DDD/USDT", "chop_quiet")
    eng.monitor_positions({"DDD/USDT": 100.0})
    assert "DDD/USDT" in eng.portfolio.positions
