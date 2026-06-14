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
    )
    assert summary["scanned"] == 2  # BAD dropped, run continued


@pytest.mark.asyncio
async def test_disabled_flag_is_hard_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = await run_from_settings(adapter=_FakeAdapter({}))
    # Default settings have the flag OFF → no-op, no fetch, no write.
    assert summary["enabled"] is False
