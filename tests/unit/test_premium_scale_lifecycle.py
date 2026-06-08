from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from app.execution import envelope_to_paper_bridge as bridge
from app.execution import scale_resolver as sr
from app.execution.paper_engine_singleton import reset_paper_engine_cache
from app.observability.premium_signal_trail import build_trail

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
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_ENTRY_TOLERANCE_PCT", "0.5")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "true")
    yield artifacts
    reset_paper_engine_cache()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_envelopes(path: Path, payload: dict[str, Any]) -> None:
    origin = {
        "timestamp_utc": "2026-06-08T11:00:00+00:00",
        "event": "telegram_channel_envelope",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": "telegram_premium_channel",
        "envelope_id": "ENV-SCALE-RAW",
        "source_uid": "telegram:-1001:9100",
        "payload": dict(payload),
    }
    approved = {
        **origin,
        "timestamp_utc": "2026-06-08T11:01:00+00:00",
        "event": "telegram_channel_approval",
        "source": "telegram_premium_channel_approved",
        "envelope_id": "ENV-SCALE-APPROVED",
        "origin_envelope_id": "ENV-SCALE-RAW",
        "payload": dict(payload),
    }
    path.write_text(
        json.dumps(origin, separators=(",", ":"))
        + "\n"
        + json.dumps(approved, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "symbol": "TICKUSDT",
        "display_symbol": "TICK/USDT",
        "side": "buy",
        "direction": "long",
        "entry_type": "at",
        "entry_value": 24.800,
        "stop_loss": 20.0,
        "targets": [30.0, 35.0],
        "leverage": 10,
        "scale_unknown": True,
        "source_uid": "telegram:-1001:9100",
    }
    base.update(overrides)
    return base


def _fixed_provider(expected_symbol: str, price: float) -> PriceProvider:
    async def _provider(symbol: str) -> float | None:
        assert symbol == expected_symbol
        return price

    return _provider


async def _no_live_price(symbol: str) -> float | None:
    raise AssertionError(f"unexpected live lookup for {symbol}")


def test_detect_scale_factor_accepts_exact_100x_tick_but_not_loose_100x() -> None:
    assert sr.detect_scale_factor(24.800, 0.248) == 1e2
    assert sr.detect_scale_factor(24.800, 0.240) == 1.0


@pytest.mark.asyncio
async def test_bridge_persists_scale_resolution_and_trail_uses_scaled_plan(
    isolated_bridge: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope_log = isolated_bridge / "telegram_message_envelope.jsonl"
    bridge_log = isolated_bridge / "bridge_pending_orders.jsonl"
    paper_log = isolated_bridge / "paper_execution_audit.jsonl"
    _write_envelopes(envelope_log, _payload())
    monkeypatch.setattr(bridge, "_fetch_price", _no_live_price)

    result = await bridge.run_tick(price_provider=_fixed_provider("TICK/USDT", 0.248))

    assert result.filled == 1, result.to_dict()
    records = _read_jsonl(bridge_log)
    scale_events = [r for r in records if r.get("event") == "premium_scale_resolved_persisted"]
    assert len(scale_events) == 1
    scale = scale_events[0]
    assert scale["scale_unknown"] is False
    assert scale["scale_factor"] == pytest.approx(100.0)
    assert scale["scaled_entry"] == pytest.approx(0.248)
    assert scale["scaled_stop_loss"] == pytest.approx(0.2)
    assert scale["scaled_targets"] == [pytest.approx(0.3), pytest.approx(0.35)]
    assert scale["scale_source"] == "bridge_market_price"

    [entry] = build_trail(
        envelope_records=_read_jsonl(envelope_log),
        bridge_records=records,
        paper_records=_read_jsonl(paper_log),
    )
    assert entry.entry_value == pytest.approx(0.248)
    assert entry.stop_loss == pytest.approx(0.2)
    assert entry.targets == [pytest.approx(0.3), pytest.approx(0.35)]
    assert entry.scale_unknown is False
    assert entry.scale_factor == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_extreme_unresolved_scale_gets_scale_reason_before_market_reason(
    isolated_bridge: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope_log = isolated_bridge / "telegram_message_envelope.jsonl"
    bridge_log = isolated_bridge / "bridge_pending_orders.jsonl"
    _write_envelopes(
        envelope_log,
        _payload(entry_value=200.0, stop_loss=190.0, targets=[220.0]),
    )
    monkeypatch.setattr(bridge, "_fetch_price", _no_live_price)

    result = await bridge.run_tick(price_provider=_fixed_provider("TICK/USDT", 1.5))

    assert result.rejected_size == 1, result.to_dict()
    rejected = [
        r
        for r in _read_jsonl(bridge_log)
        if r.get("event") == "premium_scale_unresolved_or_bad_price"
    ]
    assert len(rejected) == 1
    assert rejected[0]["stage"] == "rejected_scale_review"
    assert rejected[0]["reason"] == "scale_unresolved_or_bad_price"
    assert rejected[0]["audit_reason"] == "scale_unresolved_or_bad_price"
