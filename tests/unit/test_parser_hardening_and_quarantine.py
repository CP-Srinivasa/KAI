"""Parser hardening (·, SL:, bare leverage).

Grounded in env ENV-TG-001275462917-23879-502ef70a (US/USDT). The replay test
reconstructs the channel signal from the proven envelope payload (entry 0.00833,
SL 0.00798, targets 0.00837/0.008415/0.008455/0.008495, 10x, long).
"""

from __future__ import annotations

import pytest

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
