"""Tests for envelope_to_paper_bridge.py (Vorschlag A — Operator-Signal-Bridge).

Scope:
- Pure helpers (allowlist, TTL, tolerance, canonical-symbol, entry-price,
  pending-collection, stage-reduction) — no mocks.
- ``run_tick()`` end-to-end against temp JSONL files with mocked market data,
  verifying fail-closed default, allowlist gating, short-rejection, completeness
  gate, TTL expiry, and deduplication against terminal-staged envelopes.

Intentionally NOT tested:
- RiskEngine internals (covered by ``test_risk_engine.py``).
- PaperExecutionEngine fill math (covered by ``test_paper_execution.py``).
- Full CoinGecko snapshot path (covered by market_data tests).
The bridge composes these; we test the composition, not reimplement the parts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.execution import envelope_to_paper_bridge as bridge
from app.execution.envelope_to_paper_bridge import (
    BridgeTickResult,
    _canonical_symbol,
    _collect_pending_signals,
    _extract_source,
    _latest_bridge_stage_by_envelope,
    _parse_allowlist,
    _resolve_entry_price,
    _ttl_exceeded,
    _within_tolerance,
    run_tick,
)

# ── Pure-helper unit tests ──────────────────────────────────────────────────


class TestParseAllowlist:
    def test_basic_csv(self) -> None:
        assert _parse_allowlist("dashboard,telegram") == frozenset({"dashboard", "telegram"})

    def test_case_and_whitespace_normalized(self) -> None:
        assert _parse_allowlist("  Dashboard , TELEGRAM ") == frozenset({"dashboard", "telegram"})

    def test_empty_entries_skipped(self) -> None:
        assert _parse_allowlist("dashboard,,, ,telegram") == frozenset({"dashboard", "telegram"})

    def test_empty_string(self) -> None:
        assert _parse_allowlist("") == frozenset()


class TestWithinTolerance:
    def test_buy_fills_at_entry(self) -> None:
        assert _within_tolerance(
            current_price=100.0, target_price=100.0, tolerance_pct=0.5, side="buy"
        )

    def test_buy_fills_slightly_above_within_tol(self) -> None:
        # 0.5% tolerance → fill allowed up to 100.5
        assert _within_tolerance(
            current_price=100.4, target_price=100.0, tolerance_pct=0.5, side="buy"
        )

    def test_buy_blocked_above_tolerance(self) -> None:
        assert not _within_tolerance(
            current_price=100.6, target_price=100.0, tolerance_pct=0.5, side="buy"
        )

    def test_buy_fills_below_entry(self) -> None:
        # Buying below target is always fine (better entry).
        assert _within_tolerance(
            current_price=50.0, target_price=100.0, tolerance_pct=0.5, side="buy"
        )

    def test_sell_symmetry(self) -> None:
        # Sell entry: fill when current >= target - tol.
        assert _within_tolerance(
            current_price=100.0, target_price=100.0, tolerance_pct=0.5, side="sell"
        )
        assert not _within_tolerance(
            current_price=99.4, target_price=100.0, tolerance_pct=0.5, side="sell"
        )

    def test_rejects_zero_or_negative_prices(self) -> None:
        assert not _within_tolerance(
            current_price=0.0, target_price=100.0, tolerance_pct=0.5, side="buy"
        )
        assert not _within_tolerance(
            current_price=100.0, target_price=0.0, tolerance_pct=0.5, side="buy"
        )


class TestTtlExceeded:
    def test_within_ttl(self) -> None:
        now = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
        recent = (now - timedelta(hours=5)).isoformat()
        assert not _ttl_exceeded(recent, ttl_hours=24, now=now)

    def test_beyond_ttl(self) -> None:
        now = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
        old = (now - timedelta(hours=25)).isoformat()
        assert _ttl_exceeded(old, ttl_hours=24, now=now)

    def test_missing_timestamp_does_not_expire(self) -> None:
        # Conservative: missing timestamp cannot be proven expired.
        assert not _ttl_exceeded(None, ttl_hours=24)

    def test_malformed_timestamp_does_not_expire(self) -> None:
        assert not _ttl_exceeded("not-a-timestamp", ttl_hours=24)

    def test_naive_timestamp_treated_as_utc(self) -> None:
        now = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
        naive = "2026-04-19T11:00:00"  # 25h earlier, no tz
        assert _ttl_exceeded(naive, ttl_hours=24, now=now)


class TestCanonicalSymbol:
    def test_display_symbol_preferred(self) -> None:
        assert _canonical_symbol({"display_symbol": "BTC/USDT", "symbol": "BTCUSDT"}) == "BTC/USDT"

    def test_bare_symbol_split_on_quote(self) -> None:
        assert _canonical_symbol({"symbol": "BTCUSDT"}) == "BTC/USDT"
        assert _canonical_symbol({"symbol": "ETHUSDC"}) == "ETH/USDC"

    def test_unknown_quote_defaults_to_usdt(self) -> None:
        assert _canonical_symbol({"symbol": "DOGE"}) == "DOGE/USDT"

    def test_already_canonical_preserved(self) -> None:
        assert _canonical_symbol({"symbol": "SOL/USDT"}) == "SOL/USDT"

    def test_empty_returns_empty(self) -> None:
        assert _canonical_symbol({}) == ""


class TestResolveEntryPrice:
    def test_limit(self) -> None:
        assert _resolve_entry_price({"entry_type": "limit", "entry_value": 100.5}) == 100.5

    def test_range_midpoint(self) -> None:
        assert (
            _resolve_entry_price({"entry_type": "range", "entry_min": 100.0, "entry_max": 110.0})
            == 105.0
        )

    def test_range_missing_bounds(self) -> None:
        assert _resolve_entry_price({"entry_type": "range"}) is None

    def test_range_inverted(self) -> None:
        # max <= min: invalid range
        assert (
            _resolve_entry_price({"entry_type": "range", "entry_min": 110.0, "entry_max": 100.0})
            is None
        )

    def test_missing(self) -> None:
        assert _resolve_entry_price({}) is None


class TestExtractSource:
    def test_dashboard_passthrough(self) -> None:
        assert _extract_source({"source": "dashboard"}) == "dashboard"

    def test_telegram_aliases_normalized(self) -> None:
        assert _extract_source({"source": "structured_text"}) == "telegram"
        assert _extract_source({"source": "voice"}) == "telegram"
        assert _extract_source({"source": "natural_language"}) == "telegram"

    def test_unknown_passthrough_lowercased(self) -> None:
        assert _extract_source({"source": "Webhook"}) == "webhook"

    def test_missing(self) -> None:
        assert _extract_source({}) == "unknown"


class TestLatestBridgeStageByEnvelope:
    def test_last_stage_wins(self) -> None:
        records = [
            {"envelope_id": "A", "stage": "pending"},
            {"envelope_id": "A", "stage": "filled"},
            {"envelope_id": "B", "stage": "pending"},
        ]
        assert _latest_bridge_stage_by_envelope(records) == {
            "A": "filled",
            "B": "pending",
        }

    def test_bad_records_skipped(self) -> None:
        records: list[dict[str, Any]] = [
            {"envelope_id": "A"},  # missing stage
            {"stage": "filled"},  # missing id
            {"envelope_id": 42, "stage": "filled"},  # non-string id
            {"envelope_id": "B", "stage": "filled"},
        ]
        assert _latest_bridge_stage_by_envelope(records) == {"B": "filled"}


class TestCollectPendingSignals:
    def test_only_accepted_ok_signals(self) -> None:
        envelopes = [
            {"envelope_id": "1", "stage": "accepted", "status": "ok", "message_type": "signal"},
            {"envelope_id": "2", "stage": "parsed", "status": "ok", "message_type": "signal"},
            {
                "envelope_id": "3",
                "stage": "accepted",
                "status": "rejected",
                "message_type": "signal",
            },
            {"envelope_id": "4", "stage": "accepted", "status": "ok", "message_type": "alert"},
        ]
        pending = _collect_pending_signals(envelopes, bridge_stages={})
        assert [e["envelope_id"] for e in pending] == ["1"]

    def test_terminal_bridge_stage_skipped(self) -> None:
        envelopes = [
            {"envelope_id": "A", "stage": "accepted", "status": "ok", "message_type": "signal"},
            {"envelope_id": "B", "stage": "accepted", "status": "ok", "message_type": "signal"},
        ]
        bridge_stages = {"A": "filled", "B": "pending"}
        pending = _collect_pending_signals(envelopes, bridge_stages)
        # A is filled (terminal) → skipped; B is pending (not terminal) → kept
        assert [e["envelope_id"] for e in pending] == ["B"]


# ── run_tick integration tests ──────────────────────────────────────────────


@pytest.fixture
def tmp_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all bridge-side JSONL paths into a temp dir."""
    monkeypatch.setattr(bridge, "_ENVELOPE_LOG", tmp_path / "telegram_message_envelope.jsonl")
    monkeypatch.setattr(bridge, "_BRIDGE_LOG", tmp_path / "bridge_pending_orders.jsonl")
    monkeypatch.setattr(bridge, "_PAPER_AUDIT_LOG", tmp_path / "paper_execution_audit.jsonl")
    # paper_engine writes to its own hard-coded audit path; redirect via env
    # isn't exposed, so we chdir to tmp_path so artifacts/ resolves locally.
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_envelope(path: Path, envelope: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(envelope) + "\n")


