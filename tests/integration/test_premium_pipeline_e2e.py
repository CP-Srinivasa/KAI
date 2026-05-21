"""Premium Telegram -> approved envelope -> bridge -> paper audit E2E tests.

These tests intentionally exercise the real premium parser, envelope emitter,
approval re-emit, bridge, and PaperExecutionEngine composition. The only
external dependency is market data, injected through a deterministic
price_provider so the suite never reaches live APIs.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.execution import envelope_to_paper_bridge as bridge
from app.execution.paper_engine_singleton import get_paper_engine
from app.ingestion.telegram_channel_approval import handle_signal_approval
from app.ingestion.telegram_channel_envelope import (
    DEFAULT_SOURCE,
    emit_parsed_signal,
)
from app.ingestion.telegram_channel_parser import parse_premium_channel_message

PriceProvider = Callable[[str], Awaitable[float | None]]


PREMIUM_SOL_LONG = """\
Binance Futures, OKX, Bybit
#SOL/USDT Long/BUY - 84.20
Targets : 85.08 - 86.10 - 87.40
Stop Loss : 83.78
Leverage - 10x
Risk: 5%
"""

PREMIUM_IRYS_LONG_INVALID_SL = """\
Binance Futures, OKX, Bybit
#IRYS/USDT Long/BUY - 0.05455
Targets:
0.058
0.061
Stop Loss - 0.05230
Leverage - 10x
Risk: 5%
"""


@pytest.fixture
def isolated_premium_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route envelope, bridge, and paper JSONL writes into tmp_path."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bridge, "_ENVELOPE_LOG", artifacts_dir / "telegram_message_envelope.jsonl")
    monkeypatch.setattr(bridge, "_BRIDGE_LOG", artifacts_dir / "bridge_pending_orders.jsonl")
    monkeypatch.setattr(bridge, "_PAPER_AUDIT_LOG", artifacts_dir / "paper_execution_audit.jsonl")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv(
        "EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST",
        f"{DEFAULT_SOURCE}_approved",
    )
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_TTL_HOURS", "24")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_ENTRY_TOLERANCE_PCT", "0.5")
    return artifacts_dir


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _stage_names(records: list[dict[str, Any]]) -> list[str]:
    stages: list[str] = []
    for rec in records:
        stage = rec.get("stage") or rec.get("event_type")
        if isinstance(stage, str):
            stages.append(stage)
    return stages


async def _forbid_live_market_data(symbol: str) -> float | None:
    raise AssertionError(f"unexpected live market-data lookup for {symbol}")


def _fixed_price_provider(expected_symbol: str, price: float) -> PriceProvider:
    async def _provider(symbol: str) -> float | None:
        assert symbol == expected_symbol
        return price

    return _provider


def _emit_and_approve(
    raw_text: str,
    *,
    envelope_log: Path,
    emitted_at: datetime,
    approved_at: datetime,
) -> tuple[str, str]:
    parsed = parse_premium_channel_message(raw_text)
    assert parsed is not None
    origin_record = emit_parsed_signal(
        parsed,
        envelope_log=envelope_log,
        now=emitted_at,
        scale_factor=1.0,
    )
    assert origin_record is not None

    outcome = handle_signal_approval(
        "fill",
        str(origin_record["envelope_id"]),
        envelope_log=envelope_log,
        ttl_minutes=60,
        approved_by="integration-test",
        now=approved_at,
    )
    assert outcome.status == "filled"
    assert outcome.new_envelope_id is not None
    return str(origin_record["envelope_id"]), outcome.new_envelope_id


@pytest.mark.asyncio
async def test_premium_telegram_approved_signal_reaches_paper_fill(
    isolated_premium_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope_log = isolated_premium_artifacts / "telegram_message_envelope.jsonl"
    bridge_log = isolated_premium_artifacts / "bridge_pending_orders.jsonl"
    paper_audit_log = isolated_premium_artifacts / "paper_execution_audit.jsonl"
    emitted_at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    approved_at = emitted_at + timedelta(minutes=3)

    monkeypatch.setattr(bridge, "_fetch_price", _forbid_live_market_data)
    origin_envelope_id, approved_envelope_id = _emit_and_approve(
        PREMIUM_SOL_LONG,
        envelope_log=envelope_log,
        emitted_at=emitted_at,
        approved_at=approved_at,
    )

    result = await bridge.run_tick(price_provider=_fixed_price_provider("SOL/USDT", 84.2))

    assert result.enabled is True
    assert result.filled == 1, result.to_dict()
    assert result.skipped_source == 1

    envelope_records = _read_jsonl(envelope_log)
    bridge_records = _read_jsonl(bridge_log)
    paper_records = _read_jsonl(paper_audit_log)

    assert _stage_names(envelope_records[:1]) == ["accepted"]
    approved = envelope_records[-1]
    assert approved["event"] == "telegram_channel_approval"
    assert approved["source"] == "telegram_premium_channel_approved"
    assert approved["origin_envelope_id"] == origin_envelope_id
    assert approved["envelope_id"] == approved_envelope_id

    filled_bridge = [rec for rec in bridge_records if rec.get("stage") == "filled"]
    assert len(filled_bridge) == 1
    bridge_fill = filled_bridge[0]
    assert bridge_fill["envelope_id"] == approved_envelope_id
    assert bridge_fill["correlation_id"] == origin_envelope_id
    assert bridge_fill["symbol"] == "SOL/USDT"
    assert bridge_fill["audit_reason"] == "paper_order_filled"
    assert bridge_fill["lifecycle_state"] == "POSITION_OPEN"
    assert bridge_fill["order_intent"]["correlation_id"] == origin_envelope_id
    assert bridge_fill["order_intent"]["source"] == "telegram_premium_channel_approved"

    paper_fills = [rec for rec in paper_records if rec.get("event_type") == "order_filled"]
    assert len(paper_fills) == 1
    assert paper_fills[0]["symbol"] == "SOL/USDT"
    assert paper_fills[0]["correlation_id"] == origin_envelope_id

    lifecycle = [rec for rec in paper_records if rec.get("event_type") == "lifecycle_transition"]
    assert [rec["to_state"] for rec in lifecycle] == [
        "ORDER_SUBMITTED",
        "ORDER_ACCEPTED",
        "POSITION_OPEN",
    ]
    assert {rec["correlation_id"] for rec in lifecycle} == {origin_envelope_id}

    engine = get_paper_engine()
    position = engine.portfolio.positions["SOL/USDT"]
    assert position.correlation_id == origin_envelope_id
    assert position.source == "telegram_premium_channel_approved"

    observed_chain = {
        "parsed": parse_premium_channel_message(PREMIUM_SOL_LONG) is not None,
        "envelope": envelope_records[0]["event"] == "telegram_channel_envelope",
        "approved": approved["event"] == "telegram_channel_approval",
        "bridge": bridge_fill["stage"] == "filled",
        "paper": bool(paper_fills),
        "paper_audit": bool(lifecycle),
    }
    assert all(observed_chain.values()), observed_chain


@pytest.mark.asyncio
async def test_premium_telegram_invalid_long_sl_is_bridge_rejected(
    isolated_premium_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope_log = isolated_premium_artifacts / "telegram_message_envelope.jsonl"
    bridge_log = isolated_premium_artifacts / "bridge_pending_orders.jsonl"
    paper_audit_log = isolated_premium_artifacts / "paper_execution_audit.jsonl"
    emitted_at = datetime(2026, 5, 20, 19, 9, tzinfo=UTC)
    approved_at = emitted_at + timedelta(minutes=2)

    monkeypatch.setattr(bridge, "_fetch_price", _forbid_live_market_data)
    origin_envelope_id, approved_envelope_id = _emit_and_approve(
        PREMIUM_IRYS_LONG_INVALID_SL,
        envelope_log=envelope_log,
        emitted_at=emitted_at,
        approved_at=approved_at,
    )

    result = await bridge.run_tick(price_provider=_fixed_price_provider("IRYS/USDT", 0.05153))

    assert result.enabled is True
    assert result.filled == 0
    assert result.rejected_size == 1, result.to_dict()

    bridge_records = _read_jsonl(bridge_log)
    rejected = [rec for rec in bridge_records if rec.get("stage") == "rejected_scale_review"]
    assert len(rejected) == 1
    reject = rejected[0]
    assert reject["envelope_id"] == approved_envelope_id
    assert reject["correlation_id"] == origin_envelope_id
    assert reject["reason"] == "long_sl_at_or_above_spot"
    assert reject["audit_reason"] == "long_sl_at_or_above_spot"
    assert reject["lifecycle_state"] == "REJECTED_INVALID_SIGNAL"
    assert reject["order_intent"]["correlation_id"] == origin_envelope_id

    engine = get_paper_engine()
    assert "IRYS/USDT" not in engine.portfolio.positions
    assert [rec for rec in _read_jsonl(paper_audit_log) if rec.get("symbol") == "IRYS/USDT"] == []
