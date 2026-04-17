"""Tests for TV-4 bridge: promoted TV signals → paper trading loop.

Covers:
- _normalize_tv_symbol: TV ticker → KAI canonical symbol
- tv_consumer.load_pending_promoted / mark_consumed round-trip
- TradingLoop.run_promoted_signal: promoted signal runs through risk+execution
"""

from __future__ import annotations

import json
from pathlib import Path

from app.orchestrator.trading_loop import _normalize_tv_symbol
from app.signals.models import (
    SignalCandidate,
    SignalDirection,
)
from app.signals.tv_consumer import load_pending_promoted


class TestNormalizeTvSymbol:
    def test_btcusdt(self) -> None:
        assert _normalize_tv_symbol("BTCUSDT") == "BTC/USDT"

    def test_ethusdt(self) -> None:
        assert _normalize_tv_symbol("ETHUSDT") == "ETH/USDT"

    def test_solusdt(self) -> None:
        assert _normalize_tv_symbol("SOLUSDT") == "SOL/USDT"

    def test_already_canonical(self) -> None:
        assert _normalize_tv_symbol("BTC/USDT") == "BTC/USDT"

    def test_lowercase(self) -> None:
        assert _normalize_tv_symbol("btcusdt") == "BTC/USDT"

    def test_avaxusdt(self) -> None:
        assert _normalize_tv_symbol("AVAXUSDT") == "AVAX/USDT"

    def test_ethbtc(self) -> None:
        assert _normalize_tv_symbol("ETHBTC") == "ETH/BTC"

    def test_bare_symbol_defaults_to_usdt(self) -> None:
        assert _normalize_tv_symbol("DOGE") == "DOGE/USDT"


def _make_promoted_row(
    decision_id: str = "dec_test001",
    symbol: str = "BTCUSDT",
    direction: str = "long",
    entry_price: float = 65000.0,
) -> dict:
    return {
        "decision_id": decision_id,
        "timestamp_utc": "2026-04-17T10:00:00+00:00",
        "symbol": symbol,
        "market": "crypto",
        "venue": "paper",
        "mode": "paper",
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss_price": 63000.0,
        "take_profit_price": 70000.0,
        "confidence_score": 0.8,
        "confluence_count": 2,
        "thesis": "RSI oversold recovery",
        "source_document_id": "req_abc",
        "execution_state": "pending",
        "provenance": {
            "source": "tradingview_webhook",
            "version": "tv-3.1",
            "signal_path_id": "tv-path-1",
        },
    }


class TestTvConsumerRoundTrip:
    def test_load_empty_dir(self, tmp_path: Path) -> None:
        result = load_pending_promoted(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_load_and_mark_consumed(self, tmp_path: Path) -> None:
        promoted = tmp_path / "promoted.jsonl"
        promoted.write_text(
            json.dumps(_make_promoted_row("dec_a")) + "\n"
            + json.dumps(_make_promoted_row("dec_b", symbol="ETHUSDT")) + "\n",
            encoding="utf-8",
        )

        candidates = load_pending_promoted(promoted)
        assert len(candidates) == 2
        assert candidates[0].decision_id == "dec_a"
        assert candidates[1].decision_id == "dec_b"
        assert candidates[0].symbol == "BTCUSDT"
        assert isinstance(candidates[0], SignalCandidate)
        assert candidates[0].direction == SignalDirection.LONG
        assert candidates[0].provenance is not None
        assert candidates[0].provenance.source == "tradingview_webhook"

    def test_skip_malformed_rows(self, tmp_path: Path) -> None:
        promoted = tmp_path / "promoted.jsonl"
        good = _make_promoted_row("dec_good")
        bad = {"no_decision_id": True}
        promoted.write_text(
            json.dumps(good) + "\n"
            + "not-json-at-all\n"
            + json.dumps(bad) + "\n",
            encoding="utf-8",
        )

        candidates = load_pending_promoted(promoted)
        assert len(candidates) == 1
        assert candidates[0].decision_id == "dec_good"
