"""Tests for telegram_channel_parser (Vorschlag B — field-based V2).

Each sample is a verbatim-style message matching variants seen in the premium
channel corpus. Keeping them as raw strings makes regressions easy to spot when
the parser is extended to new layouts.

Variants covered:
  - "Long/Buy #SYM"         header + "Entry Point - X"  (classic)
  - "Long/Buy # SYM"        header with inner space
  - Short direction          ("Short/Sell")
  - "Entry Above - X"       (stop-style trigger)
  - "Enter ABove-X"         (operator-typo variant)
  - "Entry Zone: X – Y"     (range entry)
  - Exchange-prefixed emoji  ("🚀 #SOL/USDT Long/BUY – 84.48")
  - 🎯-list multi-target     (emoji bullseye per line)
  - Exchange scope header    (comma-separated exchange names)
"""

from __future__ import annotations

import pytest

from app.ingestion.telegram_channel_parser import (
    _normalize_symbol,
    _parse_targets_dashlist,
    parse_premium_channel_message,
)

# ── Real-channel corpus ─────────────────────────────────────────────────────

SAMPLE_GUN = """\
Long/Buy #GUN/USDT

Entry Point - 2800

Targets: 2815 - 2830 - 2840 - 2855

Leverage - 10x

Stop Loss - 2680"""

SAMPLE_B3 = """\
Long/Buy # B3/USDT

Entry Point - 3430

Targets: 3447 - 3464 - 3480 - 3500

Leverage - 10x

Stop Loss - 3290"""

SAMPLE_SOL_EMOJI = """\
Binance Futures, OKX, Deribit, BitGET, BybitUSDT, KuCoin, Huobi, Blofin, BingX Futures
🚀 #SOL/USDT Long/BUY – 84.48
🎯 Target:
85.75
🛑 Stop Loss:
83.00
⚡️ Leverage:"""

SAMPLE_BLUR_SHORT = """\
Short/Sell #BLUR/USDT

Entry Point - 0.1820

Targets: 0.1810 - 0.1800 - 0.1790 - 0.1780

Leverage - 10x

Stop Loss - 0.1880"""

SAMPLE_ENTRY_ABOVE = """\
Long/Buy #RECALL/USDT

Entry Above - 0.2350

Targets: 0.2360 - 0.2375 - 0.2390

Leverage - 5x

Stop Loss - 0.2280"""

SAMPLE_ENTER_ABOVE_TYPO = """\
Long/Buy #ARIA/USDT

Enter  ABove- 1.240

Targets: 1.250 - 1.260 - 1.275

Leverage - 10x

Stop Loss - 1.180"""

SAMPLE_BTC_RANGE_EMOJI_TARGETS = """\
Binance Futures, OKX, Bybit
🚀 #BTC/USDT Long/BUY
Entry Zone: 70565 – 70590
🎯 70700
🎯 70850
🎯 71000
🛑 Stop Loss - 69800
⚡️ Leverage: 10x"""


# ── Layout A (classic header + labeled fields) ──────────────────────────────


class TestLayoutClassic:
    def test_gun_full_parse(self) -> None:
        sig = parse_premium_channel_message(SAMPLE_GUN)
        assert sig is not None
        assert sig.symbol == "GUNUSDT"
        assert sig.display_symbol == "GUN/USDT"
        assert sig.direction == "long"
        assert sig.side == "buy"
        assert sig.entry_type == "at"
        assert sig.entry_value == 2800.0
        assert sig.stop_loss == 2680.0
        assert sig.targets == [2815.0, 2830.0, 2840.0, 2855.0]
        assert sig.leverage == 10
        assert sig.exchange_scope == []

    def test_b3_with_inner_space(self) -> None:
        """'# B3/USDT' has a space after # — must still parse."""
        sig = parse_premium_channel_message(SAMPLE_B3)
        assert sig is not None
        assert sig.symbol == "B3USDT"
        assert sig.display_symbol == "B3/USDT"
        assert sig.entry_value == 3430.0
        assert sig.stop_loss == 3290.0
        assert sig.targets == [3447.0, 3464.0, 3480.0, 3500.0]

    def test_short_direction(self) -> None:
        sig = parse_premium_channel_message(SAMPLE_BLUR_SHORT)
        assert sig is not None
        assert sig.direction == "short"
        assert sig.side == "sell"
        assert sig.entry_value == 0.1820
        assert sig.stop_loss == 0.1880
        assert sig.targets == [0.1810, 0.1800, 0.1790, 0.1780]


# ── Entry-variants ──────────────────────────────────────────────────────────


