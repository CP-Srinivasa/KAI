"""Parser hardening (·, SL:, bare leverage) + raw-store + quarantine.

Grounded in env ENV-TG-001275462917-23879-502ef70a (US/USDT). The replay test
reconstructs the channel signal from the proven envelope payload (entry 0.00833,
SL 0.00798, targets 0.00837/0.008415/0.008455/0.008495, 10x, long).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingestion.parser_quarantine import (
    is_signal_like,
    parse_or_quarantine,
    signal_indicators,
    store_raw,
)
from app.ingestion.telegram_channel_parser import parse_premium_channel_message as parse_signal

# --------------------------------------------------------------------------- #
# Parser hardening
# --------------------------------------------------------------------------- #


def test_sl_short_form_is_recognized() -> None:
    txt = "US/USDT LONG 10x\nEntry: 0.00833\nSL: 0.00798\nTargets: 0.00837 / 0.008415"
    r = parse_signal(txt)
    assert r is not None
    assert r.stop_loss == 0.00798


def test_middle_dot_header_is_parsed() -> None:
    txt = (
        "US/USDT · LONG · 10x\nEntry: 0.00833\nSL: 0.00798\n"
        "Targets: 0.00837 / 0.008415 / 0.008455 / 0.008495"
    )
    r = parse_signal(txt)
    assert r is not None
    assert r.symbol == "USUSDT"
    assert r.side == "buy"


def test_bare_leverage_header_form() -> None:
    txt = "US/USDT · LONG · 10x\nEntry: 0.00833\nSL: 0.00798\nTargets: 0.00837"
    r = parse_signal(txt)
    assert r is not None
    assert r.leverage == 10


def test_bare_leverage_does_not_false_match_hex_or_words() -> None:
    # "MAX" / "0x1a" must not be read as leverage; falls back to 1x.
    txt = "BTC/USDT LONG\nEntry: 100\nSL: 95\nNote: MAX risk 0x1a"
    r = parse_signal(txt)
    assert r is not None
    assert r.leverage == 1


@pytest.mark.parametrize("sep", ["·", "•", "|"])
def test_separator_variants(sep: str) -> None:
    txt = f"US/USDT {sep} LONG {sep} 10x\nEntry: 0.00833\nSL: 0.00798\nTargets: 0.00837"
    r = parse_signal(txt)
    assert r is not None
    assert r.symbol == "USUSDT"


# --------------------------------------------------------------------------- #
# Replay: the exact incident signal
# --------------------------------------------------------------------------- #


def test_replay_exact_us_usdt_signal() -> None:
    """Reconstruction of ENV-TG-001275462917-23879-502ef70a from the proven
    envelope payload. The parser must extract every field the bridge later saw."""
    txt = (
        "🎯 US/USDT · LONG · 10x · R/R 1:0.1 · Risk 42.0% · TTL bis 17:53 UTC\n\n"
        "📡 Signal — telegram_premium_channel\n"
        "US/USDT · LONG · 10x\n"
        "Entry: 0.00833\n"
        "SL: 0.00798 (-4.20%)\n"
        "Targets: 0.00837 / 0.008415 / 0.008455 / 0.008495\n"
        "Leverage: 10x\n"
    )
    r = parse_signal(txt)
    assert r is not None
    assert r.symbol == "USUSDT"
    assert r.display_symbol == "US/USDT"
    assert r.direction == "long"
    assert r.side == "buy"
    assert r.entry_value == 0.00833
    assert r.stop_loss == 0.00798
    assert r.targets == [0.00837, 0.008415, 0.008455, 0.008495]
    assert r.leverage == 10
    # raw text preserved verbatim (incl. the middle dots)
    assert "·" in r.raw_text


# --------------------------------------------------------------------------- #
# Quarantine + raw store
# --------------------------------------------------------------------------- #


def test_signal_indicators_and_signal_like() -> None:
    assert is_signal_like("US/USDT LONG Entry 100 SL 95") is True
    assert "direction" in signal_indicators("buy something at 5")
    # plain chatter is not signal-like
    assert is_signal_like("gm everyone, nice pump today!") is False


def test_store_raw_writes_before_parse(tmp_path: Path) -> None:
    inbox = tmp_path / "raw_inbox.jsonl"
    ok = store_raw(
        raw_text="US/USDT LONG ...",
        raw_json={"message_id": 23879},
        source_uid="telegram:-1001275462917:23879",
        message_id=23879,
        inbox_log=inbox,
    )
    assert ok is True
    rec = json.loads(inbox.read_text(encoding="utf-8").strip())
    assert rec["event"] == "telegram_raw_inbox"
    assert rec["message_id"] == 23879
    assert rec["raw_json"]["message_id"] == 23879


def test_good_signal_parses_without_quarantine(tmp_path: Path) -> None:
    q = tmp_path / "quarantine.jsonl"
    txt = "US/USDT LONG 10x\nEntry: 0.00833\nSL: 0.00798\nTargets: 0.00837"
    r = parse_or_quarantine(txt, quarantine_log=q)
    assert r is not None
    assert not q.exists()  # nothing quarantined


def test_signal_like_but_unparseable_is_quarantined_and_alerted(tmp_path: Path) -> None:
    q = tmp_path / "quarantine.jsonl"
    alerts: list[str] = []
    # Looks like a signal (direction + price + keyword) but has NO stop loss ->
    # parser returns None -> must quarantine, not silently drop.
    txt = "DOGE/USDT LONG Entry 0.12 Targets 0.13 0.14 (no stop given)"
    r = parse_or_quarantine(
        txt,
        source_uid="telegram:-100:42",
        message_id=42,
        quarantine_log=q,
        alert_cb=lambda rec: alerts.append(rec.reason_code),
    )
    assert r is None
    assert q.exists()
    rec = json.loads(q.read_text(encoding="utf-8").strip())
    assert rec["event"] == "parser_quarantine"
    assert rec["reason_code"] == "REJECT_BY_PARSER"
    assert rec["alert_emitted"] is True
    assert "direction" in rec["signal_indicators"]
    assert alerts == ["REJECT_BY_PARSER"]


def test_non_signal_text_is_not_quarantined(tmp_path: Path) -> None:
    q = tmp_path / "quarantine.jsonl"
    r = parse_or_quarantine("gm, wen moon?", quarantine_log=q)
    assert r is None
    assert not q.exists()  # no quarantine noise for chatter