def _read_bridge_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _accepted_envelope(
    *,
    envelope_id: str = "env-001",
    source: str = "dashboard",
    timestamp_utc: str | None = None,
    payload_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ts = timestamp_utc or datetime.now(UTC).isoformat()
    payload: dict[str, Any] = {
        "direction": "long",
        "side": "buy",
        "symbol": "BTCUSDT",
        "display_symbol": "BTC/USDT",
        "entry_type": "limit",
        "entry_value": 60000.0,
        "stop_loss": 58000.0,
        "targets": [62000.0, 64000.0],
        "leverage": 5,
        "margin_pct": 5.0,
    }
    if payload_overrides:
        payload.update(payload_overrides)
    return {
        "envelope_id": envelope_id,
        "stage": "accepted",
        "status": "ok",
        "message_type": "signal",
        "source": source,
        "timestamp_utc": ts,
        "payload": payload,
    }


@pytest.mark.asyncio
async def test_fail_closed_when_flag_off(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag off → run_tick is a no-op with enabled=False."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "false")
    # Even with an envelope sitting there, nothing should happen.
    _write_envelope(tmp_artifacts / "telegram_message_envelope.jsonl", _accepted_envelope())

    result = await run_tick()

    assert result.enabled is False
    assert result.envelopes_scanned == 0
    assert not (tmp_artifacts / "bridge_pending_orders.jsonl").exists()


@pytest.mark.asyncio
async def test_source_not_in_allowlist_skipped(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allowlist=dashboard; telegram-sourced envelope → skipped_source."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    _write_envelope(
        tmp_artifacts / "telegram_message_envelope.jsonl",
        _accepted_envelope(envelope_id="env-tg", source="structured_text"),
    )

    result = await run_tick()

    assert result.enabled is True
    assert result.skipped_source == 1
    assert result.filled == 0
    records = _read_bridge_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert len(records) == 1
    assert records[0]["stage"] == "skipped_source"
    assert records[0]["envelope_id"] == "env-tg"


@pytest.mark.asyncio
async def test_short_direction_rejected(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v1: short/sell must be rejected (paper_engine has no short primitive)."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    _write_envelope(
        tmp_artifacts / "telegram_message_envelope.jsonl",
        _accepted_envelope(payload_overrides={"direction": "short", "side": "sell"}),
    )

    result = await run_tick()

    assert result.rejected_short == 1
    records = _read_bridge_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "rejected_short_unsupported"


@pytest.mark.asyncio
async def test_incomplete_envelope_rejected(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing SL → rejected_incomplete, not a crash."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    _write_envelope(
        tmp_artifacts / "telegram_message_envelope.jsonl",
        _accepted_envelope(payload_overrides={"stop_loss": None}),
    )

    result = await run_tick()

    assert result.rejected_incomplete == 1
    records = _read_bridge_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "rejected_incomplete"
    assert "stop_loss" in records[-1]["missing"]


@pytest.mark.asyncio
async def test_ttl_expired(tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Envelope older than TTL → stage=expired, no fill attempt."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_TTL_HOURS", "1")
    old_ts = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    _write_envelope(
        tmp_artifacts / "telegram_message_envelope.jsonl",
        _accepted_envelope(timestamp_utc=old_ts),
    )

    result = await run_tick()

    assert result.expired == 1
    assert result.filled == 0
    records = _read_bridge_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "expired"


@pytest.mark.asyncio
async def test_happy_path_fills(tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Allowlisted dashboard envelope, price in tolerance → filled paper order."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_TTL_HOURS", "24")
    # Bridge hardcodes confidence=1.0 and confluence=99 → default risk gates pass.

    _write_envelope(
        tmp_artifacts / "telegram_message_envelope.jsonl",
        _accepted_envelope(),
    )

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=59950.0)):
        result = await run_tick()

    assert result.filled == 1, result.to_dict()
    records = _read_bridge_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "filled"
    assert records[-1]["symbol"] == "BTC/USDT"
    assert records[-1]["entry_price_target"] == 60000.0
    assert records[-1]["lifecycle_state"] == "POSITION_OPEN"
    assert records[-1]["correlation_id"] == "env-001"
    assert records[-1]["order_intent"]["side"] == "BUY"
    assert records[-1]["order_intent"]["leverage"] == 5.0
    assert records[-1]["order_intent"]["risk_allocation_pct"] == 5.0
    assert records[-1]["order_intent"]["stop_loss"] == 58000.0
    assert records[-1]["order_intent"]["take_profit_targets"] == [62000.0, 64000.0]


