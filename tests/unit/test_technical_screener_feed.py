"""WP-D part 2 (2026-06-15): live technical-screener feed (SHADOW-ONLY).

Verifies the feed fetches → screens → eligibility → records shadow candidates,
forces non-BTC selection, stamps gate_would_reject from the technical path, and
is a hard no-op when the flag is OFF. No execution path is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.market_data.models import OHLCV
from app.observability.technical_screener_feed import (
    run_from_settings,
    run_technical_screen,
)
from app.signals.technical_screener import DEFAULT_LOOKBACK

_BASE = 100.0


def _series(total_pct: float) -> list[OHLCV]:
    closes = [_BASE] * DEFAULT_LOOKBACK + [_BASE * (1 + total_pct)]
    return [
        OHLCV(
            symbol="X",
            timestamp_utc=f"{i:04d}",
            timeframe="1h",
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1.0,
        )
        for i, c in enumerate(closes)
    ]


class _FakeAdapter:
    """Returns a per-symbol candle series; empty for unknown symbols."""

    def __init__(self, series_by_symbol: dict[str, list[OHLCV]]) -> None:
        self._series = series_by_symbol

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[OHLCV]:
        return self._series.get(symbol, [])


@pytest.mark.asyncio
async def test_feed_writes_shadow_candidates_and_forces_non_btc(tmp_path: Path) -> None:
    adapter = _FakeAdapter(
        {
            "BTC/USDT": _series(0.01),  # BTC mildly up
            "SOL/USDT": _series(0.06),  # outperforms BTC → strongest
            "ADA/USDT": _series(0.03),
        }
    )
    ledger = tmp_path / "shadow.jsonl"
    summary = await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "SOL/USDT", "ADA/USDT"],
        min_strength=0.1,
        ledger_path=ledger,
        now_utc="2026-06-15T00:00:00+00:00",
        entry_price_fetcher=lambda *_a: None,  # no network; deterministic 1h fallback
    )

    assert summary["enabled"] is True
    assert summary["scanned"] == 3
    assert int(summary["written"]) >= 2  # type: ignore[call-overload]
    assert int(summary["non_btc_signals"]) >= 1  # type: ignore[call-overload]

    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows, "expected shadow rows written"
    assert all(r["source"] == "technical_screener" for r in rows)
    assert all(r["candidate_kind"] == "technical" for r in rows)
    # Strongest (outperforming) alt is recorded and is non-BTC.
    symbols = {r["symbol"] for r in rows}
    assert "SOL/USDT" in symbols
    # gate_would_reject is stamped (technical path eligibility ran).
    assert all("gate_would_reject" in r for r in rows)
    # Bullish technical signals → side long, eligible on the technical path
    # (narrative gates bypassed; tradingview-style low-precision not applied).
    sol = next(r for r in rows if r["symbol"] == "SOL/USDT")
    assert sol["side"] == "long"
    assert sol["gate_would_reject"] is False


@pytest.mark.asyncio
async def test_bearish_technical_still_blocked_by_d142(tmp_path: Path) -> None:
    """WP-B kept D-142 on the technical path; WP-E opens it later."""
    adapter = _FakeAdapter(
        {
            "BTC/USDT": _series(0.05),  # BTC strongly up
            "WEAK/USDT": _series(-0.05),  # falling, underperforms → bearish
        }
    )
    ledger = tmp_path / "shadow.jsonl"
    await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "WEAK/USDT"],
        min_strength=0.1,
        ledger_path=ledger,
        now_utc="2026-06-15T00:00:00+00:00",
        entry_price_fetcher=lambda *_a: None,
    )
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    weak = next((r for r in rows if r["symbol"] == "WEAK/USDT"), None)
    assert weak is not None
    assert weak["side"] == "short"
    assert weak["gate_would_reject"] is True
    assert "bearish_directional_disabled" in weak["gate_reason_codes"]


@pytest.mark.asyncio
async def test_no_write_mode(tmp_path: Path) -> None:
    adapter = _FakeAdapter({"BTC/USDT": _series(0.01), "SOL/USDT": _series(0.06)})
    ledger = tmp_path / "shadow.jsonl"
    summary = await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "SOL/USDT"],
        min_strength=0.1,
        write=False,
        ledger_path=ledger,
        entry_price_fetcher=lambda *_a: None,
    )
    assert int(summary["written"]) == 0  # type: ignore[call-overload]
    assert not ledger.exists()


@pytest.mark.asyncio
async def test_fetch_error_drops_symbol_not_run(tmp_path: Path) -> None:
    class _Boom(_FakeAdapter):
        async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100):
            if symbol == "BAD/USDT":
                raise RuntimeError("boom")
            return self._series.get(symbol, [])

    adapter = _Boom({"BTC/USDT": _series(0.01), "SOL/USDT": _series(0.06)})
    summary = await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "SOL/USDT", "BAD/USDT"],
        min_strength=0.1,
        ledger_path=tmp_path / "s.jsonl",
        now_utc="2026-06-15T00:00:00+00:00",
        entry_price_fetcher=lambda *_a: None,
    )
    assert summary["scanned"] == 2  # BAD dropped, run continued


@pytest.mark.asyncio
async def test_disabled_flag_is_hard_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = await run_from_settings(adapter=_FakeAdapter({}))
    # Default settings have the flag OFF → no-op, no fetch, no write.
    assert summary["enabled"] is False


# --------------------------------------------------------------------------- #
# Decision-time entry price (venue-consistent with the shadow resolver)
# --------------------------------------------------------------------------- #

from datetime import datetime  # noqa: E402

from app.observability.technical_screener_feed import (  # noqa: E402
    resolve_decision_time_entry,
)

_TS = "2026-06-15T00:00:30+00:00"
_TS_MS = int(datetime.fromisoformat(_TS).timestamp() * 1000)
_MIN_OPEN = _TS_MS - (_TS_MS % 60_000)


def test_resolve_entry_uses_binance_bar_covering_decision_minute() -> None:
    # Bar (open_ms, high, low, close) covering the decision minute → its close wins.
    bars = [(_MIN_OPEN, 105.0, 95.0, 101.0)]
    price, basis = resolve_decision_time_entry("SOL/USDT", _TS, 88.0, lambda *_a: bars)
    assert price == 101.0
    assert basis == "binance_1m_decision"


def test_resolve_entry_falls_back_when_no_binance_data() -> None:
    # Empty/None fetch (symbol not on Binance / transient) → provider-open fallback.
    p1, b1 = resolve_decision_time_entry("XAU/USDT", _TS, 88.0, lambda *_a: None)
    p2, b2 = resolve_decision_time_entry("XAU/USDT", _TS, 88.0, lambda *_a: [])
    assert (p1, b1) == (88.0, "fallback_1h_last")
    assert (p2, b2) == (88.0, "fallback_1h_last")


def test_resolve_entry_falls_back_when_no_covering_bar_or_bad_ts() -> None:
    far = [(_MIN_OPEN + 600_000, 105.0, 95.0, 101.0)]  # 10 min later → no cover
    assert resolve_decision_time_entry("SOL/USDT", _TS, 88.0, lambda *_a: far) == (
        88.0,
        "fallback_1h_last",
    )
    assert resolve_decision_time_entry("SOL/USDT", "not-a-ts", 88.0, lambda *_a: far) == (
        88.0,
        "fallback_1h_last",
    )


@pytest.mark.asyncio
async def test_feed_records_binance_decision_price_and_basis(tmp_path: Path) -> None:
    adapter = _FakeAdapter({"BTC/USDT": _series(0.01), "SOL/USDT": _series(0.06)})
    ledger = tmp_path / "shadow.jsonl"
    # Fetcher returns a 1m bar covering the decision minute with a DISTINCT close
    # (4242.0) — different from the 1h fallback close — so we can prove it is used.
    summary = await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "SOL/USDT"],
        min_strength=0.1,
        ledger_path=ledger,
        now_utc=_TS,
        entry_price_fetcher=lambda _s, _a, _b: [(_MIN_OPEN, 4300.0, 4200.0, 4242.0)],
    )
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows
    assert all(r["entry_price"] == 4242.0 for r in rows)
    assert all(r["entry_price_basis"] == "binance_1m_decision" for r in rows)
    assert int(summary["entry_binance_1m"]) == len(rows)  # type: ignore[call-overload]
    assert int(summary["entry_fallback_1h"]) == 0  # type: ignore[call-overload]
