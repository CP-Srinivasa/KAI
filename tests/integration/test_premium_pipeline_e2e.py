"""Premium Telegram -> approved envelope -> bridge -> paper audit E2E tests.

These tests intentionally exercise the real premium parser, envelope emitter,
approval re-emit, bridge, and PaperExecutionEngine composition. The only
external dependency is market data, injected through a deterministic
price_provider so the suite never reaches live APIs.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.execution import envelope_to_paper_bridge as bridge
from app.execution import paper_engine as paper_engine_module
from app.execution.paper_engine_singleton import (
    get_paper_engine,
    reset_paper_engine_cache,
)
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

PREMIUM_SOL_LONG_RANGE = """\
Binance Futures, OKX, Bybit
#SOL/USDT Long/BUY
Entry Zone: 84.20 - 84.50
Targets : 85.08 - 86.10 - 87.40
Stop Loss : 83.78
Leverage - 10x
Risk: 5%
"""

PREMIUM_ETH_LONG_RANGE = """\
Binance Futures, OKX, Bybit
#ETH/USDT Long/BUY
Entry Zone: 3000.00 - 3010.00
Targets : 3050.00 - 3100.00 - 3150.00
Stop Loss : 2980.00
Leverage - 10x
Risk: 5%
"""

PREMIUM_SKYAI_BREAKOUT = """\
Binance Futures, OKX, Bybit
#SKYAI/USDT Long/BUY
Entry Above - 0.40
Targets : 0.50 - 0.60
Stop Loss : 0.30
Leverage - 10x
Risk: 5%
"""


@pytest.fixture
def isolated_premium_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Route envelope, bridge, and paper JSONL writes into tmp_path.

    Also resets the process-local PaperExecutionEngine singleton before and
    after each test so portfolio state cannot bleed between tests (e.g.
    a SOL position from V1 still visible to V2.1). Documented escape-hatch
    from app/execution/paper_engine_singleton.py:52.
    """
    reset_paper_engine_cache()
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
    # premium.paper_execution_enabled now defaults to False (explicit opt-in);
    # the positive-path premium tests must enable it. The disabled-blocks test
    # overrides this back to "false" in its own body.
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "true")
    yield artifacts_dir
    reset_paper_engine_cache()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
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


async def _unavailable_market_data(symbol: str) -> float | None:
    return None


class _FixedBridgeDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> _FixedBridgeDatetime:
        fixed = cls(2026, 5, 20, 12, 10, tzinfo=UTC)
        if tz is None:
            return fixed
        shifted = fixed.astimezone(tz)
        return cls.fromtimestamp(shifted.timestamp(), tz)


def _fixed_price_provider(expected_symbol: str, price: float) -> PriceProvider:
    async def _provider(symbol: str) -> float | None:
        assert symbol == expected_symbol
        return price

    return _provider


def _sequence_price_provider(expected_symbol: str, prices: list[float | None]) -> PriceProvider:
    remaining = iter(prices)

    async def _provider(symbol: str) -> float | None:
        assert symbol == expected_symbol
        return next(remaining)

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
    monkeypatch.setattr(bridge, "datetime", _FixedBridgeDatetime)
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
    monkeypatch.setattr(bridge, "datetime", _FixedBridgeDatetime)
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


# ── V2: partial-entry-fill spec (see artifacts/operator_memos/
# premium_pipeline_e2e_v2_spec_2026-05-21.md) ────────────────────────────────


def _freeze_bridge_now(at_time: datetime) -> type[datetime]:
    """Build a datetime subclass whose .now() always returns ``at_time``.

    Used to monkeypatch ``bridge.datetime`` so TTL math against the wall
    clock stays deterministic regardless of when the test actually runs.
    Same pattern as the module-level _FixedBridgeDatetime helper, just
    parameterised so V2.1/V2.2/V2.3 can each pin a different "now".
    """

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> _Frozen:
            if tz is None:
                return cls(
                    at_time.year,
                    at_time.month,
                    at_time.day,
                    at_time.hour,
                    at_time.minute,
                    at_time.second,
                    at_time.microsecond,
                    tzinfo=None,
                )
            shifted = at_time.astimezone(tz)
            return cls.fromtimestamp(shifted.timestamp(), tz)

    return _Frozen


