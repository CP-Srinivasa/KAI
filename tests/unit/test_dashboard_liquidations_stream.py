"""Endpoint test for /dashboard/api/markets/liquidations-stream (#316)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import dashboard as dashboard_mod
from app.api.routers.dashboard import router
from app.ingestion.liquidations import binance_stream as bs
from app.market_data import liquidation_ledger as ledger_mod
from app.market_data.liquidation_event import LiquidationEvent
from app.market_data.liquidation_ledger import append_event


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _event(notional: float, side: str = "LONG") -> LiquidationEvent:
    now = datetime.now(UTC)
    return LiquidationEvent(
        event_id=f"e:{notional}:{side}",
        source="binance_forceorder",
        exchange="binance",
        symbol="BTCUSDT",
        asset_id="BTC",
        side="SELL" if side == "LONG" else "BUY",
        liquidated_side=side,  # type: ignore[arg-type]
        price=1.0,
        quantity=notional,
        notional_usd=notional,
        event_time=now,
        received_at=now,
        latency_ms=0,
        raw_payload_hash="h",
        confidence=1.0,
        is_snapshot_limited=True,
    )


def test_stream_endpoint_reports_connected_metrics(tmp_path: Path) -> None:
    led = tmp_path / "liq.jsonl"
    hb = tmp_path / "hb.txt"
    append_event(_event(100.0, "LONG"), led)
    append_event(_event(40.0, "SHORT"), led)
    hb.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")

    with (
        patch.object(ledger_mod, "DEFAULT_PATH", led),
        patch.object(bs, "HEARTBEAT_PATH", hb),
        patch.dict(dashboard_mod._liq_stream_cache, {"at": 0.0, "payload": None}),
        TestClient(_app()) as client,
    ):
        r = client.get("/dashboard/api/markets/liquidations-stream")

    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["stream_connected"] is True
    assert body["is_snapshot_limited"] is True
    m = body["metrics"]
    assert m["total_events"] == 2
    assert m["long_notional_usd_15m"] == 100.0
    assert m["short_notional_usd_15m"] == 40.0
    assert m["feed_health"] == "ok"


def test_stream_endpoint_offline_without_heartbeat(tmp_path: Path) -> None:
    led = tmp_path / "liq.jsonl"
    hb = tmp_path / "missing.txt"  # no heartbeat written → stream considered down

    with (
        patch.object(ledger_mod, "DEFAULT_PATH", led),
        patch.object(bs, "HEARTBEAT_PATH", hb),
        patch.dict(dashboard_mod._liq_stream_cache, {"at": 0.0, "payload": None}),
        TestClient(_app()) as client,
    ):
        r = client.get("/dashboard/api/markets/liquidations-stream")

    body = r.json()
    assert body["available"] is False
    assert body["stream_connected"] is False
    assert body["heartbeat_age_seconds"] is None
    assert body["metrics"]["feed_health"] == "no_data"
