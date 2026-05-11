from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution import operator_entry_watch as watch_mod
from app.execution.envelope_to_paper_bridge import BridgeTickResult
from app.market_data.models import MarketDataSnapshot


def _write_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _accepted_envelope(**payload_overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "direction": "long",
        "side": "buy",
        "symbol": "BTCUSDT",
        "display_symbol": "BTC/USDT",
        "entry_type": "range",
        "entry_value": None,
        "entry_min": 65000.0,
        "entry_max": 65500.0,
        "stop_loss": 64200.0,
        "targets": [66000.0, 67000.0],
        "leverage": 10,
        "margin_pct": 5.0,
    }
    payload.update(payload_overrides)
    return {
        "envelope_id": "env-watch-1",
        "stage": "accepted",
        "status": "ok",
        "message_type": "signal",
        "source": "dashboard",
        "timestamp_utc": "2026-05-10T12:00:00+00:00",
        "payload": payload,
    }


@pytest.fixture
def patched_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(watch_mod, "_ENVELOPE_LOG", tmp_path / "telegram.jsonl")
    monkeypatch.setattr(watch_mod, "_BRIDGE_LOG", tmp_path / "bridge.jsonl")
    monkeypatch.setattr(watch_mod, "_ENTRY_WATCH_AUDIT", tmp_path / "watcher.jsonl")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    return tmp_path


def _snapshot(price: float, *, stale: bool = False) -> MarketDataSnapshot:
    return MarketDataSnapshot(
        symbol="BTC/USDT",
        provider="test",
        retrieved_at_utc="2026-05-10T12:00:01+00:00",
        source_timestamp_utc="2026-05-10T12:00:01+00:00",
        price=price,
        is_stale=stale,
        freshness_seconds=99.0 if stale else 1.0,
        available=not stale,
    )


@pytest.mark.asyncio
async def test_entry_watch_triggers_bridge_with_observed_price(
    patched_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_jsonl(watch_mod._ENVELOPE_LOG, _accepted_envelope())

    async def fresh_snapshot(*_args, **_kwargs):
        return _snapshot(65250.0)

    monkeypatch.setattr(watch_mod, "_snapshot", fresh_snapshot)

    seen_prices: list[float | None] = []

    async def fake_run_tick(*, price_provider=None):
        seen_prices.append(await price_provider("BTC/USDT"))
        return BridgeTickResult(enabled=True, envelopes_scanned=1, filled=1)

    monkeypatch.setattr(watch_mod, "run_tick", fake_run_tick)

    result = await watch_mod.run_watch_once()

    assert result.triggered == 1
    assert result.bridge_filled == 1
    assert seen_prices == [65250.0]
    rows = [
        json.loads(line)
        for line in watch_mod._ENTRY_WATCH_AUDIT.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["decision"] == "TRIGGER_ENTRY"
    assert rows[-1]["correlation_id"] == "env-watch-1"


@pytest.mark.asyncio
async def test_entry_watch_stale_data_does_not_call_bridge(
    patched_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_jsonl(watch_mod._ENVELOPE_LOG, _accepted_envelope())

    async def stale_snapshot(*_args, **_kwargs):
        return _snapshot(65250.0, stale=True)

    monkeypatch.setattr(watch_mod, "_snapshot", stale_snapshot)

    async def fail_run_tick(**_kwargs):
        raise AssertionError("bridge must not run on stale data")

    monkeypatch.setattr(watch_mod, "run_tick", fail_run_tick)

    result = await watch_mod.run_watch_once()

    assert result.triggered == 0
    assert result.stale_or_unavailable == 1