@pytest.mark.asyncio
async def test_premium_telegram_range_entry_multi_tick_fills_on_second_tick(
    isolated_premium_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2.1 — Range-Entry SOL 84.20-84.50: first tick price 84.10 below
    range stays pending, second tick price 84.30 inside range fills."""
    envelope_log = isolated_premium_artifacts / "telegram_message_envelope.jsonl"
    bridge_log = isolated_premium_artifacts / "bridge_pending_orders.jsonl"
    paper_audit_log = isolated_premium_artifacts / "paper_execution_audit.jsonl"
    emitted_at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    approved_at = emitted_at + timedelta(minutes=3)

    monkeypatch.setattr(bridge, "_fetch_price", _forbid_live_market_data)
    monkeypatch.setattr(bridge, "datetime", _freeze_bridge_now(emitted_at + timedelta(minutes=10)))

    origin_envelope_id, approved_envelope_id = _emit_and_approve(
        PREMIUM_SOL_LONG_RANGE,
        envelope_log=envelope_log,
        emitted_at=emitted_at,
        approved_at=approved_at,
    )

    tick1 = await bridge.run_tick(price_provider=_fixed_price_provider("SOL/USDT", 84.10))
    assert tick1.enabled is True
    assert tick1.filled == 0, tick1.to_dict()
    assert tick1.expired == 0, tick1.to_dict()

    bridge_records_after_tick1 = _read_jsonl(bridge_log)
    approved_after_tick1 = [
        rec for rec in bridge_records_after_tick1 if rec.get("envelope_id") == approved_envelope_id
    ]
    assert approved_after_tick1, "approved envelope must have at least one bridge record"
    last_stage_tick1 = approved_after_tick1[-1]["stage"]
    assert last_stage_tick1 == "pending", (
        f"expected pending after sub-range tick, got {last_stage_tick1}"
    )
    paper_records_tick1 = _read_jsonl(paper_audit_log)
    assert not [rec for rec in paper_records_tick1 if rec.get("event_type") == "order_filled"]

    tick2 = await bridge.run_tick(price_provider=_fixed_price_provider("SOL/USDT", 84.30))
    assert tick2.filled == 1, tick2.to_dict()

    bridge_records = _read_jsonl(bridge_log)
    filled_bridge = [
        rec
        for rec in bridge_records
        if rec.get("envelope_id") == approved_envelope_id and rec.get("stage") == "filled"
    ]
    assert len(filled_bridge) == 1, "exactly one fill across both ticks"
    bridge_fill = filled_bridge[0]
    assert bridge_fill["correlation_id"] == origin_envelope_id
    assert bridge_fill["symbol"] == "SOL/USDT"
    assert bridge_fill["lifecycle_state"] == "POSITION_OPEN"

    paper_fills = [
        rec for rec in _read_jsonl(paper_audit_log) if rec.get("event_type") == "order_filled"
    ]
    assert len(paper_fills) == 1
    assert paper_fills[0]["correlation_id"] == origin_envelope_id

    engine = get_paper_engine()
    position = engine.portfolio.positions["SOL/USDT"]
    assert position.correlation_id == origin_envelope_id
    assert position.quantity > 0


@pytest.mark.asyncio
async def test_premium_skyai_bad_tick_replay_stays_pending_not_terminal(
    isolated_premium_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope_log = isolated_premium_artifacts / "telegram_message_envelope.jsonl"
    bridge_log = isolated_premium_artifacts / "bridge_pending_orders.jsonl"
    paper_audit_log = isolated_premium_artifacts / "paper_execution_audit.jsonl"
    emitted_at = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)
    approved_at = emitted_at + timedelta(minutes=2)

    monkeypatch.setattr(bridge, "_fetch_price", _unavailable_market_data)
    monkeypatch.setattr(bridge, "datetime", _freeze_bridge_now(emitted_at + timedelta(minutes=10)))

    _, approved_envelope_id = _emit_and_approve(
        PREMIUM_SKYAI_BREAKOUT,
        envelope_log=envelope_log,
        emitted_at=emitted_at,
        approved_at=approved_at,
    )
    prices = _sequence_price_provider("SKYAI/USDT", [0.356, None, 0.356, 101.94])

    for _ in range(4):
        result = await bridge.run_tick(price_provider=prices)
        assert result.filled == 0, result.to_dict()

    bridge_records = _read_jsonl(bridge_log)
    approved_bridge = [
        rec for rec in bridge_records if rec.get("envelope_id") == approved_envelope_id
    ]
    outliers = [
        rec
        for rec in approved_bridge
        if rec.get("event") == "premium_market_price_outlier_rejected"
    ]
    assert len(outliers) == 1
    assert outliers[0]["current_price"] == pytest.approx(101.94)
    assert outliers[0]["reason"] == "pending_entry_with_bad_tick_ignored"
    assert not [rec for rec in approved_bridge if rec.get("stage") == "rejected_scale_review"]
    assert not [rec for rec in _read_jsonl(paper_audit_log) if rec.get("symbol") == "SKYAI/USDT"]


@pytest.mark.asyncio
async def test_premium_telegram_range_entry_ttl_expires_without_fill(
    isolated_premium_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2.2 — Range-Entry SOL 84.20-84.50, TTL=1h: bridge "now" frozen
    2 hours after emitted_at, even with a price inside the range the
    bridge must emit "expired" instead of filling. TTL is Gate 2 in
    _process_one (envelope_to_paper_bridge.py:620-639), so it runs
    BEFORE the range check."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_TTL_HOURS", "1")

    envelope_log = isolated_premium_artifacts / "telegram_message_envelope.jsonl"
    bridge_log = isolated_premium_artifacts / "bridge_pending_orders.jsonl"
    paper_audit_log = isolated_premium_artifacts / "paper_execution_audit.jsonl"
    emitted_at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    approved_at = emitted_at + timedelta(minutes=2)
    bridge_now = emitted_at + timedelta(hours=2)  # past 1h TTL

    monkeypatch.setattr(bridge, "_fetch_price", _forbid_live_market_data)
    monkeypatch.setattr(bridge, "datetime", _freeze_bridge_now(bridge_now))

    origin_envelope_id, approved_envelope_id = _emit_and_approve(
        PREMIUM_SOL_LONG_RANGE,
        envelope_log=envelope_log,
        emitted_at=emitted_at,
        approved_at=approved_at,
    )

    tick = await bridge.run_tick(price_provider=_fixed_price_provider("SOL/USDT", 84.30))
    assert tick.filled == 0, tick.to_dict()
    assert tick.expired == 1, tick.to_dict()

    bridge_records = _read_jsonl(bridge_log)
    expired_records = [
        rec
        for rec in bridge_records
        if rec.get("envelope_id") == approved_envelope_id and rec.get("stage") == "expired"
    ]
    assert len(expired_records) == 1
    expired_rec = expired_records[0]
    assert expired_rec["correlation_id"] == origin_envelope_id
    assert expired_rec["lifecycle_state"] == "EXPIRED"
    assert expired_rec["ttl_hours"] == 1

    paper_fills = [
        rec for rec in _read_jsonl(paper_audit_log) if rec.get("event_type") == "order_filled"
    ]
    assert paper_fills == [], "TTL-expiry must short-circuit before any paper fill"

    engine = get_paper_engine()
    assert "SOL/USDT" not in engine.portfolio.positions


@pytest.mark.asyncio
async def test_premium_telegram_partial_fill_opens_half_position(
    isolated_premium_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2.3 — Partial-fill durchläuft: monkeypatch
    PaperExecutionEngine.create_order to inject partial_fill_ratio=0.5,
    verifying the paper layer opens a half-quantity position while
    keeping the correlation_id chain intact end-to-end.

    Bridge currently never sets partial_fill_ratio < 1.0 — this test
    documents the paper-layer behaviour that any future Bridge extension
    must NOT regress. If the Bridge feature never lands, V2.3 can be
    closed without harm (V2-Spec R4)."""
    envelope_log = isolated_premium_artifacts / "telegram_message_envelope.jsonl"
    bridge_log = isolated_premium_artifacts / "bridge_pending_orders.jsonl"
    paper_audit_log = isolated_premium_artifacts / "paper_execution_audit.jsonl"
    emitted_at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    approved_at = emitted_at + timedelta(minutes=3)

    monkeypatch.setattr(bridge, "_fetch_price", _forbid_live_market_data)
    monkeypatch.setattr(bridge, "datetime", _freeze_bridge_now(emitted_at + timedelta(minutes=10)))

    original_create_order = paper_engine_module.PaperExecutionEngine.create_order

    def patched_create_order(
        self: paper_engine_module.PaperExecutionEngine,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        kwargs.setdefault("partial_fill_ratio", 0.5)
        return original_create_order(self, *args, **kwargs)

    monkeypatch.setattr(
        paper_engine_module.PaperExecutionEngine,
        "create_order",
        patched_create_order,
    )

    origin_envelope_id, approved_envelope_id = _emit_and_approve(
        PREMIUM_ETH_LONG_RANGE,
        envelope_log=envelope_log,
        emitted_at=emitted_at,
        approved_at=approved_at,
    )

    result = await bridge.run_tick(price_provider=_fixed_price_provider("ETH/USDT", 3005.0))
    assert result.filled == 1, result.to_dict()

    paper_fills = [
        rec for rec in _read_jsonl(paper_audit_log) if rec.get("event_type") == "order_filled"
    ]
    assert len(paper_fills) == 1
    fill = paper_fills[0]
    assert fill["fill_status"] == "partial_entry"
    assert fill["partial_fill_ratio"] == pytest.approx(0.5)
    assert fill["filled_quantity"] == pytest.approx(fill["requested_quantity"] * 0.5, rel=1e-9)
    assert fill["remaining_quantity"] == pytest.approx(fill["requested_quantity"] * 0.5, rel=1e-9)
    assert fill["correlation_id"] == origin_envelope_id

    engine = get_paper_engine()
    position = engine.portfolio.positions["ETH/USDT"]
    assert position.quantity == pytest.approx(fill["filled_quantity"], rel=1e-9)
    assert position.correlation_id == origin_envelope_id

    bridge_records = _read_jsonl(bridge_log)
    filled_bridge = [
        rec
        for rec in bridge_records
        if rec.get("envelope_id") == approved_envelope_id and rec.get("stage") == "filled"
    ]
    assert len(filled_bridge) == 1
    # Bridge does not know about partial fills — stage stays "filled"
    # (documented limitation per V2-Spec R4).
    assert filled_bridge[0]["correlation_id"] == origin_envelope_id


@pytest.mark.asyncio
async def test_premium_telegram_entry_watcher_rejects_implausible_then_triggers(
    isolated_premium_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2.4 — EntryRangeWatcher Plausibility-Outlier via OperatorEntryWatch.

    Different test path than V2.1-V2.3: instead of bridge.run_tick() directly,
    this exercises the HF-polling layer (app.execution.operator_entry_watch.
    run_watch_once). The watcher accumulates a rolling-median plausibility
    window; once filled, an outlier tick must be rejected without poisoning
    the window, and the next plausible in-range tick triggers a fill via
    the same bridge.run_tick path.

    Sequence of 7 ticks for SOL/USDT (Entry Zone 84.20-84.50):
      1-5: 83.50, 83.60, 83.70, 83.80, 83.90 (below range → HOLD, window fills)
      6:   50.00 (window-filled, deviation >> 5% from median ~83.70
            → REJECT_TICK_PLAUSIBILITY, NOT recorded in window)
      7:   84.30 (inside range, ~0.7% from median → plausible
            → TRIGGER_ENTRY → bridge.run_tick fills)
    """
    from app.execution import operator_entry_watch as oew
    from app.execution.entry_watcher import EntryWatcherConfig
    from app.market_data.models import MarketDataSnapshot

    envelope_log = isolated_premium_artifacts / "telegram_message_envelope.jsonl"
    bridge_log = isolated_premium_artifacts / "bridge_pending_orders.jsonl"
    paper_audit_log = isolated_premium_artifacts / "paper_execution_audit.jsonl"
    entry_watch_audit = isolated_premium_artifacts / "entry_watcher_audit.jsonl"

    emitted_at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    approved_at = emitted_at + timedelta(minutes=3)
    frozen_now = _freeze_bridge_now(emitted_at + timedelta(minutes=10))

    monkeypatch.setattr(bridge, "_fetch_price", _forbid_live_market_data)
    monkeypatch.setattr(bridge, "datetime", frozen_now)
    monkeypatch.setattr(oew, "_ENVELOPE_LOG", envelope_log)
    monkeypatch.setattr(oew, "_BRIDGE_LOG", bridge_log)
    monkeypatch.setattr(oew, "_ENTRY_WATCH_AUDIT", entry_watch_audit)

    origin_envelope_id, _ = _emit_and_approve(
        PREMIUM_SOL_LONG_RANGE,
        envelope_log=envelope_log,
        emitted_at=emitted_at,
        approved_at=approved_at,
    )

    price_sequence = iter([83.50, 83.60, 83.70, 83.80, 83.90, 50.00, 84.30])

    async def _stub_snapshot(symbol: str, config: EntryWatcherConfig) -> MarketDataSnapshot:
        now_iso = datetime(2026, 5, 20, 12, 10, tzinfo=UTC).isoformat()
        return MarketDataSnapshot(
            symbol=symbol,
            provider="stub",
            retrieved_at_utc=now_iso,
            source_timestamp_utc=now_iso,
            price=next(price_sequence),
            is_stale=False,
            freshness_seconds=1.0,
            available=True,
        )

    monkeypatch.setattr(oew, "_snapshot", _stub_snapshot)

    cfg = EntryWatcherConfig()
    watchers: dict[str, Any] = {}
    aggregate = oew.EntryWatchResult(enabled=True)
    for _ in range(7):
        tick_result = await oew.run_watch_once(watchers=watchers, config=cfg)
        aggregate.add(tick_result)

    assert aggregate.implausible == 1, aggregate.to_dict()
    assert aggregate.triggered == 1, aggregate.to_dict()
    assert aggregate.held == 5, aggregate.to_dict()
    assert aggregate.bridge_filled == 1, aggregate.to_dict()

    audit = _read_jsonl(entry_watch_audit)
    decisions = [rec["decision"] for rec in audit]
    assert decisions.count("REJECT_TICK_PLAUSIBILITY") == 1
    assert decisions.count("TRIGGER_ENTRY") == 1
    assert decisions.count("HOLD") == 5

    outlier = next(rec for rec in audit if rec["decision"] == "REJECT_TICK_PLAUSIBILITY")
    assert outlier["price"] == pytest.approx(50.00)
    assert outlier["correlation_id"] == origin_envelope_id

    trigger = next(rec for rec in audit if rec["decision"] == "TRIGGER_ENTRY")
    assert trigger["price"] == pytest.approx(84.30)
    assert trigger["correlation_id"] == origin_envelope_id

    paper_fills = [
        rec for rec in _read_jsonl(paper_audit_log) if rec.get("event_type") == "order_filled"
    ]
    assert len(paper_fills) == 1
    assert paper_fills[0]["correlation_id"] == origin_envelope_id

    engine = get_paper_engine()
    position = engine.portfolio.positions["SOL/USDT"]
    assert position.correlation_id == origin_envelope_id
    assert position.quantity > 0


@pytest.mark.asyncio
async def test_premium_paper_execution_disabled_blocks_e2e(
    isolated_premium_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Premium signals are blocked if premium.paper_execution_enabled is False (E2E)."""
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "false")

    envelope_log = isolated_premium_artifacts / "telegram_message_envelope.jsonl"
    bridge_log = isolated_premium_artifacts / "bridge_pending_orders.jsonl"

    emitted_at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    approved_at = emitted_at + timedelta(minutes=3)

    monkeypatch.setattr(bridge, "_fetch_price", _forbid_live_market_data)
    monkeypatch.setattr(bridge, "datetime", _FixedBridgeDatetime)

    origin_envelope_id, approved_envelope_id = _emit_and_approve(
        PREMIUM_SOL_LONG,
        envelope_log=envelope_log,
        emitted_at=emitted_at,
        approved_at=approved_at,
    )

    result = await bridge.run_tick(price_provider=_fixed_price_provider("SOL/USDT", 84.2))

    assert result.filled == 0
    assert result.rejected_entry_mode == 1

    bridge_records = _read_jsonl(bridge_log)
    assert bridge_records[-1]["stage"] == "rejected_entry_mode"
    assert bridge_records[-1]["reason"] == "premium_paper_execution_disabled"
    assert bridge_records[-1]["reason_codes"] == ["ENTRY_MODE_DISABLED"]

    engine = get_paper_engine()
    assert "SOL/USDT" not in engine.portfolio.positions