class TestEntryAbove:
    def test_canonical(self) -> None:
        sig = parse_premium_channel_message(SAMPLE_ENTRY_ABOVE)
        assert sig is not None
        assert sig.entry_type == "above"
        assert sig.entry_value == 0.2350
        assert sig.stop_loss == 0.2280

    def test_typo_enter_above(self) -> None:
        """Operator typo 'Enter  ABove-' must still be recognised."""
        sig = parse_premium_channel_message(SAMPLE_ENTER_ABOVE_TYPO)
        assert sig is not None
        assert sig.entry_type == "above"
        assert sig.entry_value == 1.240


class TestEntryRange:
    def test_btc_range_with_emoji_targets(self) -> None:
        sig = parse_premium_channel_message(SAMPLE_BTC_RANGE_EMOJI_TARGETS)
        assert sig is not None
        assert sig.entry_type == "range"
        assert sig.entry_value is None
        assert sig.entry_min == 70565.0
        assert sig.entry_max == 70590.0
        assert sig.stop_loss == 69800.0
        # emoji-list is authoritative when present
        assert sig.targets == [70700.0, 70850.0, 71000.0]
        assert sig.leverage == 10


# ── Exchange-prefix emoji layout ────────────────────────────────────────────


class TestEmojiInlineLayout:
    def test_sol_emoji_full_parse(self) -> None:
        sig = parse_premium_channel_message(SAMPLE_SOL_EMOJI)
        assert sig is not None
        assert sig.symbol == "SOLUSDT"
        assert sig.display_symbol == "SOL/USDT"
        assert sig.direction == "long"
        assert sig.side == "buy"
        assert sig.entry_type == "at"
        assert sig.entry_value == 84.48
        assert sig.stop_loss == 83.0
        # TP1 only — emoji layout ships one target in observed sample
        assert sig.targets == [85.75]
        # Leverage field was blank in the sample → default 1
        assert sig.leverage == 1
        # Exchange header should be recognised
        assert "binance" in sig.exchange_scope
        assert "bybit" in sig.exchange_scope
        assert "okx" in sig.exchange_scope


# ── Non-signal messages (must return None) ──────────────────────────────────


class TestRejectNonSignals:
    def test_empty(self) -> None:
        assert parse_premium_channel_message("") is None
        assert parse_premium_channel_message("   \n\n") is None

    def test_status_update_like(self) -> None:
        assert parse_premium_channel_message("SL moved to BE on GUN/USDT") is None

    def test_prose_only(self) -> None:
        assert (
            parse_premium_channel_message("Good morning traders! Market looking bullish today.")
            is None
        )

    def test_header_but_no_entry_is_rejected(self) -> None:
        """Header without entry-line → incomplete signal."""
        assert parse_premium_channel_message("Long/Buy #ABC/USDT") is None

    def test_header_and_entry_but_no_stop_loss_rejected(self) -> None:
        text = "Long/Buy #ABC/USDT\n\nEntry Point - 100"
        assert parse_premium_channel_message(text) is None

    def test_range_without_both_bounds_rejected(self) -> None:
        text = "Long/Buy #ABC/USDT\nEntry Zone: 100 – \nStop Loss - 90"
        assert parse_premium_channel_message(text) is None


# ── Helper correctness ──────────────────────────────────────────────────────


class TestSymbolNormalize:
    @pytest.mark.parametrize(
        "raw,internal,display",
        [
            ("GUN/USDT", "GUNUSDT", "GUN/USDT"),
            (" B3/USDT", "B3USDT", "B3/USDT"),
            ("#SOL/USDT", "SOLUSDT", "SOL/USDT"),
            ("BTCUSDT", "BTCUSDT", "BTC/USDT"),  # no slash → split on known quote
            ("dogeusdt", "DOGEUSDT", "DOGE/USDT"),
        ],
    )
    def test_variants(self, raw: str, internal: str, display: str) -> None:
        assert _normalize_symbol(raw) == (internal, display)


class TestTargetParsing:
    def test_dash_separated(self) -> None:
        assert _parse_targets_dashlist("2815 - 2830 - 2840 - 2855") == [
            2815.0,
            2830.0,
            2840.0,
            2855.0,
        ]

    def test_en_dash(self) -> None:
        assert _parse_targets_dashlist("100 \u2013 110 \u2013 120") == [
            100.0,
            110.0,
            120.0,
        ]

    def test_skips_empty(self) -> None:
        assert _parse_targets_dashlist("--5-10--") == [5.0, 10.0]


# ── Payload shape ───────────────────────────────────────────────────────────


def test_to_payload_matches_envelope_schema() -> None:
    """The bridge reads exactly these keys from payload — schema contract."""
    sig = parse_premium_channel_message(SAMPLE_GUN)
    assert sig is not None
    payload = sig.to_payload()
    required = {
        "symbol",
        "display_symbol",
        "direction",
        "side",
        "entry_type",
        "entry_value",
        "stop_loss",
        "targets",
        "leverage",
    }
    assert required <= set(payload.keys())
    # bridge expects targets as a plain list of floats
    assert isinstance(payload["targets"], list)
    assert all(isinstance(t, float) for t in payload["targets"])
