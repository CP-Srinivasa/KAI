"""DS-20260818-MOCK-EXIT: closes booked against the synthetic mock adapter.

The mock is the last link of the live ``fallback`` chain and returns
``is_stale=False`` unconditionally, so a tick on which every real venue failed
handed the position monitor a fabricated price that passed its stale-guard.
These tests pin (a) the bit-exact recognition of such prices, (b) that they are
quarantined on the read side, and (c) that the source is closed so no future
entry or exit can use a synthetic quote.
"""

from __future__ import annotations

import pytest

from app.learning.bayes_quarantine import corruption_reason, quarantine_reason
from app.market_data.base import BaseMarketDataAdapter
from app.market_data.mock_adapter import MockMarketDataAdapter, _mock_price
from app.market_data.mock_price_forensics import (
    is_mock_derived_price,
    match_mock_price,
)
from app.market_data.models import OHLCV, MarketDataPoint, Ticker
from app.market_data.service import _MOCK_SOURCE, FallbackMarketDataAdapter

# The two prices that were independently forensicked from the live paper audit.
# Both are a mock ETH quote with the 0.05% sell-side fill slippage applied.
ETH_AUG_2026 = 3225.6863500000004  # 2026-08-11 23:09:58 and 2026-08-12 23:06:34
ETH_MAY_2026 = 3259.9692  # DS-20260601-EDGE-OUTLIER, phase differs (per-process hash)


