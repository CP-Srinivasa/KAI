"""Tests for the TradingView-alert → shadow-candidate feed (2026-06-22).

Behaviour, not implementation: ticker normalisation (USD/.P → USDT), exotic
rejection (SPCX/dated), long-only-by-default, payload-price preference with a
live fallback, idempotency, and the no-execution contract (writes only to the
shadow ledger).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.observability.tradingview_shadow_feed import feed_tv_shadow, tv_pair


@dataclass
class _Ev:
    event_id: str
    ticker: str
    action: str
    price: float | None = None
    strategy: str | None = None
    received_at: str = "2026-06-22T11:00:00+00:00"


class _Bar:
    def __init__(self, ts: str, close: float) -> None:
        self.timestamp_utc = ts
        self.close = close


class _FakeAdapter:
    def __init__(self, bars: list[_Bar] | None) -> None:
        self._bars = bars
        self.calls = 0

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100):
        self.calls += 1
        return self._bars or []


class _ExplodingAdapter:
    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100):
        raise AssertionError("adapter must not be called when payload carries a price")


def test_tv_pair_normalises_usd_and_perp():
    assert tv_pair("BTCUSD.P") == "BTC/USDT"
    assert tv_pair("XRPUSD") == "XRP/USDT"
    assert tv_pair("BTCUSDT") == "BTC/USDT"
    assert tv_pair("ETHUSD.P") == "ETH/USDT"
    assert tv_pair("BYBIT:SOLUSDT") == "SOL/USDT"


def test_tv_pair_rejects_exotic_and_empty():
    assert tv_pair("SPCXUSD.UMM2031") is None
    assert tv_pair("SOLM2026") is None
    assert tv_pair("") is None
    assert tv_pair(None) is None
    assert tv_pair("JUSTNOQUOTE") is None


@pytest.mark.asyncio
async def test_records_long_with_fallback_price(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    adapter = _FakeAdapter(
        [_Bar("2026-06-22T10:59:00+00:00", 100.0), _Bar("2026-06-22T11:00:00+00:00", 110.0)]
    )
    consumed: set[str] = set()
    summary = await feed_tv_shadow(
        events=[_Ev("e1", "BTCUSD.P", "buy", strategy="hash_ribbon")],
        adapter=adapter,
        consumed_ids=consumed,
        ledger_path=ledger,
    )
    assert summary["recorded"] == 1 and adapter.calls == 1
    rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["source"] == "tradingview_webhook"
    assert rec["candidate_kind"] == "technical"
    assert rec["symbol"] == "BTC/USDT" and rec["side"] == "long"
    assert rec["entry_price"] == 110.0  # last close
    assert rec["score_source"] == "hash_ribbon"
    assert "e1" in consumed


@pytest.mark.asyncio
async def test_uses_payload_price_without_calling_adapter(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    summary = await feed_tv_shadow(
        events=[_Ev("e2", "XRPUSD", "buy", price=0.55)],
        adapter=_ExplodingAdapter(),
        consumed_ids=set(),
        ledger_path=ledger,
    )
    assert summary["recorded"] == 1
    rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["symbol"] == "XRP/USDT" and rec["entry_price"] == 0.55


@pytest.mark.asyncio
async def test_short_skipped_when_disallowed(tmp_path: Path):
    summary = await feed_tv_shadow(
        events=[_Ev("e3", "BTCUSD.P", "sell")],
        adapter=_FakeAdapter([_Bar("t", 100.0)]),
        consumed_ids=set(),
        allow_short=False,
        ledger_path=tmp_path / "l.jsonl",
    )
    assert summary["short_skipped"] == 1 and summary["recorded"] == 0


@pytest.mark.asyncio
async def test_unmappable_ticker_skipped(tmp_path: Path):
    summary = await feed_tv_shadow(
        events=[_Ev("e4", "SPCXUSD.UMM2031", "buy")],
        adapter=_FakeAdapter([_Bar("t", 1.0)]),
        consumed_ids=set(),
        ledger_path=tmp_path / "l.jsonl",
    )
    assert summary["unmappable"] == 1 and summary["recorded"] == 0


@pytest.mark.asyncio
async def test_idempotent_across_runs(tmp_path: Path):
    ledger = tmp_path / "l.jsonl"
    consumed: set[str] = set()
    ev = [_Ev("e5", "ETHUSDT", "buy", price=2000.0)]
    first = await feed_tv_shadow(events=ev, adapter=None, consumed_ids=consumed, ledger_path=ledger)
    second = await feed_tv_shadow(
        events=ev, adapter=None, consumed_ids=consumed, ledger_path=ledger
    )
    assert first["recorded"] == 1
    assert second["recorded"] == 0 and second["already"] == 1


@pytest.mark.asyncio
async def test_no_price_is_not_consumed_for_retry(tmp_path: Path):
    consumed: set[str] = set()
    summary = await feed_tv_shadow(
        events=[_Ev("e6", "BTCUSD.P", "buy")],  # no payload price
        adapter=_FakeAdapter([]),  # adapter yields nothing
        consumed_ids=consumed,
        ledger_path=tmp_path / "l.jsonl",
    )
    assert summary["no_price"] == 1 and summary["recorded"] == 0
    assert "e6" not in consumed  # retryable next tick
