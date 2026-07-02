"""BUG-2 — premium market-data outlier gate (2026-06-08).

Replays the SKYAI 2026-06-07 garbage-spot sequence and asserts the 101.94
outlier is rejected before it can drive scale/validation/PnL.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.market_data.price_sanity import (
    OUTLIER_REASON,
    LastGoodPriceStore,
    evaluate_price_sanity,
    get_last_good_store,
)

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "premium_marketdata_skyai_glitch.json"
)

# SKYAI/USDT real spot lived around $0.356; the feed glitched to $101.94 (286×).
SKYAI_GOOD_A = 0.35609
SKYAI_GOOD_B = 0.35561
SKYAI_GARBAGE = 101.94


def test_first_tick_accepted_best_effort_but_unverified() -> None:
    v = evaluate_price_sanity(symbol="SKYAI/USDT", candidate_price=SKYAI_GOOD_A)
    assert v.ok is True
    assert v.verified is False
    assert v.reference == "none"


def test_good_tick_within_band_is_verified() -> None:
    v = evaluate_price_sanity(
        symbol="SKYAI/USDT", candidate_price=SKYAI_GOOD_B, last_good_price=SKYAI_GOOD_A
    )
    assert v.ok is True
    assert v.verified is True
    assert v.reference == "last_good"


def test_garbage_spot_rejected_against_last_good() -> None:
    v = evaluate_price_sanity(
        symbol="SKYAI/USDT", candidate_price=SKYAI_GARBAGE, last_good_price=SKYAI_GOOD_A
    )
    assert v.ok is False
    assert v.reason == OUTLIER_REASON
    assert v.outlier_score > 100.0  # ~286×


def test_garbage_spot_rejected_against_median() -> None:
    v = evaluate_price_sanity(
        symbol="SKYAI/USDT", candidate_price=SKYAI_GARBAGE, median_price=SKYAI_GOOD_A
    )
    assert v.ok is False
    assert v.reason == OUTLIER_REASON
    assert v.reference == "median"


def test_none_or_nonpositive_is_no_price() -> None:
    for bad in (None, 0.0, -1.0):
        v = evaluate_price_sanity(symbol="X/USDT", candidate_price=bad)
        assert v.ok is False
        assert v.reason == "no_price"


def test_normal_volatility_not_rejected() -> None:
    # 3% move tick-to-tick is real, must pass.
    v = evaluate_price_sanity(symbol="BTC/USDT", candidate_price=60000.0, last_good_price=58300.0)
    assert v.ok is True
    assert v.verified is True


def test_store_only_records_good_prices_for_skyai_replay() -> None:
    store = LastGoodPriceStore()
    sequence: list[float | None] = [SKYAI_GOOD_A, None, SKYAI_GOOD_B, SKYAI_GARBAGE]
    verdicts = []
    for px in sequence:
        last_good = store.get("SKYAI/USDT")
        v = evaluate_price_sanity(
            symbol="SKYAI/USDT", candidate_price=px, last_good_price=last_good
        )
        verdicts.append(v)
        if v.ok and px is not None:
            store.record("SKYAI/USDT", px)

    # tick 0: accepted (first), tick 1: no_price, tick 2: verified good,
    # tick 3 (garbage 101.94): rejected against last-good 0.35561.
    assert verdicts[0].ok is True
    assert verdicts[1].ok is False and verdicts[1].reason == "no_price"
    assert verdicts[2].ok is True and verdicts[2].verified is True
    assert verdicts[3].ok is False and verdicts[3].reason == OUTLIER_REASON
    # the garbage value never became the last-good reference
    assert store.get("SKYAI/USDT") == SKYAI_GOOD_B


def test_module_singleton_store() -> None:
    s1 = get_last_good_store()
    s1.clear()
    s1.record("ETH/USDT", 3500.0)
    assert get_last_good_store().get("ETH/USDT") == 3500.0
    s1.clear()


def test_antigravity_skyai_glitch_fixture_acceptance() -> None:
    """QA acceptance against the Antigravity fixture (not a code source):
    0.356 -> unavailable -> 0.356 -> 101.94 (rejected) -> 0.356 (recovers)."""
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    symbol = data["symbol"]
    store = LastGoodPriceStore()
    verdicts = []
    for tick in data["ticks"]:
        price = tick.get("price") if tick.get("available") and not tick.get("is_stale") else None
        v = evaluate_price_sanity(
            symbol=symbol, candidate_price=price, last_good_price=store.get(symbol)
        )
        if v.ok and price is not None:
            store.record(symbol, price)
        verdicts.append(v)

    oks = [v.ok for v in verdicts]
    # tick0 good, tick1 unavailable->no_price, tick2 good, tick3 garbage rejected,
    # tick4 recovery good again.
    assert oks == [True, False, True, False, True], [(v.ok, v.reason) for v in verdicts]
    assert verdicts[1].reason == "no_price"
    assert verdicts[3].reason == OUTLIER_REASON
    assert verdicts[3].outlier_score > 100.0  # 101.94 vs 0.356
    # the garbage 101.94 never became the reference; recovery judged vs 0.356
    assert store.get(symbol) == 0.356
