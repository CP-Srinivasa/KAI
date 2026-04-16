"""Unit tests for TV-3.1 operator-promotion logic.

Covers:
    - promote_event happy path with/without RSI enrichment
    - rejection of unsupported actions (close) and missing price
    - confidence_score validation
    - decision-log roundtrip + idempotency (load_decisions + filter_open_events)
    - JSONL append-only semantics
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.signals.models import SignalDirection, SignalProvenance, SignalState
from app.signals.tradingview_event import TradingViewSignalEvent
from app.signals.tradingview_promotion import (
    DecisionRecord,
    PromotionError,
    PromotionInputs,
    append_decision,
    append_promoted_candidate,
    filter_open_events,
    load_decisions,
    load_pending_events,
    promote_event,
)

_NOW_ISO = "2026-04-16T18:00:00+00:00"


def _event(
    *,
    event_id: str = "tvsig_abc",
    action: str = "buy",
    price: float | None = 100.0,
    ticker: str = "BTCUSDT",
    note: str | None = None,
    strategy: str | None = None,
) -> TradingViewSignalEvent:
    return TradingViewSignalEvent(
        event_id=event_id,
        received_at="2026-04-16T17:00:00+00:00",
        ticker=ticker,
        action=action,  # type: ignore[arg-type]
        price=price,
        note=note,
        strategy=strategy,
        source_request_id="tvwh_xyz",
        source_payload_hash="b" * 64,
        provenance=SignalProvenance(
            source="tradingview_webhook",
            version="tv-3",
            signal_path_id="tvpath_zzz",
        ),
    )


class TestPromoteEvent:
    def test_happy_path_long(self) -> None:
        ev = _event(action="buy", price=100.0)
        candidate = promote_event(
            ev, PromotionInputs(thesis="breakout"), now_iso=_NOW_ISO
        )
        assert candidate.symbol == "BTCUSDT"
        assert candidate.direction is SignalDirection.LONG
        assert candidate.entry_price == 100.0
        assert candidate.thesis == "breakout"
        assert candidate.market == "crypto"
        assert candidate.approval_state is SignalState.APPROVED
        assert candidate.execution_state is SignalState.PENDING
        assert candidate.model_version == "tv-3.1"
        assert "tradingview_alert_trigger" in candidate.supporting_factors
        assert "tradingview_webhook" in candidate.data_sources_used
        assert candidate.confluence_count == 1
        assert candidate.provenance.signal_path_id == "tvpath_zzz"
        assert candidate.provenance.version == "tv-3.1"

    def test_happy_path_short(self) -> None:
        ev = _event(action="sell", price=200.0)
        candidate = promote_event(ev, PromotionInputs(thesis="x"))
        assert candidate.direction is SignalDirection.SHORT

    def test_rsi_enrichment_appends_factor_and_data_source(self) -> None:
        ev = _event(action="buy", price=100.0, strategy="rsi_reversal")
        candidate = promote_event(
            ev, PromotionInputs(thesis="x"), rsi_value=27.5, now_iso=_NOW_ISO
        )
        assert any("rsi_14=27.50" in f for f in candidate.supporting_factors)
        assert "binance_ohlcv_rsi" in candidate.data_sources_used
        assert any("strategy:rsi_reversal" in f for f in candidate.supporting_factors)
        assert candidate.confluence_count == 2

    def test_close_action_rejected(self) -> None:
        ev = _event(action="close", price=100.0)
        with pytest.raises(PromotionError, match="cannot be promoted"):
            promote_event(ev, PromotionInputs(thesis="x"))

    def test_missing_price_rejected(self) -> None:
        ev = _event(action="buy", price=None)
        with pytest.raises(PromotionError, match="no price"):
            promote_event(ev, PromotionInputs(thesis="x"))

    def test_invalid_confidence_rejected(self) -> None:
        ev = _event(action="buy", price=100.0)
        with pytest.raises(PromotionError, match="confidence_score"):
            promote_event(ev, PromotionInputs(thesis="x", confidence_score=1.5))

    def test_market_heuristic_unknown_for_non_crypto_suffix(self) -> None:
        ev = _event(ticker="AAPL", action="buy", price=100.0)
        candidate = promote_event(ev, PromotionInputs(thesis="x"))
        assert candidate.market == "unknown"

    def test_note_truncated_to_120_chars(self) -> None:
        long_note = "x" * 500
        ev = _event(action="buy", price=100.0, note=long_note)
        candidate = promote_event(ev, PromotionInputs(thesis="x"))
        note_factor = next(
            f for f in candidate.supporting_factors if f.startswith("note:")
        )
        # "note:" + 120 chars
        assert len(note_factor) == len("note:") + 120


class TestDecisionLogRoundtrip:
    def test_append_and_load_single_decision(self, tmp_path: Path) -> None:
        path = tmp_path / "decisions.jsonl"
        record = DecisionRecord(
            event_id="tvsig_abc",
            decision="promoted",
            timestamp_utc=_NOW_ISO,
            operator_reason="looks good",
            promoted_decision_id="dec_xyz",
        )
        append_decision(path, record)
        loaded = load_decisions(path)
        assert "tvsig_abc" in loaded
        assert loaded["tvsig_abc"].decision == "promoted"
        assert loaded["tvsig_abc"].promoted_decision_id == "dec_xyz"

    def test_load_decisions_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_decisions(tmp_path / "nope.jsonl") == {}

    def test_filter_open_events_excludes_decided(self, tmp_path: Path) -> None:
        events = [_event(event_id="a"), _event(event_id="b"), _event(event_id="c")]
        decisions = {
            "b": DecisionRecord(
                event_id="b",
                decision="rejected",
                timestamp_utc=_NOW_ISO,
                operator_reason="noise",
                promoted_decision_id=None,
            )
        }
        open_events = filter_open_events(events, decisions)
        assert [e.event_id for e in open_events] == ["a", "c"]

    def test_load_decisions_skips_malformed_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "decisions.jsonl"
        path.write_text(
            "{not json}\n"
            '{"event_id":"x","decision":"rejected","timestamp_utc":"t","operator_reason":""}\n'
            "\n",
            encoding="utf-8",
        )
        loaded = load_decisions(path)
        assert list(loaded) == ["x"]


class TestPromotedCandidateAppender:
    def test_append_writes_one_line_with_enum_values(self, tmp_path: Path) -> None:
        path = tmp_path / "promoted.jsonl"
        ev = _event(action="buy", price=100.0)
        candidate = promote_event(ev, PromotionInputs(thesis="x"), now_iso=_NOW_ISO)
        append_promoted_candidate(path, candidate)
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["direction"] == "long"
        assert row["approval_state"] == "approved"
        assert row["execution_state"] == "pending"
        assert row["entry_price"] == 100.0
        assert row["model_version"] == "tv-3.1"


class TestLoadPendingEvents:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_pending_events(tmp_path / "nope.jsonl") == []

    def test_skips_malformed_and_keeps_well_formed(self, tmp_path: Path) -> None:
        path = tmp_path / "pending.jsonl"
        good_row = {
            "event_id": "tvsig_x",
            "received_at": "2026-04-16T17:00:00+00:00",
            "ticker": "BTCUSDT",
            "action": "buy",
            "price": 100.0,
            "note": None,
            "strategy": None,
            "source_request_id": "tvwh_x",
            "source_payload_hash": "h",
            "provenance": {
                "source": "tradingview_webhook",
                "version": "tv-3",
                "signal_path_id": "tvpath_x",
            },
        }
        path.write_text(
            "{not json}\n"
            + json.dumps(good_row)
            + "\n"
            + '{"event_id":"missing_fields"}\n',
            encoding="utf-8",
        )
        events = load_pending_events(path)
        assert [e.event_id for e in events] == ["tvsig_x"]
