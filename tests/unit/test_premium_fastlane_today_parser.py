"""Parser coverage for the four missed 2026-06-05 premium signals (Goal §16).

Pins: "Enter above" → entry_type "above"; "Entry point" → "at"; numeric base
symbol 4/USDT; hyphen target lists; "Stop Loss -" and "Leverage - 10x".
"""

from __future__ import annotations

import pytest

from app.ingestion.telegram_channel_parser import parse_premium_channel_message as parse

TAC = (
    "Long/Buy #TAC/USDT\nEnter above - 19000\n"
    "Targets: 19095 - 19190 - 19285 - 19380\nLeverage - 10x\nStop Loss - 18240"
)
CLO = (
    "Long/Buy #CLO/USDT\nEnter above - 16860\n"
    "Targets: 16945 - 17030 - 17110 - 17195\nLeverage - 10x\nStop Loss - 16185"
)
BEAT = (
    "Long/Buy #BEAT/USDT\nEntry point - 1.6810\n"
    "Targets: 1.6895 - 1.6980 - 1.7060 - 1.7145\nLeverage - 10x\nStop Loss - 1.6130"
)
FOUR = (
    "Long/Buy #4/USDT\nEntry point - 9440\n"
    "Targets: 9485 - 9535 - 9580 - 9630\nLeverage - 10x\nStop Loss - 9060"
)


def test_tac_enter_above() -> None:
    r = parse(TAC)
    assert r is not None
    assert r.display_symbol == "TAC/USDT"
    assert r.direction == "long" and r.side == "buy"
    assert r.entry_type == "above"
    assert r.entry_value == 19000.0
    assert r.stop_loss == 18240.0
    assert r.targets == [19095.0, 19190.0, 19285.0, 19380.0]
    assert r.leverage == 10


def test_clo_enter_above() -> None:
    r = parse(CLO)
    assert r is not None
    assert r.display_symbol == "CLO/USDT"
    assert r.entry_type == "above"
    assert r.entry_value == 16860.0
    assert r.stop_loss == 16185.0
    assert r.targets == [16945.0, 17030.0, 17110.0, 17195.0]


def test_beat_entry_point_decimal() -> None:
    r = parse(BEAT)
    assert r is not None
    assert r.display_symbol == "BEAT/USDT"
    assert r.entry_type == "at"
    assert r.entry_value == pytest.approx(1.6810)
    assert r.stop_loss == pytest.approx(1.6130)
    assert r.targets == pytest.approx([1.6895, 1.6980, 1.7060, 1.7145])


def test_numeric_base_symbol_four() -> None:
    r = parse(FOUR)
    assert r is not None, "numeric base symbol 4/USDT must parse"
    assert r.display_symbol == "4/USDT"
    assert r.symbol == "4USDT"
    assert r.entry_type == "at"
    assert r.entry_value == 9440.0
    assert r.stop_loss == 9060.0
    assert r.targets == [9485.0, 9535.0, 9580.0, 9630.0]
    assert r.leverage == 10