class _FakeAdapter(BaseMarketDataAdapter):
    def __init__(self, name: str, point: MarketDataPoint | None) -> None:
        self._name = name
        self._point = point

    @property
    def adapter_name(self) -> str:
        return self._name

    async def get_ticker(self, symbol: str) -> Ticker | None:  # pragma: no cover
        return None

    async def get_ohlcv(  # pragma: no cover
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        return []

    async def get_price(self, symbol: str) -> float | None:  # pragma: no cover
        return self._point.price if self._point else None

    async def get_market_data_point(self, symbol: str) -> MarketDataPoint | None:
        return self._point


def _pt(
    source: str,
    price: float,
    *,
    symbol: str = "ETH/USDT",
    is_stale: bool = False,
) -> MarketDataPoint:
    return MarketDataPoint(
        symbol=symbol,
        timestamp_utc="2026-08-11T23:09:58+00:00",
        price=price,
        volume_24h=1000.0,
        change_pct_24h=0.0,
        source=source,
        is_stale=is_stale,
    )


# --- (a) bit-exact recognition -------------------------------------------------


@pytest.mark.parametrize("price", [ETH_AUG_2026, ETH_MAY_2026])
def test_known_incident_prices_reproduce_bit_exactly(price: float) -> None:
    """Both forensicked ETH exits are mock quotes - matched, not guessed."""
    match = match_mock_price("ETH/USDT", price)
    assert match is not None
    # Bit-exact: reconstructing raw*factor must return the very same float.
    assert match.mock_raw_price * (1.0 + match.slippage_applied) == price
    assert match.slippage_applied == pytest.approx(-0.0005)
    assert 0 <= match.phase < 360


def test_may_and_august_incidents_sit_on_different_phases() -> None:
    """Per-process hash randomization: same code path, two different curves."""
    august = match_mock_price("ETH/USDT", ETH_AUG_2026)
    may = match_mock_price("ETH/USDT", ETH_MAY_2026)
    assert august is not None and may is not None
    assert august.phase != may.phase


def test_recognition_is_deterministic() -> None:
    """Byte-identical inputs must resolve to the byte-identical match."""
    first = match_mock_price("ETH/USDT", ETH_AUG_2026)
    second = match_mock_price("ETH/USDT", ETH_AUG_2026)
    assert first == second


def test_symbol_outside_base_prices_uses_the_100_default() -> None:
    """MKR real ~1288 was stopped out at a ~101 mock price (-92%)."""
    match = match_mock_price("MKR/USDT", 101.62916000000001)
    assert match is not None
    assert 98.0 <= match.mock_raw_price <= 102.0


def test_short_close_buy_side_slippage_is_matched() -> None:
    """A short close fills at price*(1+s); the detector must cover that sign.

    Der Rohwert wird NICHT ueber ``_mock_price`` geholt: dessen Phase haengt an
    ``hash(symbol)`` und ist pro Prozess zufaellig. Faellt sie auf eine
    degenerierte Phase (Basispreis, Amplituden-Extremum), die der Detektor
    bewusst ausschliesst, war der Test zufaellig rot — ~0,8 % je Lauf. Statt
    dessen ein bekannt nicht-degenerierter Wert.
    """
    raw = 3227.3  # mock(ETH/USDT, phase 101), forensisch belegt
    assert match_mock_price("ETH/USDT", raw * 1.0005) is not None


def test_raw_mock_quote_is_not_matched() -> None:
    """paper_engine always applies slippage - a raw value is not an engine close.

    Accepting it flagged round placeholder prices (FB/USDT 101.00) that merely
    sit in the mock's default 98..102 band.
    """
    assert match_mock_price("ETH/USDT", 3227.3) is None
    assert not is_mock_derived_price("FB/USDT", 101.0)


def test_real_venue_quotes_are_not_flagged() -> None:
    """Continuous venue floats must not collide with the 2-decimal mock grid."""
    for symbol, price in (
        ("ETH/USDT", 1874.24956227636),
        ("ETH/USDT", 1880.409735),
        ("BTC/USDT", 76865.43850931706),
        ("SOL/USDT", 77.22900566009066),
        ("MKR/USDT", 1288.48392),
    ):
        assert not is_mock_derived_price(symbol, price), f"{symbol} {price} misflagged"


@pytest.mark.parametrize("bad", [None, "3225.68", True, float("nan"), 0.0, -1.0])
def test_unusable_prices_are_never_flagged(bad: object) -> None:
    assert match_mock_price("ETH/USDT", bad) is None


def test_empty_symbol_is_never_flagged() -> None:
    assert match_mock_price("", ETH_AUG_2026) is None


# --- (b) read-side quarantine ---------------------------------------------------


def test_mock_close_is_quarantined_with_its_own_label() -> None:
    row = {
        "event_type": "position_closed",
        "symbol": "ETH/USDT",
        "entry_price": 1874.24956227636,
        "exit_price": ETH_AUG_2026,
        "position_side": "long",
    }
    assert quarantine_reason(row) == "mock_synthetic_exit_price"
    assert corruption_reason(row) == "mock_synthetic_exit_price"


def test_small_mock_close_under_the_phantom_cap_is_still_caught() -> None:
    """The +2.8% BTC close from the same tick - no magnitude cap can see it."""
    raw = _mock_price("BTC/USDT")
    exit_price = raw * 0.9995
    entry = exit_price / 1.028  # ~ +2.8%, far below any implausibility threshold
    row = {
        "event_type": "position_closed",
        "symbol": "BTC/USDT",
        "entry_price": entry,
        "exit_price": exit_price,
        "position_side": "long",
    }
    assert corruption_reason(row) == "mock_synthetic_exit_price"


def test_clean_close_stays_clean() -> None:
    row = {
        "event_type": "position_closed",
        "symbol": "ETH/USDT",
        "entry_price": 1874.24956227636,
        "exit_price": 1901.3387412,
        "position_side": "long",
    }
    assert corruption_reason(row) is None


# --- (c) the source is closed --------------------------------------------------


@pytest.mark.asyncio
async def test_mock_only_chain_is_tagged_stale() -> None:
    """Every real venue failed - the synthetic point must not be tradeable."""
    chain = FallbackMarketDataAdapter(
        [
            _FakeAdapter("bybit", None),
            _FakeAdapter("binance_futures", None),
            MockMarketDataAdapter(),
        ]
    )
    point = await chain.get_market_data_point("ETH/USDT")
    assert point is not None
    assert point.is_stale is True, "mock price would drive entry AND exit"
    assert "synthetic_not_tradeable" in point.source


@pytest.mark.asyncio
async def test_real_quote_still_wins_and_stays_fresh() -> None:
    """Regression: the mock must not make a real single-venue quote stale."""
    chain = FallbackMarketDataAdapter(
        [
            _FakeAdapter("bybit", _pt("bybit", 1874.25)),
            MockMarketDataAdapter(),
        ]
    )
    point = await chain.get_market_data_point("ETH/USDT")
    assert point is not None
    assert point.source == "bybit"
    assert point.is_stale is False


@pytest.mark.asyncio
async def test_stale_real_quote_is_preferred_over_synthetic() -> None:
    """A stale REAL price is still real - it must outrank the mock."""
    chain = FallbackMarketDataAdapter(
        [
            _FakeAdapter("bybit", _pt("bybit", 1874.25, is_stale=True)),
            MockMarketDataAdapter(),
        ]
    )
    point = await chain.get_market_data_point("ETH/USDT")
    assert point is not None
    assert point.source == "bybit"
    assert _MOCK_SOURCE not in point.source