@pytest.mark.asyncio
async def test_deduplication_via_terminal_stage(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bridge record with terminal stage prevents re-processing.

    This is the core invariant that protects against double-fills: if the
    envelope appears twice in envelope-jsonl (it shouldn't, but defense-in-depth)
    or if run_tick is called twice in close succession, a prior ``filled``
    record must block a second fill attempt.
    """
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")

    _write_envelope(
        tmp_artifacts / "telegram_message_envelope.jsonl",
        _accepted_envelope(envelope_id="env-dup"),
    )
    # Pre-seed a terminal bridge record as if a prior tick had filled it.
    _write_envelope(
        tmp_artifacts / "bridge_pending_orders.jsonl",
        {"envelope_id": "env-dup", "stage": "filled", "event": "operator_signal_bridge"},
    )

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=59950.0)) as mock_price:
        result = await run_tick()

    assert result.envelopes_scanned == 0
    assert result.filled == 0
    # Price lookup must never happen — terminal stage stops us earlier.
    mock_price.assert_not_called()


@pytest.mark.asyncio
async def test_pending_when_price_outside_tolerance(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Current price above entry + tolerance → pending, no fill, no position."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_ENTRY_TOLERANCE_PCT", "0.5")

    _write_envelope(
        tmp_artifacts / "telegram_message_envelope.jsonl",
        _accepted_envelope(),
    )

    # 60000 entry + 0.5% tol = 60300 ceiling. 61000 is clearly outside.
    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=61000.0)):
        result = await run_tick()

    assert result.filled == 0
    assert result.newly_pending + result.re_pending == 1
    records = _read_bridge_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "pending"
    assert records[-1]["reason"] == "price_outside_tolerance"
    assert records[-1]["lifecycle_state"] == "WAITING_FOR_ENTRY"
    assert records[-1]["audit_reason"] == "entry_not_reached"


@pytest.mark.asyncio
async def test_range_entry_waits_until_price_inside_range(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Range entries are watched as ranges, not midpoint-tolerance shortcuts."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    _write_envelope(
        tmp_artifacts / "telegram_message_envelope.jsonl",
        _accepted_envelope(
            envelope_id="env-range",
            payload_overrides={
                "entry_type": "range",
                "entry_value": None,
                "entry_min": 65000.0,
                "entry_max": 65500.0,
                "stop_loss": 64200.0,
                "targets": [66000.0, 67000.0, 68500.0],
                "leverage": 10,
                "margin_pct": 5.0,
            },
        ),
    )

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=65600.0)):
        result = await run_tick()

    assert result.filled == 0
    assert result.newly_pending == 1
    records = _read_bridge_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "pending"
    assert records[-1]["entry_min"] == 65000.0
    assert records[-1]["entry_max"] == 65500.0

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=65250.0)):
        result = await run_tick()

    assert result.filled == 1, result.to_dict()
    records = _read_bridge_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "filled"
    assert records[-1]["lifecycle_state"] == "POSITION_OPEN"
    assert records[-1]["order_intent"]["entry_type"] == "range"
    assert records[-1]["order_intent"]["entry_min"] == 65000.0
    assert records[-1]["order_intent"]["entry_max"] == 65500.0
    assert records[-1]["order_intent"]["side"] == "BUY"
    assert records[-1]["order_intent"]["leverage"] == 10.0
    assert records[-1]["order_intent"]["risk_allocation_pct"] == 5.0


@pytest.mark.asyncio
async def test_short_signal_maps_to_sell_order_intent_before_paper_reject(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paper still cannot open shorts, but the contract preserves SELL intent."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    _write_envelope(
        tmp_artifacts / "telegram_message_envelope.jsonl",
        _accepted_envelope(
            envelope_id="env-short",
            payload_overrides={
                "direction": "short",
                "side": "sell",
                "entry_value": 60000.0,
                "stop_loss": 62000.0,
                "targets": [58000.0],
            },
        ),
    )

    result = await run_tick()

    assert result.rejected_short == 1
    records = _read_bridge_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "rejected_short_unsupported"
    assert records[-1]["order_intent"]["side"] == "SELL"
    assert records[-1]["audit_reason"] == "paper_short_open_unsupported"


@pytest.mark.asyncio
async def test_rejects_when_position_already_open(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merge-Schutz: existing position in same symbol → reject, no merge."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")

    # Seed a paper_execution_audit.jsonl with a prior BTC fill so rehydrate
    # picks it up as an open position.
    from datetime import datetime

    from app.execution.paper_engine import _AUDIT_LOG  # noqa: PLC0415

    paper_audit = tmp_artifacts / _AUDIT_LOG
    paper_audit.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).isoformat()
    _write_envelope(
        paper_audit,
        {
            "event_type": "order_created",
            "timestamp_utc": ts,
            "order_id": "ord_prior",
            "symbol": "BTC/USDT",
            "side": "buy",
            "stop_loss": 55000.0,
            "take_profit": 70000.0,
        },
    )
    _write_envelope(
        paper_audit,
        {
            "event_type": "order_filled",
            "timestamp_utc": ts,
            "order_id": "ord_prior",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 0.05,
            "fill_price": 59000.0,
            "filled_at": ts,
        },
    )

    _write_envelope(
        tmp_artifacts / "telegram_message_envelope.jsonl",
        _accepted_envelope(envelope_id="env-merge-attempt"),
    )

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=59950.0)) as price:
        result = await run_tick()

    assert result.rejected_position_exists == 1
    assert result.filled == 0
    # Price lookup should not happen — position gate is earlier than market data.
    price.assert_not_called()
    records = _read_bridge_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "rejected_position_exists"
    assert records[-1]["existing_quantity"] == 0.05


def test_detect_scale_factor_recognises_bybit_tick_formats() -> None:
    from app.execution.envelope_to_paper_bridge import _detect_scale_factor

    assert _detect_scale_factor(32450.0, 0.033627) == 1e6  # SWARMS-style
    assert _detect_scale_factor(10310.0, 0.10524) == 1e5  # 1000LUNC-style
    assert _detect_scale_factor(39.5, 39.05) == 1.0  # GIGGLE direct USD
    assert _detect_scale_factor(40.9, 42.063) == 1.0  # HYPE direct USD
    # Pathological: ratio outside any recognised scale → fall through
    assert _detect_scale_factor(100.0, 1.0) == 1.0


def test_apply_scale_rescales_entry_sl_targets_in_place() -> None:
    from app.execution.envelope_to_paper_bridge import _apply_scale

    payload: dict[str, object] = {
        "entry_value": 32450.0,
        "stop_loss": 31150.0,
        "targets": [32610.0, 32775.0, 32935.0, 33099.0],
        "symbol": "SWARMSUSDT",
    }
    _apply_scale(payload, 1e6)
    assert payload["entry_value"] == 32450.0 / 1e6
    assert payload["stop_loss"] == 31150.0 / 1e6
    assert payload["targets"] == [t / 1e6 for t in [32610.0, 32775.0, 32935.0, 33099.0]]
    assert payload["symbol"] == "SWARMSUSDT"  # untouched


def test_apply_scale_noop_when_factor_one() -> None:
    from app.execution.envelope_to_paper_bridge import _apply_scale

    payload: dict[str, object] = {
        "entry_value": 39.5,
        "stop_loss": 37.9,
        "targets": [39.7, 39.9, 40.1, 40.3],
    }
    snapshot = dict(payload)
    snapshot["targets"] = list(payload["targets"])  # type: ignore[arg-type]
    _apply_scale(payload, 1.0)
    assert payload == snapshot


@pytest.mark.asyncio
async def test_result_to_dict_shape() -> None:
    """CLI/cron integration relies on stable key names in to_dict()."""
    r = BridgeTickResult(enabled=True)
    d = r.to_dict()
    required = {
        "enabled",
        "envelopes_scanned",
        "newly_pending",
        "re_pending",
        "filled",
        "expired",
        "skipped_source",
        "rejected_risk",
        "rejected_size",
        "rejected_incomplete",
        "rejected_short",
        "rejected_fill",
        "rejected_position_exists",
        "no_market_data",
        "errors",
    }
    assert required <= set(d.keys())
