"""Tests for the TradingView-alert → PAPER-trade envelope feeder (2026-06-22).

Proves the emitted envelopes are bridge-acceptable (so they actually fill) and
the feeder's gating: long-only default, stop/take geometry, payload-vs-fallback
price, unmappable skip, idempotency, no-price-not-consumed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.execution.envelope_to_paper_bridge import _collect_pending_signals, _extract_source
from app.observability.tradingview_paper_feeder import build_envelope, feed_tv_paper


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
    def __init__(self, bars):
        self._bars = bars
        self.calls = 0

    async def get_ohlcv(self, symbol, timeframe="1h", limit=100):
        self.calls += 1
        return self._bars or []


def test_build_envelope_is_bridge_acceptable_and_geometry_correct():
    env = build_envelope(
        event_id="e1",
        pair="BTC/USDT",
        side="buy",
        direction="long",
        entry=100.0,
        ts_utc="2026-06-22T11:00:00+00:00",
        strategy="hash_ribbon",
    )
    # Bridge gate fields
    assert env["message_type"] == "signal" and env["stage"] == "accepted"
    assert env["status"] == "ok" and env["source"] == "tradingview_webhook"
    assert _extract_source(env) == "tradingview_webhook"
    # The bridge recognises it as a pending signal needing a decision.
    pending = _collect_pending_signals([env], {})
    assert len(pending) == 1
    p = env["payload"]
    assert p["symbol"] == "BTCUSDT" and p["side"] == "buy" and p["direction"] == "long"
    assert p["entry_value"] == 100.0
    assert p["stop_loss"] < 100.0 < p["targets"][0]  # long: stop below, target above
    assert p["strategy"] == "hash_ribbon"


def test_build_envelope_short_geometry_inverts():
    env = build_envelope(
        event_id="s1",
        pair="ETH/USDT",
        side="sell",
        direction="short",
        entry=200.0,
        ts_utc="2026-06-22T11:00:00+00:00",
        strategy=None,
    )
    p = env["payload"]
    assert p["targets"][0] < 200.0 < p["stop_loss"]  # short: target below, stop above


@pytest.mark.asyncio
async def test_emits_long_with_fallback_price(tmp_path: Path):
    log = tmp_path / "env.jsonl"
    adapter = _FakeAdapter([_Bar("2026-06-22T11:00:00+00:00", 65000.0)])
    consumed: set[str] = set()
    s = await feed_tv_paper(
        events=[_Ev("e1", "BTCUSD.P", "buy")],
        adapter=adapter,
        consumed_ids=consumed,
        envelope_log=log,
    )
    assert s["emitted"] == 1 and adapter.calls == 1 and "e1" in consumed
    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["payload"]["symbol"] == "BTCUSDT" and rec["payload"]["entry_value"] == 65000.0


@pytest.mark.asyncio
async def test_uses_payload_price(tmp_path: Path):
    log = tmp_path / "env.jsonl"
    s = await feed_tv_paper(
        events=[_Ev("e2", "XRPUSD", "buy", price=0.55)],
        adapter=None,
        consumed_ids=set(),
        envelope_log=log,
    )
    assert s["emitted"] == 1
    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["payload"]["symbol"] == "XRPUSDT" and rec["payload"]["entry_value"] == 0.55


@pytest.mark.asyncio
async def test_short_skipped_unmappable_and_idempotent(tmp_path: Path):
    log = tmp_path / "env.jsonl"
    consumed: set[str] = set()
    s1 = await feed_tv_paper(
        events=[
            _Ev("e3", "BTCUSD.P", "sell"),
            _Ev("e4", "SPCXUSD.UMM2031", "buy"),
            _Ev("e5", "ETHUSDT", "buy", price=2000.0),
        ],
        adapter=None,
        consumed_ids=consumed,
        allow_short=False,
        envelope_log=log,
    )
    assert s1["short_skipped"] == 1 and s1["unmappable"] == 1 and s1["emitted"] == 1
    s2 = await feed_tv_paper(
        events=[_Ev("e5", "ETHUSDT", "buy", price=2000.0)],
        adapter=None,
        consumed_ids=consumed,
        envelope_log=log,
    )
    assert s2["emitted"] == 0 and s2["already"] == 1


@pytest.mark.asyncio
async def test_no_price_not_consumed(tmp_path: Path):
    consumed: set[str] = set()
    s = await feed_tv_paper(
        events=[_Ev("e6", "BTCUSD.P", "buy")],
        adapter=_FakeAdapter([]),
        consumed_ids=consumed,
        envelope_log=tmp_path / "env.jsonl",
    )
    assert s["no_price"] == 1 and s["emitted"] == 0 and "e6" not in consumed
