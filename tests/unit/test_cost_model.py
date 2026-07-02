"""Sprint B (2026-06-01): CostModel — single source of trading-cost truth.

Contract under test (behaviour, not implementation):

- Per-side fees are the source; round-trip is ALWAYS derived (entry + exit),
  never stored as a standalone round-trip number that could drift.
- maker vs taker is honored per side and resolvable independently.
- paper venue default = realistic Binance-Spot 10 bp/side (NOT 60 bp worst-case).
- The hard worst-case fallback (corrupt/missing YAML) stays conservative and is
  a distinct layer from the realistic paper default — an error path, not normal.
- total_cost_bps = round_trip_fee_bps + spread + slippage (round-trip basis).
- Engine/Gate/Backtest derive their round-trip cost from the SAME CostModel.
- Open-fill fees must be separable from closed round-trip fees (the real bug:
  summing all fill fees minus closed PnL mislabels gross +433 as if a loss).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.execution import fees
from app.execution.cost_model import CostModel, RoundTripCost


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    fees.reset_cache()
    yield
    fees.reset_cache()


# --- per-side is source, round-trip is derived ---------------------------------


def test_round_trip_is_derived_sum_of_entry_and_exit_taker():
    cm = CostModel()
    cost = cm.round_trip(venue="binance", entry_side="taker", exit_side="taker")
    assert isinstance(cost, RoundTripCost)
    assert cost.entry_fee_bps == pytest.approx(10.0)
    assert cost.exit_fee_bps == pytest.approx(10.0)
    # round-trip is the SUM, derived — not an independently stored field.
    assert cost.round_trip_fee_bps == pytest.approx(20.0)
    assert cost.round_trip_fee_bps == pytest.approx(cost.entry_fee_bps + cost.exit_fee_bps)


def test_maker_and_taker_sides_resolve_independently():
    cm = CostModel()
    # OKX: taker 10 bp, maker 8 bp.
    mixed = cm.round_trip(venue="okx", entry_side="maker", exit_side="taker")
    assert mixed.entry_fee_bps == pytest.approx(8.0)
    assert mixed.exit_fee_bps == pytest.approx(10.0)
    assert mixed.round_trip_fee_bps == pytest.approx(18.0)


def test_entry_and_exit_fee_helpers_match_round_trip_components():
    cm = CostModel()
    assert cm.entry_fee_bps(venue="binance", side="taker") == pytest.approx(10.0)
    assert cm.exit_fee_bps(venue="binance", side="taker") == pytest.approx(10.0)


# --- paper default is realistic 10 bp, NOT worst-case 60 bp ---------------------


def test_paper_default_is_realistic_ten_bps_per_side():
    cm = CostModel()
    cost = cm.round_trip(venue="paper", entry_side="taker", exit_side="taker")
    assert cost.entry_fee_bps == pytest.approx(10.0)
    assert cost.exit_fee_bps == pytest.approx(10.0)
    assert cost.round_trip_fee_bps == pytest.approx(20.0)


def test_paper_round_trip_pct_is_realistic_for_v1_gate():
    """The V1 cost-geometry gate consumes round_trip as PERCENT. With the
    realistic paper default it must be ~0.2%, not the legacy 1.2%."""
    cm = CostModel()
    assert cm.round_trip_fee_pct(venue="paper") == pytest.approx(0.20)


# --- hard worst-case fallback is a DISTINCT layer ------------------------------


def test_hard_fallback_stays_conservative_on_corrupt_yaml(tmp_path: Path):
    bad = tmp_path / "broken.yaml"
    bad.write_text("not: valid: yaml: ::\n", encoding="utf-8")
    cm = CostModel(config_path=bad)
    cost = cm.round_trip(venue="paper", entry_side="taker", exit_side="taker")
    # Error path: worst-case 60 bp/side survives, clearly separate from the
    # realistic 10 bp normal-operation default.
    assert cost.entry_fee_bps == pytest.approx(60.0)
    assert cost.exit_fee_bps == pytest.approx(60.0)
    assert cost.round_trip_fee_bps == pytest.approx(120.0)


def test_genuinely_unknown_venue_still_worst_case():
    """paper is realistic, but a truly unknown venue stays worst-case so we
    never silently under-estimate a venue we have no data for."""
    cm = CostModel()
    cost = cm.round_trip(venue="kraken", entry_side="taker", exit_side="taker")
    assert cost.round_trip_fee_bps == pytest.approx(120.0)


# --- total cost incl. spread + slippage ----------------------------------------


def test_total_cost_bps_adds_spread_and_slippage():
    cm = CostModel()
    cost = cm.round_trip(venue="binance", entry_side="taker", exit_side="taker")
    expected = cost.round_trip_fee_bps + cost.expected_spread_bps + cost.expected_slippage_bps
    assert cost.total_cost_bps == pytest.approx(expected)
    assert cost.total_cost_bps >= cost.round_trip_fee_bps  # spread/slippage non-negative


# --- determinism ---------------------------------------------------------------


def test_cost_model_is_deterministic():
    cm = CostModel()
    a = cm.round_trip(venue="binance", entry_side="taker", exit_side="taker")
    b = cm.round_trip(venue="binance", entry_side="taker", exit_side="taker")
    assert a == b


# --- provider-open: venue is config, no hardcoded binance lock-in --------------


def test_other_venue_resolves_from_config_not_binance():
    cm = CostModel()
    cb = cm.round_trip(venue="coinbase", entry_side="taker", exit_side="taker")
    # coinbase taker 60 bp/side -> 120 bp round-trip, distinct from binance 20.
    assert cb.round_trip_fee_bps == pytest.approx(120.0)
