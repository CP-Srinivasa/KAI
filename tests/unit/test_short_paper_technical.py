"""WP-E (2026-06-15): gated bearish SHORTS on the technical path.

D-142 disabled bearish based on *narrative* (4% precision). WP-E opens bearish
for the technical path ONLY, behind ``allow_short`` (default False), for
eligibility/shadow-measurement only — execution stays gated by entry_mode.
Narrative bearish stays strictly blocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.alerts.eligibility import (
    BLOCK_REASON_BEARISH_DISABLED,
    SIGNAL_PATH_NARRATIVE,
    SIGNAL_PATH_TECHNICAL,
    evaluate_directional_eligibility,
)
from app.market_data.models import OHLCV
from app.observability.technical_screener_feed import run_technical_screen
from app.signals.technical_screener import DEFAULT_LOOKBACK

_GOOD = "SOL/USDT"


# --------------------------------------------------------------------------- #
# Eligibility: allow_short opens bearish ONLY for the technical path.
# --------------------------------------------------------------------------- #


def test_bearish_technical_blocked_by_default() -> None:
    """Default allow_short=False → D-142 still blocks (unchanged)."""
    d = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=[_GOOD],
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=0.6,
    )
    assert d.directional_eligible is False
    assert d.directional_block_reason == BLOCK_REASON_BEARISH_DISABLED


def test_bearish_technical_opened_with_allow_short() -> None:
    d = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=[_GOOD],
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=0.6,
        allow_short=True,
    )
    assert d.directional_eligible is True
    assert d.directional_block_reason is None
    assert d.eligible_assets == [_GOOD]


def test_bearish_narrative_stays_blocked_even_with_allow_short() -> None:
    """allow_short must NOT open the narrative path (D-142 narrative stands)."""
    d = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=[_GOOD],
        signal_path=SIGNAL_PATH_NARRATIVE,
        allow_short=True,
        priority=9,
        actionable=True,
    )
    assert d.directional_eligible is False
    assert d.directional_block_reason == BLOCK_REASON_BEARISH_DISABLED


def test_bullish_technical_unaffected_by_allow_short() -> None:
    d = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=[_GOOD],
        signal_path=SIGNAL_PATH_TECHNICAL,
        technical_strength=0.6,
        allow_short=True,
    )
    assert d.directional_eligible is True


# --------------------------------------------------------------------------- #
# Feed: allow_short admits bearish-technical shadow candidates (audit-logged).
# --------------------------------------------------------------------------- #

_BASE = 100.0


def _series(total_pct: float) -> list[OHLCV]:
    closes = [_BASE] * DEFAULT_LOOKBACK + [_BASE * (1 + total_pct)]
    return [
        OHLCV(
            symbol="X",
            timestamp_utc=f"{i:04d}",
            timeframe="1h",
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1.0,
        )
        for i, c in enumerate(closes)
    ]


class _FakeAdapter:
    def __init__(self, series_by_symbol: dict[str, list[OHLCV]]) -> None:
        self._series = series_by_symbol

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[OHLCV]:
        return self._series.get(symbol, [])


@pytest.mark.asyncio
async def test_feed_admits_short_only_with_flag(tmp_path: Path) -> None:
    adapter = _FakeAdapter(
        {
            "BTC/USDT": _series(0.05),  # BTC up → WEAK underperforms → bearish
            "ADA/USDT": _series(-0.05),
        }
    )

    # Flag OFF → bearish short stays rejected (gate_would_reject True).
    off = tmp_path / "off.jsonl"
    await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "ADA/USDT"],
        min_strength=0.1,
        allow_short=False,
        ledger_path=off,
        now_utc="2026-06-15T00:00:00+00:00",
    )
    rows_off = [json.loads(line) for line in off.read_text(encoding="utf-8").splitlines()]
    weak_off = next(r for r in rows_off if r["symbol"] == "ADA/USDT")
    assert weak_off["side"] == "short"
    assert weak_off["gate_would_reject"] is True

    # Flag ON → bearish short admitted (gate_would_reject False), audit counter set.
    on = tmp_path / "on.jsonl"
    summary = await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "ADA/USDT"],
        min_strength=0.1,
        allow_short=True,
        ledger_path=on,
        now_utc="2026-06-15T00:00:00+00:00",
    )
    rows_on = [json.loads(line) for line in on.read_text(encoding="utf-8").splitlines()]
    weak_on = next(r for r in rows_on if r["symbol"] == "ADA/USDT")
    assert weak_on["side"] == "short"
    assert weak_on["gate_would_reject"] is False
    assert int(summary["shorts_admitted_shadow"]) >= 1  # type: ignore[call-overload]
