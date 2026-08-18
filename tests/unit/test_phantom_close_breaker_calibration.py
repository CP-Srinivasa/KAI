r"""Der Phantom-Close-Breaker stand auf 200 % und liess vier Artefakte durch.

Der Breaker (DS-20260529-V1) wurde gegen den MATIC-Vorfall kalibriert: ein
delistetes Instrument buchte **+364 %** je Zyklus. Die Schwelle wurde auf 200 %
gesetzt — knapp unter diesen einen Fall. Alles darunter passierte ungeprueft.

Live gemessen am 2026-08-18 ueber **alle 549** Closes des Audit-Streams:

    Median 1,52 %   p90 4,92 %   p95 7,70 %
    groesster NICHT verdaechtiger Wert:  17,16 %
    ---------------- Luecke ----------------
    naechster Wert:                      21,18 %  (VELVET, autonomous_generator)

Oberhalb von 20 % liegen **18 von 549** Closes, und jeder einzelne ist ein
bekanntes oder vermutetes Artefakt:

    +368…+361 %  MATIC/USDT   2026-05-28  (9x, delistetes BitMEX-Instrument)
     +96,85 %    SOL/USDT     2026-07-08
     -92,11 %    MKR/USDT     2026-07-09  (die -3792 USD aus EINEM Trade)
     +72,11 %    ETH/USDT     2026-08-11  \ byte-identischer Exit 3225.6863500000004
     +71,54 %    ETH/USDT     2026-08-12  /  zusammen +2255,58 USD Scheingewinn
     +55,21 %    ETH/USDT     2026-05-26
     -50,24 %    SOL/USDT     2026-08-12
     +38,82 %    CYS/USDT     2026-08-11
     +28,19 %    SLX/USDT     2026-06-27
     -21,18 %    VELVET/USDT  2026-06-29

Die beiden ETH-Closes drehen das Buch der Epoche von **+396,73** auf
**-1.853,45 USD**. Sie kamen durch, weil +72 % weit unter 200 % liegt.

20 % ist damit nicht geraten, sondern gemessen: rund das 2,6-Fache des p95 und
oberhalb jedes plausiblen Closes im gesamten Bestand.

WICHTIG: der Breaker weist den CLOSE ab und laesst die Position offen. Ein
Fehlalarm kostet also keinen Trade, sondern erzeugt eine offene Position und
ein ``close_price_sanity_rejected``-Ereignis — das ab jetzt auch jemand sieht
(siehe ``test_health_check_watches_rejected_closes``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.paper_engine import (
    _DEFAULT_MAX_CLOSE_RETURN_PCT,
    PaperExecutionEngine,
)


def _engine(tmp_path: Path) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=10000.0,
        fee_pct=0.1,
        slippage_pct=0.0,
        live_enabled=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


def _open_long(eng: PaperExecutionEngine, symbol: str, entry: float, qty: float) -> None:
    order = eng.create_order(
        symbol=symbol, side="buy", quantity=qty, idempotency_key=f"open_{symbol}"
    )
    assert eng.fill_order(order, current_price=entry) is not None


def _events(tmp_path: Path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_schwelle_ist_gemessen_nicht_geraten() -> None:
    """20 % — oberhalb jedes plausiblen Closes (max 17,16 %), unter jedem Artefakt."""
    assert _DEFAULT_MAX_CLOSE_RETURN_PCT == pytest.approx(0.20)


# (symbol, entry, exit, erwartete implizite Rendite in %) — echte Zeilen aus
# artifacts/paper_execution_audit.jsonl auf dem Pi.
_ECHTE_ARTEFAKTE = [
    ("ETH/USDT", 1874.24956227636, 3225.6863500000004, 72.1),
    ("ETH/USDT", 1880.409735, 3225.6863500000004, 71.5),
    ("SOL/USDT", 100.0, 196.85, 96.85),
    ("CYS/USDT", 1.0, 1.3882, 38.82),
]


@pytest.mark.parametrize(("symbol", "entry", "exit_price", "pct"), _ECHTE_ARTEFAKTE)
def test_die_echten_artefakte_werden_jetzt_abgewiesen(
    tmp_path: Path, symbol: str, entry: float, exit_price: float, pct: float
) -> None:
    eng = _engine(tmp_path)
    _open_long(eng, symbol, entry=entry, qty=1.0)
    realized_before = eng.portfolio.realized_pnl_usd

    assert eng.close_position(symbol, current_price=exit_price, reason="take") is None
    assert symbol in eng.portfolio.positions, "Position muss offen bleiben"
    assert eng.portfolio.realized_pnl_usd == realized_before, "kein Scheingewinn gebucht"

    rejected = [e for e in _events(tmp_path) if e["event_type"] == "close_price_sanity_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["implied_return_pct"] == pytest.approx(pct, abs=0.6)


def test_plausibler_gewinner_wird_weiter_gebucht(tmp_path: Path) -> None:
    """17,16 % ist der groesste nicht verdaechtige Close im Bestand — er muss durch."""
    eng = _engine(tmp_path)
    _open_long(eng, "ETH/USDT", entry=1000.0, qty=1.0)
    assert eng.close_position("ETH/USDT", current_price=1171.6, reason="take") is not None
    assert "ETH/USDT" not in eng.portfolio.positions


def test_auch_der_verlust_richtung_greift(tmp_path: Path) -> None:
    """MKR/USDT 2026-07-09 war -92,11 % — ein Vorzeichen darf nicht blind machen."""
    eng = _engine(tmp_path)
    _open_long(eng, "MKR/USDT", entry=1000.0, qty=1.0)
    assert eng.close_position("MKR/USDT", current_price=78.89, reason="stop") is None
    assert "MKR/USDT" in eng.portfolio.positions
