"""Unit tests for TV-3 TradingViewSignalEvent normalizer + JSONL emitter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.signals.models import SignalProvenance
from app.signals.tradingview_event import (
    NormalizationError,
    TradingViewSignalEvent,
    append_pending_signal,
    event_to_jsonl_dict,
    normalize_tradingview_payload,
)

_REQUEST_ID = "tvwh_abc123"
_PAYLOAD_HASH = "a" * 64
_RECEIVED_AT = "2026-04-16T12:00:00+00:00"


def _normalize(payload: dict) -> TradingViewSignalEvent:
    return normalize_tradingview_payload(
        payload,
        request_id=_REQUEST_ID,
        payload_hash=_PAYLOAD_HASH,
        received_at=_RECEIVED_AT,
    )


class TestNormalize:
    def test_happy_path_minimal(self) -> None:
        event = _normalize({"ticker": "BTCUSDT", "action": "buy"})
        assert event.ticker == "BTCUSDT"
        assert event.action == "buy"
        assert event.price is None
        assert event.note is None
        assert event.strategy is None
        assert event.source_request_id == _REQUEST_ID
        assert event.source_payload_hash == _PAYLOAD_HASH
        assert event.received_at == _RECEIVED_AT
        assert event.event_id.startswith("tvsig_")
        assert isinstance(event.provenance, SignalProvenance)
        assert event.provenance.source == "tradingview_webhook"
        assert event.provenance.version == "tv-3"
        assert event.provenance.signal_path_id is not None
        assert event.provenance.signal_path_id.startswith("tvpath_")

    def test_happy_path_full(self) -> None:
        event = _normalize(
            {
                "ticker": "ETHUSDT",
                "action": "SELL",  # case-insensitive
                "price": "1800.55",  # stringified numeric
                "note": "  stop hit  ",  # trimmed
                "strategy": "rsi_reversal",
                "time": "ignored-by-normalizer",
            }
        )
        assert event.action == "sell"
        assert event.price == pytest.approx(1800.55)
        assert event.note == "stop hit"
        assert event.strategy == "rsi_reversal"

    def test_action_close_accepted(self) -> None:
        assert _normalize({"ticker": "BTC/USDT", "action": "close"}).action == "close"

    def test_unique_ids_per_call(self) -> None:
        a = _normalize({"ticker": "X", "action": "buy"})
        b = _normalize({"ticker": "X", "action": "buy"})
        assert a.event_id != b.event_id
        assert a.provenance.signal_path_id != b.provenance.signal_path_id

    @pytest.mark.parametrize("missing", ["ticker", "action"])
    def test_required_field_missing(self, missing: str) -> None:
        payload = {"ticker": "X", "action": "buy"}
        payload.pop(missing)
        with pytest.raises(NormalizationError):
            _normalize(payload)

    def test_empty_ticker_rejected(self) -> None:
        with pytest.raises(NormalizationError, match="ticker"):
            _normalize({"ticker": "   ", "action": "buy"})

    def test_unsupported_action_rejected(self) -> None:
        with pytest.raises(NormalizationError, match="action"):
            _normalize({"ticker": "X", "action": "rebalance"})

    def test_non_string_ticker_rejected(self) -> None:
        with pytest.raises(NormalizationError):
            _normalize({"ticker": 42, "action": "buy"})

    def test_bool_price_rejected(self) -> None:
        # bool is a subclass of int — explicit reject
        with pytest.raises(NormalizationError, match="price"):
            _normalize({"ticker": "X", "action": "buy", "price": True})

    def test_negative_price_rejected(self) -> None:
        with pytest.raises(NormalizationError, match="price"):
            _normalize({"ticker": "X", "action": "buy", "price": -1.0})

    def test_zero_price_rejected(self) -> None:
        with pytest.raises(NormalizationError, match="price"):
            _normalize({"ticker": "X", "action": "buy", "price": 0})

    def test_garbage_price_rejected(self) -> None:
        with pytest.raises(NormalizationError, match="price"):
            _normalize({"ticker": "X", "action": "buy", "price": "notanumber"})

    def test_empty_string_price_becomes_none(self) -> None:
        event = _normalize({"ticker": "X", "action": "buy", "price": "   "})
        assert event.price is None

    def test_ticker_too_long(self) -> None:
        with pytest.raises(NormalizationError):
            _normalize({"ticker": "A" * 65, "action": "buy"})

    def test_non_string_note_dropped(self) -> None:
        # Non-string optional fields are silently dropped, not rejected.
        event = _normalize({"ticker": "X", "action": "buy", "note": {"nested": 1}})
        assert event.note is None

    def test_note_truncated_to_max_len(self) -> None:
        event = _normalize({"ticker": "X", "action": "buy", "note": "z" * 2000})
        assert event.note is not None
        assert len(event.note) == 1024

    def test_non_dict_payload_rejected(self) -> None:
        with pytest.raises(NormalizationError):
            normalize_tradingview_payload(
                "not-a-dict",  # type: ignore[arg-type]
                request_id=_REQUEST_ID,
                payload_hash=_PAYLOAD_HASH,
                received_at=_RECEIVED_AT,
            )


class TestSerialization:
    def test_event_to_jsonl_dict_roundtrip(self) -> None:
        event = _normalize({"ticker": "X", "action": "buy", "price": 1.23})
        as_dict = event_to_jsonl_dict(event)
        # Must be JSON-serializable.
        round_tripped = json.loads(json.dumps(as_dict))
        assert round_tripped["ticker"] == "X"
        assert round_tripped["action"] == "buy"
        assert round_tripped["price"] == pytest.approx(1.23)
        # Provenance is flattened by asdict.
        assert round_tripped["provenance"]["source"] == "tradingview_webhook"
        assert round_tripped["provenance"]["version"] == "tv-3"
        assert round_tripped["provenance"]["signal_path_id"].startswith("tvpath_")

    def test_append_pending_signal_writes_one_line(self, tmp_path: Path) -> None:
        out = tmp_path / "sub" / "pending.jsonl"  # exercise mkdir
        event = _normalize({"ticker": "X", "action": "buy"})
        append_pending_signal(out, event)
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["event_id"] == event.event_id
        assert row["provenance"]["version"] == "tv-3"

    def test_append_pending_signal_is_append_only(self, tmp_path: Path) -> None:
        out = tmp_path / "pending.jsonl"
        a = _normalize({"ticker": "A", "action": "buy"})
        b = _normalize({"ticker": "B", "action": "sell"})
        append_pending_signal(out, a)
        append_pending_signal(out, b)
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["ticker"] == "A"
        assert json.loads(lines[1])["ticker"] == "B"
