r"""ARB/USDT 17.–23.09.2026: ein echter Kursanstieg wurde 7868-mal als Phantom abgewiesen.

Befund (read-only gegen Runtime ``ffd2f07a``):

* 14 Nachkaeufe in eine Long-Position (15.09. 21:31Z bis 16.09. 18:16Z),
  38 460 ARB, Durchschnittseinstieg 0,152135 — rund 63 % des Paper-Equity.
* Jeder Nachkauf ueberschrieb das Take-Profit der Position mit dem TP der
  NEUESTEN Order. Das TP war relativ zum neuesten Fill gesetzt, der Phantom-Cap
  prueft aber gegen den DURCHSCHNITTSEINSTIEG: 0,182973 / 0,152135 = +20,27 %
  > 20 %. Jeder TP-Treffer musste abgewiesen werden.
* Der Preis war echt: Binance 0,2396, Coinbase 0,2394, Bybit 0,239.

Drei Korrekturen, je ein Block hier:

1. Das TP einer Position liegt nie jenseits des Caps (live UND im Replay).
2. Bestaetigt ein unabhaengiger zweiter Anbieter den Close-Preis, geht der
   Close trotz Cap durch — mit eigenem Audit-Ereignis, nicht still.
3. Nachkaeufe je Position sind begrenzt.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.execution.audit_replay import replay_paper_audit
from app.execution.close_guard import (
    clamp_take_profit,
    close_corroboration,
    max_entry_fills_per_position,
)
from app.execution.models import PaperFill, PriceEvidence
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.base import BaseMarketDataAdapter
from app.market_data.models import OHLCV, MarketDataPoint, Ticker
from app.market_data.service import FallbackMarketDataAdapter
from app.orchestrator.monitor_prices import collect_monitor_prices

ARB_AVG_ENTRY = 0.15213502622171599
ARB_LAST_TP = 0.1829734510068084


def _engine(tmp_path: Path) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=100_000.0,
        fee_pct=0.1,
        slippage_pct=0.0,
        live_enabled=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


def _events(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "audit.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _buy(
    eng: PaperExecutionEngine,
    symbol: str,
    price: float,
    *,
    key: str,
    tp: float | None = None,
    qty: float = 100.0,
    side: str = "buy",
    position_side: str = "long",
) -> PaperFill | None:
    order = eng.create_order(
        symbol=symbol,
        side=side,
        quantity=qty,
        take_profit=tp,
        idempotency_key=key,
        position_side=position_side,
    )
    return eng.fill_order(order, current_price=price)


def _evidence(
    source: str, price: float, *, by: str = "", by_price: float | None = None
) -> PriceEvidence:
    return PriceEvidence(
        source=source,
        observed_price=price,
        is_stale=False,
        corroborated_by=by,
        corroborating_price=by_price,
    )


# ── 1. TP-Klemme ──────────────────────────────────────────────────────────────


def test_arb_tp_wird_unter_den_cap_geklemmt() -> None:
    clamped = clamp_take_profit(ARB_LAST_TP, ARB_AVG_ENTRY, "long", cap=0.20)
    assert clamped is not None
    assert clamped < ARB_AVG_ENTRY * 1.20
    assert clamped > ARB_AVG_ENTRY * 1.19


def test_tp_innerhalb_des_caps_bleibt_unveraendert() -> None:
    assert clamp_take_profit(0.17, ARB_AVG_ENTRY, "long", cap=0.20) == 0.17
    assert clamp_take_profit(None, ARB_AVG_ENTRY, "long", cap=0.20) is None


def test_short_tp_wird_von_unten_geklemmt() -> None:
    # Short: implied = entry/close - 1. Ein TP bei 0,5 ist +100 % — weit ueber dem Cap.
    clamped = clamp_take_profit(0.5, 1.0, "short", cap=0.20)
    assert clamped is not None
    assert 1.0 / clamped - 1.0 < 0.20
    assert clamp_take_profit(0.9, 1.0, "short", cap=0.20) == 0.9


def test_nachkauf_mit_hoeherem_tp_feuert_und_schliesst(tmp_path: Path) -> None:
    """Die ARB-Form im Kleinen: Einstieg 1,00, Nachkauf 1,10 mit TP 1,32 (≈ +26 % auf Ø)."""
    eng = _engine(tmp_path)
    assert _buy(eng, "ARB/USDT", 1.00, key="a", tp=1.15) is not None
    assert _buy(eng, "ARB/USDT", 1.10, key="b", tp=1.32) is not None
    pos = eng.portfolio.positions["ARB/USDT"]
    assert pos.take_profit is not None
    implied_tp = pos.take_profit / pos.avg_entry_price - 1.0
    assert implied_tp < 0.20

    fills = eng.monitor_positions({"ARB/USDT": pos.take_profit * 1.001})
    assert len(fills) == 1
    assert "ARB/USDT" not in eng.portfolio.positions
    assert not [e for e in _events(tmp_path) if e["event_type"] == "close_price_sanity_rejected"]


def test_erst_eroeffnung_behaelt_ihr_tp(tmp_path: Path) -> None:
    """Freigegeben ist die Klemme nur beim Nachkauf — ein bewusst weites Ziel bleibt."""
    eng = _engine(tmp_path)
    assert _buy(eng, "SOL/USDT", 100.0, key="a", tp=150.0) is not None
    assert eng.portfolio.positions["SOL/USDT"].take_profit == 150.0
    replayed = replay_paper_audit(tmp_path / "audit.jsonl").positions["SOL/USDT"]
    assert replayed.take_profit == 150.0


def test_replay_rekonstruiert_dasselbe_geklemmte_tp(tmp_path: Path) -> None:
    """Rehydrate darf das ungeklemmte TP aus order_created nicht zurueckholen."""
    eng = _engine(tmp_path)
    _buy(eng, "ARB/USDT", 1.00, key="a", tp=1.15)
    _buy(eng, "ARB/USDT", 1.10, key="b", tp=1.32)
    live = eng.portfolio.positions["ARB/USDT"]

    replayed = replay_paper_audit(tmp_path / "audit.jsonl").positions["ARB/USDT"]
    assert replayed.take_profit == pytest.approx(live.take_profit)
    assert replayed.entry_fill_count == live.entry_fill_count == 2


# ── 2. Bestaetigung durch einen zweiten Anbieter ─────────────────────────────


def test_bestaetigter_close_ueber_dem_cap_geht_durch(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _buy(eng, "ARB/USDT", 1.00, key="a", tp=1.19)
    ev = {"ARB/USDT": _evidence("bybit", 1.57, by="binance_futures", by_price=1.571)}
    fills = eng.monitor_positions({"ARB/USDT": 1.57}, {"ARB/USDT": "bybit"}, price_evidence=ev)

    assert len(fills) == 1
    events = _events(tmp_path)
    corroborated = [e for e in events if e["event_type"] == "close_price_sanity_corroborated"]
    assert len(corroborated) == 1
    assert corroborated[0]["corroborated_by"] == "binance_futures"
    assert corroborated[0]["corroborating_price"] == pytest.approx(1.571)
    assert not [e for e in events if e["event_type"] == "close_price_sanity_rejected"]


@pytest.mark.parametrize(
    ("evidence", "grund"),
    [
        (None, "keine Evidenz"),
        (_evidence("bybit", 1.57), "kein Zweitanbieter"),
        (_evidence("bybit", 1.57, by="bybit", by_price=1.57), "derselbe Anbieter"),
        (_evidence("bybit", 1.57, by="mock", by_price=1.57), "synthetische Quote"),
        (_evidence("bybit", 1.57, by="okx", by_price=1.20), "Zweitanbieter widerspricht"),
        (_evidence("bybit", 1.10, by="okx", by_price=1.57), "Beobachtung != Close-Preis"),
    ],
)
def test_ohne_echte_bestaetigung_bleibt_die_abweisung(
    tmp_path: Path, evidence: PriceEvidence | None, grund: str
) -> None:
    eng = _engine(tmp_path)
    _buy(eng, "ARB/USDT", 1.00, key="a", tp=1.19)
    ev = {"ARB/USDT": evidence} if evidence is not None else None
    fills = eng.monitor_positions({"ARB/USDT": 1.57}, {"ARB/USDT": "bybit"}, price_evidence=ev)

    assert fills == [], grund
    assert "ARB/USDT" in eng.portfolio.positions
    kinds = [e["event_type"] for e in _events(tmp_path)]
    assert "close_price_sanity_rejected" in kinds
    assert "close_price_sanity_corroborated" not in kinds


def test_bestaetigung_gilt_auch_fuer_den_tier_pfad(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _buy(eng, "ARB/USDT", 1.00, key="a")
    eng.set_position_tp_tiers("ARB/USDT", [(1.10, 0.5), (1.19, 0.5)])
    ev = {"ARB/USDT": _evidence("bybit", 1.57, by="okx", by_price=1.569)}
    fills = eng.monitor_positions({"ARB/USDT": 1.57}, {"ARB/USDT": "bybit"}, price_evidence=ev)
    assert len(fills) == 2
    assert "ARB/USDT" not in eng.portfolio.positions


def test_quellen_suffix_des_primaeranbieters_wird_ignoriert() -> None:
    ev = _evidence("bybit|x", 1.57, by="bybit", by_price=1.57)
    assert close_corroboration(ev, 1.57) is None


class _Fake(BaseMarketDataAdapter):
    def __init__(self, name: str, price: float | None) -> None:
        self._name = name
        self._price = price

    @property
    def adapter_name(self) -> str:
        return self._name

    async def get_ticker(self, symbol: str) -> Ticker | None:  # pragma: no cover
        return None

    async def get_ohlcv(  # pragma: no cover
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        return []

    async def get_price(self, symbol: str) -> float | None:  # pragma: no cover
        return self._price

    async def get_market_data_point(self, symbol: str) -> MarketDataPoint | None:
        if self._price is None:
            return None
        return MarketDataPoint(
            symbol=symbol,
            timestamp_utc="2026-09-23T10:50:00+00:00",
            price=self._price,
            volume_24h=1.0,
            change_pct_24h=0.0,
            source=self._name,
        )


def test_fallback_adapter_stempelt_den_bestaetigenden_anbieter() -> None:
    adapter = FallbackMarketDataAdapter(
        [_Fake("bybit", 0.2390), _Fake("binance_futures", 0.2396)], disagreement_pct=0.10
    )
    point = asyncio.run(adapter.get_market_data_point("ARB/USDT"))
    assert point is not None and not point.is_stale
    assert point.source == "bybit"
    assert point.corroborated_by == "binance_futures"
    assert point.corroborating_price == pytest.approx(0.2396)


def test_fallback_adapter_ohne_zweitanbieter_stempelt_nichts() -> None:
    adapter = FallbackMarketDataAdapter([_Fake("bybit", 0.2390), _Fake("okx", None)])
    point = asyncio.run(adapter.get_market_data_point("ARB/USDT"))
    assert point is not None
    assert point.corroborated_by == ""
    assert point.corroborating_price is None


@dataclass
class _Point:
    price: float
    source: str
    corroborated_by: str = ""
    corroborating_price: float | None = None
    is_stale: bool = False


class _MD:
    def __init__(self, point: _Point) -> None:
        self._point = point

    async def get_market_data_point(self, symbol: str) -> _Point:
        return self._point


def test_monitor_preise_reichen_die_bestaetigung_durch() -> None:
    md = _MD(_Point(0.239, "bybit", "binance_futures", 0.2396))
    collected = asyncio.run(collect_monitor_prices(md, ["ARB/USDT"]))
    ev = collected.evidence["ARB/USDT"]
    assert ev.corroborated_by == "binance_futures"
    assert ev.corroborating_price == pytest.approx(0.2396)


# ── 3. Nachkauf-Grenze ───────────────────────────────────────────────────────


def test_default_grenze_ist_vier_entry_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAPER_MAX_ENTRY_FILLS_PER_POSITION", raising=False)
    assert max_entry_fills_per_position() == 4


@pytest.mark.parametrize(("raw", "expected"), [("0", None), ("-1", None), ("x", 4), ("6", 6)])
def test_grenze_ist_per_env_einstellbar(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int | None
) -> None:
    monkeypatch.setenv("PAPER_MAX_ENTRY_FILLS_PER_POSITION", raw)
    assert max_entry_fills_per_position() == expected


def test_nachkauf_ueber_der_grenze_wird_abgewiesen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPER_MAX_ENTRY_FILLS_PER_POSITION", "3")
    eng = _engine(tmp_path)
    for i in range(3):
        assert _buy(eng, "ARB/USDT", 1.0 + i * 0.01, key=f"k{i}") is not None
    assert _buy(eng, "ARB/USDT", 1.05, key="k3") is None

    pos = eng.portfolio.positions["ARB/USDT"]
    assert pos.entry_fill_count == 3
    assert pos.quantity == pytest.approx(300.0)
    rejected = [e for e in _events(tmp_path) if e["event_type"] == "order_rejected_pyramid_limit"]
    assert len(rejected) == 1
    assert rejected[0]["entry_fill_count"] == 3
    assert rejected[0]["max_entry_fills"] == 3


def test_schliessen_bleibt_trotz_grenze_moeglich(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPER_MAX_ENTRY_FILLS_PER_POSITION", "1")
    eng = _engine(tmp_path)
    _buy(eng, "ARB/USDT", 1.0, key="a")
    assert eng.close_position("ARB/USDT", 1.05, reason="manual") is not None


def test_grenze_aus_laesst_beliebig_nachkaufen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPER_MAX_ENTRY_FILLS_PER_POSITION", "0")
    eng = _engine(tmp_path)
    for i in range(6):
        assert _buy(eng, "ARB/USDT", 1.0, key=f"k{i}") is not None


def test_zaehler_ueberlebt_teilclose_und_adjust(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _buy(eng, "ARB/USDT", 1.0, key="a")
    _buy(eng, "ARB/USDT", 1.0, key="b")
    sell = eng.create_order(symbol="ARB/USDT", side="sell", quantity=50.0, idempotency_key="s")
    assert eng.fill_order(sell, current_price=1.01) is not None
    assert eng.adjust_position("ARB/USDT", stop_loss=0.9)
    assert eng.portfolio.positions["ARB/USDT"].entry_fill_count == 2
    replayed = replay_paper_audit(tmp_path / "audit.jsonl").positions["ARB/USDT"]
    assert replayed.entry_fill_count == 2
