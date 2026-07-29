"""W2 Quoten-Sprint: eigene Signale in den Outcome-Loop + Binance-Preisquelle.

Befund 2026-07-29: 62 eigene ``technical_paper``-Fills mit **null**
Outcome-Annotationen — die gehandelte und die gemessene Population waren
disjunkt. Zweitens hing die gesamte Outcome-Messung am CoinGecko-Free-Tier
(5-s-Delays, Batch-Caps, 423-Backlog), während die Ausführung gegen Binance
läuft. Teil a: synthetische Pendings aus Paper-Fills (in-memory, KEIN Schreiben
nach alert_audit.jsonl). Teil b: Binance-OHLCV als primäre Preisquelle,
CoinGecko nur noch Fallback; das API-Delay bleibt ausschließlich im
CoinGecko-Pfad.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.alerts.audit import (
    ALERT_AUDIT_JSONL_FILENAME,
    ALERT_OUTCOMES_JSONL_FILENAME,
    AlertAuditRecord,
    append_alert_audit,
)
from app.alerts.auto_annotator import auto_annotate_pending
from app.market_data.binance_adapter import BinanceAdapter
from app.market_data.models import OHLCV

_PAPER_AUDIT = "paper_execution_audit.jsonl"


def _write_fill(
    tmp_path: Path,
    doc_id: str,
    *,
    hours_ago: float = 30.0,
    symbol: str = "ADA/USDT",
    position_side: str = "long",
) -> None:
    side = "buy" if position_side == "long" else "sell"
    row = {
        "event_type": "order_filled",
        "document_id": doc_id,
        "symbol": symbol,
        "side": side,
        "position_side": position_side,
        "timestamp_utc": (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(),
    }
    with (tmp_path / _PAPER_AUDIT).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _cg_patch(pct: float, start: float = 65000.0, end: float = 65100.0):
    ctx = patch("app.alerts.auto_annotator.CoinGeckoAdapter")
    mock_cls = ctx.__enter__()
    adapter = mock_cls.return_value
    adapter.get_ticker = AsyncMock(return_value=None)
    adapter.get_price_change_between = AsyncMock(return_value=(start, end, pct))
    return ctx, adapter


def _last_rows(tmp_path: Path) -> list[dict]:
    p = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── Teil a: eigene Signale werden gemessen ─────────────────────────────


async def test_technical_fill_is_annotated_with_provenance(tmp_path: Path) -> None:
    """Ein technical_paper-Fill erzeugt eine Outcome-Annotation samt Provenance."""
    _write_fill(tmp_path, "technical_paper_ADAUSDT_tech-x1")

    ctx, _ = _cg_patch(pct=3.1, end=67000.0)
    try:
        results = await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    assert len(results) == 1
    assert results[0].document_id == "technical_paper_ADAUSDT_tech-x1"
    assert results[0].outcome == "hit"
    rows = _last_rows(tmp_path)
    assert rows[-1]["provenance"]["source"] == "technical_paper"


async def test_short_fill_maps_bearish(tmp_path: Path) -> None:
    """position_side=short → bearish; fallender Preis = hit."""
    _write_fill(tmp_path, "technical_paper_INJUSDT_tech-x2", position_side="short")

    ctx, _ = _cg_patch(pct=-3.1, end=63000.0)
    try:
        results = await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    assert len(results) == 1
    assert results[0].outcome == "hit"


async def test_foreign_fills_are_not_synthesized(tmp_path: Path) -> None:
    """SIG-TVP hängt am tv:-Alert, UUIDs an anderen Feeds — keine Doppel-Messung."""
    _write_fill(tmp_path, "SIG-TVP-BTCUSDT-0039647c")
    _write_fill(tmp_path, "84e1f513-20cb-4e36-bc68-f2e467be141d")

    ctx, adapter = _cg_patch(pct=3.1)
    try:
        results = await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    assert results == []
    adapter.get_price_change_between.assert_not_called()


async def test_real_audit_record_wins_over_synthetic(tmp_path: Path) -> None:
    """Existiert ein echter Audit-Record, wird NICHT zusätzlich synthetisiert."""
    doc = "technical_paper_AAVEUSDT_tech-x3"
    ts = datetime.now(UTC) - timedelta(hours=30)
    append_alert_audit(
        AlertAuditRecord(
            document_id=doc,
            channel="telegram",
            message_id="dry_run",
            is_digest=False,
            dispatched_at=ts.isoformat(),
            sentiment_label="bullish",
            affected_assets=["AAVE/USDT"],
            directional_eligible=True,
        ),
        tmp_path / ALERT_AUDIT_JSONL_FILENAME,
    )
    _write_fill(tmp_path, doc)

    ctx, _ = _cg_patch(pct=3.1)
    try:
        results = await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    assert len(results) == 1
    assert len(_last_rows(tmp_path)) == 1


# ── Teil b: Binance als primäre Preisquelle ────────────────────────────


async def test_binance_get_price_change_between() -> None:
    """Klines → (start, end, pct) mit Nearest-Neighbor-Zuordnung."""
    adapter = BinanceAdapter()
    t0 = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)
    candles = [
        OHLCV(
            symbol="ADA/USDT",
            timestamp_utc=(t0 + timedelta(hours=i)).isoformat(),
            timeframe="1h",
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.0 + i,
            volume=1.0,
        )
        for i in range(6)
    ]
    with patch.object(adapter, "get_ohlcv", new=AsyncMock(return_value=candles)):
        out = await adapter.get_price_change_between(
            "ADA/USDT",
            start_utc=t0,
            end_utc=t0 + timedelta(hours=4),
        )

    assert out is not None
    start, end, pct = out
    assert start == 100.0
    assert end == 104.0
    assert pct == pytest.approx(4.0)


async def test_binance_returns_none_outside_gap() -> None:
    """Kein Candle nahe genug am Zeitpunkt → None (fail-closed, kein Raten)."""
    adapter = BinanceAdapter()
    t0 = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)
    far = [
        OHLCV(
            symbol="ADA/USDT",
            timestamp_utc=(t0 - timedelta(hours=12)).isoformat(),
            timeframe="1h",
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=1.0,
        )
    ]
    with patch.object(adapter, "get_ohlcv", new=AsyncMock(return_value=far)):
        out = await adapter.get_price_change_between(
            "ADA/USDT",
            start_utc=t0,
            end_utc=t0 + timedelta(hours=4),
        )

    assert out is None


async def test_binance_primary_no_coingecko_no_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default binance: CoinGecko bleibt unberührt, kein API-Delay."""
    monkeypatch.setenv("ALERTS_OUTCOME_PRICE_SOURCE", "binance")
    _write_fill(tmp_path, "technical_paper_JUPUSDT_tech-x4")

    sleep_mock = AsyncMock()
    with (
        patch("app.alerts.auto_annotator.BinanceAdapter") as bn_cls,
        patch("app.alerts.auto_annotator.CoinGeckoAdapter") as cg_cls,
        patch("asyncio.sleep", new=sleep_mock),
    ):
        bn = bn_cls.return_value
        bn.get_ticker = AsyncMock(return_value=None)
        bn.get_price_change_between = AsyncMock(return_value=(65000.0, 67000.0, 3.1))
        cg = cg_cls.return_value
        cg.get_ticker = AsyncMock(return_value=None)
        cg.get_price_change_between = AsyncMock(return_value=(65000.0, 67000.0, 3.1))

        results = await auto_annotate_pending(tmp_path, min_age_hours=4)

    assert len(results) == 1
    assert results[0].outcome == "hit"
    cg.get_price_change_between.assert_not_called()
    assert sleep_mock.await_count == 0


async def test_binance_none_falls_back_to_coingecko(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Binance liefert nichts → CoinGecko-Fallback greift (mit Delay)."""
    monkeypatch.setenv("ALERTS_OUTCOME_PRICE_SOURCE", "binance")
    _write_fill(tmp_path, "technical_paper_DOTUSDT_tech-x5")

    sleep_mock = AsyncMock()
    with (
        patch("app.alerts.auto_annotator.BinanceAdapter") as bn_cls,
        patch("app.alerts.auto_annotator.CoinGeckoAdapter") as cg_cls,
        patch("asyncio.sleep", new=sleep_mock),
    ):
        bn = bn_cls.return_value
        bn.get_ticker = AsyncMock(return_value=None)
        bn.get_price_change_between = AsyncMock(return_value=None)
        cg = cg_cls.return_value
        cg.get_ticker = AsyncMock(return_value=None)
        cg.get_price_change_between = AsyncMock(return_value=(65000.0, 67000.0, 3.1))

        results = await auto_annotate_pending(tmp_path, min_age_hours=4)

    assert len(results) == 1
    assert results[0].outcome == "hit"
    assert cg.get_price_change_between.await_count >= 1
    assert sleep_mock.await_count >= 1
