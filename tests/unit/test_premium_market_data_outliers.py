from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from app.execution import envelope_to_paper_bridge as bridge
from app.execution.paper_engine_singleton import reset_paper_engine_cache

PriceProvider = Callable[[str], Awaitable[float | None]]


@pytest.fixture
def isolated_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    reset_paper_engine_cache()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bridge, "_ENVELOPE_LOG", artifacts / "telegram_message_envelope.jsonl")
    monkeypatch.setattr(bridge, "_BRIDGE_LOG", artifacts / "bridge_pending_orders.jsonl")
    monkeypatch.setattr(bridge, "_PAPER_AUDIT_LOG", artifacts / "paper_execution_audit.jsonl")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv(
        "EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST",
        "telegram_premium_channel_approved",
    )
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_TTL_HOURS", "24")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_ENTRY_TOLERANCE_PCT", "0.5")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "true")
    yield artifacts
    reset_paper_engine_cache()


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(rec, separators=(",", ":")) + "\n" for rec in records),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _skyai_envelopes() -> list[dict[str, Any]]:
    payload = {
        "symbol": "SKYAIUSDT",
        "display_symbol": "SKYAI/USDT",
        "side": "buy",
        "direction": "long",
        "entry_type": "above",
        "entry_value": 0.40,
        "stop_loss": 0.30,
        "targets": [0.50, 0.60],
        "leverage": 10,
        "scale_factor": 1.0,
        "scale_resolved_at_emit": True,
        "source_uid": "telegram:-1001:9001",
    }
    origin = {
        "timestamp_utc": "2026-06-08T10:00:00+00:00",
        "event": "telegram_channel_envelope",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": "telegram_premium_channel",
        "envelope_id": "ENV-SKYAI-RAW",
        "source_uid": "telegram:-1001:9001",
        "payload": dict(payload),
    }
    approved = {
        **origin,
        "timestamp_utc": "2026-06-08T10:01:00+00:00",
        "event": "telegram_channel_approval",
        "source": "telegram_premium_channel_approved",
        "envelope_id": "ENV-SKYAI-APPROVED",
        "origin_envelope_id": "ENV-SKYAI-RAW",
        "payload": dict(payload),
    }
    return [origin, approved]


def _sequence_provider(prices: list[float | None]) -> PriceProvider:
    remaining = iter(prices)

    async def _provider(symbol: str) -> float | None:
        assert symbol == "SKYAI/USDT"
        return next(remaining)

    return _provider


async def _no_live_price(symbol: str) -> float | None:
    raise AssertionError(f"unexpected live lookup for {symbol}")


async def _unavailable_price(symbol: str) -> float | None:
    return None


@pytest.mark.asyncio
async def test_skyai_garbage_tick_is_rejected_without_terminal_state(
    isolated_bridge: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope_log = isolated_bridge / "telegram_message_envelope.jsonl"
    bridge_log = isolated_bridge / "bridge_pending_orders.jsonl"
    _write_jsonl(envelope_log, _skyai_envelopes())
    monkeypatch.setattr(bridge, "_fetch_price", _unavailable_price)
    prices = _sequence_provider([0.356, None, 0.356, 101.94])

    for _ in range(4):
        await bridge.run_tick(price_provider=prices)

    records = _read_jsonl(bridge_log)
    approved_records = [r for r in records if r.get("envelope_id") == "ENV-SKYAI-APPROVED"]
    assert any(r.get("current_price") == pytest.approx(0.356) for r in approved_records)
    bad = [r for r in approved_records if r.get("event") == "premium_market_price_outlier_rejected"]
    assert len(bad) == 1
    assert bad[0]["current_price"] == pytest.approx(101.94)
    assert bad[0]["reason"] == "pending_entry_with_bad_tick_ignored"
    assert bad[0]["audit_events"] == [
        "premium_market_price_outlier_rejected",
        "premium_bad_tick_ignored",
    ]
    assert not any(r.get("stage") == "rejected_scale_review" for r in approved_records)
    assert not any(r.get("stage") == "filled" for r in approved_records)


@pytest.mark.asyncio
async def test_three_consecutive_bad_ticks_terminalize_stably(
    isolated_bridge: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope_log = isolated_bridge / "telegram_message_envelope.jsonl"
    bridge_log = isolated_bridge / "bridge_pending_orders.jsonl"
    _write_jsonl(envelope_log, _skyai_envelopes())
    monkeypatch.setattr(bridge, "_fetch_price", _no_live_price)
    prices = _sequence_provider([0.356, 101.94, 102.0, 103.0])

    for _ in range(4):
        await bridge.run_tick(price_provider=prices)

    terminal = [
        r for r in _read_jsonl(bridge_log) if r.get("event") == "premium_terminal_stabilized"
    ]
    assert len(terminal) == 1
    assert terminal[0]["stage"] == "rejected_scale_review"
    assert terminal[0]["bad_tick_count"] == 3
