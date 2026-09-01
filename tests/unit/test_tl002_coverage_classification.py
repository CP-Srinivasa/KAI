"""STAB-2026-09-01 §25 — TL-002 proof strength must depend on curve coverage.

The old rule was ``95.0 <= fill_price <= 105.0``. That is a coincidence test, not
a corruption test, and its own history says so: three rounds of manual
sight-checks (2026-07-12, 08-02, 08-31) each ended EXPLAINED_FALSE_POSITIVE
because AAVE really does trade in the nineties and SOL really does trade near 100.

The replacement recognises the mock adapter's own deterministic curve bit-exactly.
But a bit-exact hit is not automatically proof of synthetic origin — that depends
on how much of the representable price space the curve occupies:

    BTC/USDT  0.0014   ETH/USDT 0.0277  -> a hit is a fingerprint  (MOCK_SYNTHETIC)
    SOL/USDT  0.4433   AAVE/USDT 0.4975 -> a hit is a coincidence  (REQUIRES_VERIFICATION)

There is no single global threshold that decides corruption on its own, and the
rule publishes its own detection coverage so a reader can weigh the verdict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.market_data.mock_price_forensics import (
    DEFAULT_PAPER_SLIPPAGE_FRACTION,
    HIGH_COVERAGE_THRESHOLD,
    _mock_candidates,
    is_high_coverage_symbol,
    mock_curve_coverage,
    uses_default_base_price,
)
from app.truth.lint import run_lint

BASE_TS = "2026-08-01T09:00:00+00:00"


def _curve_sell_fill(symbol: str, nth: int = 100) -> float:
    """A price the mock adapter provably can emit for ``symbol``."""
    raw, _phase = sorted(_mock_candidates(symbol, 2.0).items())[nth]
    return raw * (1.0 - DEFAULT_PAPER_SLIPPAGE_FRACTION)


def _artifacts(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    art = tmp_path / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    with (art / "paper_execution_audit.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return art


def _tl002(art: Path) -> list[dict[str, Any]]:
    return [v for v in run_lint(art)["violations"] if v["invariant_id"] == "TL-002"]


def _fill(symbol: str, price: float, **over: Any) -> dict[str, Any]:
    row = {
        "event_type": "order_filled",
        "order_id": f"ord_{symbol.replace('/', '')}",
        "symbol": symbol,
        "fill_price": price,
        "timestamp_utc": BASE_TS,
    }
    row.update(over)
    return row


# --------------------------------------------------------------------------
# The band is gone
# --------------------------------------------------------------------------
def test_a_real_price_near_100_is_no_longer_a_finding(tmp_path: Path) -> None:
    """THE regression this section exists for.

    AAVE at 98.769 was flagged for months and cleared by hand every time. It sits
    squarely in the old [95,105] band and is not on the curve.
    """
    art = _artifacts(tmp_path, [_fill("AAVE/USDT", 98.769)])
    assert _tl002(art) == []


def test_prices_across_the_whole_old_band_produce_nothing(tmp_path: Path) -> None:
    rows = [
        _fill("AAVE/USDT", p, order_id=f"ord_{i}")
        for i, p in enumerate([95.0, 97.021, 99.89, 101.0, 104.99])
    ]
    art = _artifacts(tmp_path, rows)
    assert _tl002(art) == []


# --------------------------------------------------------------------------
# POSITIVE CONTROL — a fingerprint symbol
# --------------------------------------------------------------------------
def test_exact_curve_hit_on_a_low_coverage_symbol_is_mock_synthetic(
    tmp_path: Path,
) -> None:
    price = _curve_sell_fill("ETH/USDT")
    art = _artifacts(tmp_path, [_fill("ETH/USDT", price)])
    v = _tl002(art)
    assert len(v) == 1
    assert v[0]["severity"] == "WARNING"
    assert v[0]["evidence"]["classification"] == "MOCK_SYNTHETIC"
    row = v[0]["evidence"]["rows"][0]
    assert row["symbol"] == "ETH/USDT"
    assert row["curve_coverage"] < HIGH_COVERAGE_THRESHOLD


# --------------------------------------------------------------------------
# NEGATIVE CONTROL — a high-coverage / default-base symbol
# --------------------------------------------------------------------------
def test_exact_curve_hit_on_a_default_base_symbol_only_requires_verification(
    tmp_path: Path,
) -> None:
    """A hit here proves little: the curve covers ~50 % of the band."""
    assert uses_default_base_price("AAVE/USDT")
    price = _curve_sell_fill("AAVE/USDT")
    art = _artifacts(tmp_path, [_fill("AAVE/USDT", price)])
    v = _tl002(art)
    assert len(v) == 1
    assert v[0]["evidence"]["classification"] == "REQUIRES_VERIFICATION"
    assert v[0]["severity"] == "INFO"
    assert v[0]["evidence"]["rows"][0]["reason"] == "DEFAULT_BASE_PRICE"


def test_exact_curve_hit_on_a_high_coverage_named_symbol_requires_verification(
    tmp_path: Path,
) -> None:
    """SOL has its own base price but the curve still covers ~44 % of the band."""
    assert not uses_default_base_price("SOL/USDT")
    assert is_high_coverage_symbol("SOL/USDT")
    price = _curve_sell_fill("SOL/USDT")
    art = _artifacts(tmp_path, [_fill("SOL/USDT", price)])
    v = _tl002(art)
    assert len(v) == 1
    assert v[0]["evidence"]["classification"] == "REQUIRES_VERIFICATION"
    assert v[0]["evidence"]["rows"][0]["reason"] == "HIGH_CURVE_COVERAGE"


def test_the_two_classes_are_reported_separately(tmp_path: Path) -> None:
    art = _artifacts(
        tmp_path,
        [
            _fill("ETH/USDT", _curve_sell_fill("ETH/USDT")),
            _fill("AAVE/USDT", _curve_sell_fill("AAVE/USDT")),
        ],
    )
    by_class = {v["evidence"]["classification"]: v for v in _tl002(art)}
    assert set(by_class) == {"MOCK_SYNTHETIC", "REQUIRES_VERIFICATION"}
    assert by_class["MOCK_SYNTHETIC"]["evidence"]["count"] == 1
    assert by_class["REQUIRES_VERIFICATION"]["evidence"]["count"] == 1


# --------------------------------------------------------------------------
# The rule reports its own detection coverage
# --------------------------------------------------------------------------
def test_violation_carries_its_own_detection_coverage(tmp_path: Path) -> None:
    art = _artifacts(
        tmp_path,
        [
            _fill("ETH/USDT", _curve_sell_fill("ETH/USDT")),
            _fill("BTC/USDT", 108_000.0, order_id="ord_btc_clean"),
        ],
    )
    detection = _tl002(art)[0]["evidence"]["detection"]
    assert detection["fills_examined"] == 2
    assert detection["curve_matches"] == 1
    assert "ETH/USDT" in detection["per_symbol_curve_coverage"]
    assert detection["coverage_note"]


def test_coverage_ordering_is_the_documented_one() -> None:
    """Pins the numbers the classification rests on."""
    assert mock_curve_coverage("BTC/USDT") < mock_curve_coverage("ETH/USDT")
    assert mock_curve_coverage("ETH/USDT") < HIGH_COVERAGE_THRESHOLD
    assert mock_curve_coverage("SOL/USDT") >= HIGH_COVERAGE_THRESHOLD
    assert mock_curve_coverage("AAVE/USDT") >= HIGH_COVERAGE_THRESHOLD


# --------------------------------------------------------------------------
# Fail-closed survives the rewrite
# --------------------------------------------------------------------------
def test_a_real_price_source_still_clears_a_curve_hit(tmp_path: Path) -> None:
    price = _curve_sell_fill("ETH/USDT")
    art = _artifacts(
        tmp_path,
        [_fill("ETH/USDT", price, price_source="bybit", market_data_is_stale=False)],
    )
    v = _tl002(art)
    assert v == []


def test_a_stale_real_price_source_does_not_clear_a_curve_hit(tmp_path: Path) -> None:
    price = _curve_sell_fill("ETH/USDT")
    art = _artifacts(
        tmp_path,
        [_fill("ETH/USDT", price, price_source="bybit", market_data_is_stale=True)],
    )
    assert len(_tl002(art)) == 1
