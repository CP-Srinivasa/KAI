"""Tests für das pure Eligibility-Entscheidungs-Core."""

from __future__ import annotations

from app.trading.symbol_eligibility import (
    DEFAULT_MIN_HISTORY_DAYS,
    DEFAULT_MIN_TURNOVER_USD,
    EligibilityVerdict,
    SymbolMetrics,
    evaluate_eligibility,
    resolve_duplicates,
)


def _m(symbol: str, turnover: float | None, history: int | None) -> SymbolMetrics:
    base, _, quote = symbol.partition("/")
    return SymbolMetrics(
        symbol=symbol,
        base=base,
        quote=quote,
        turnover_24h_usd=turnover,
        history_days=history,
    )


def test_healthy_symbol_is_eligible() -> None:
    v = evaluate_eligibility(_m("BTC/USDT", 5e8, 365))
    assert isinstance(v, EligibilityVerdict)
    assert v.eligible is True
    assert v.reasons == []


def test_no_canonical_venue_data_when_both_missing() -> None:
    v = evaluate_eligibility(_m("SLX/USDT", None, None))
    assert v.eligible is False
    assert v.reasons == ["no_canonical_venue_data"]


def test_below_min_turnover_is_ineligible() -> None:
    v = evaluate_eligibility(_m("FOO/USDT", 1_000.0, 365))
    assert v.eligible is False
    assert "below_min_turnover" in v.reasons


def test_below_min_history_is_ineligible() -> None:
    v = evaluate_eligibility(_m("NEW/USDT", 5e8, 5))
    assert v.eligible is False
    assert "below_min_history" in v.reasons


def test_partial_missing_data_lists_specific_reason() -> None:
    v = evaluate_eligibility(_m("FOO/USDT", None, 365))
    assert v.eligible is False
    assert v.reasons == ["no_turnover_data"]


def test_duplicate_is_ineligible_with_canonical_reason() -> None:
    v = evaluate_eligibility(_m("BTC/USDC", 5e8, 365), duplicate_of="BTC/USDT")
    assert v.eligible is False
    assert "duplicate_of:BTC/USDT" in v.reasons


def test_duplicate_of_self_is_not_flagged() -> None:
    v = evaluate_eligibility(_m("BTC/USDT", 5e8, 365), duplicate_of="BTC/USDT")
    assert v.eligible is True
    assert v.reasons == []


def test_thresholds_are_parametrised() -> None:
    assert DEFAULT_MIN_TURNOVER_USD == 10_000_000.0
    assert DEFAULT_MIN_HISTORY_DAYS == 30
    v = evaluate_eligibility(_m("FOO/USDT", 2e6, 365), min_turnover_usd=1e6)
    assert v.eligible is True


def test_resolve_prefers_usdt_over_usdc() -> None:
    out = resolve_duplicates(["BTC/USDC", "BTC/USDT"])
    assert out["BTC/USDT"] == "BTC/USDT"
    assert out["BTC/USDC"] == "BTC/USDT"


def test_resolve_prefers_spot_over_perp() -> None:
    out = resolve_duplicates(["BTC/USDT:USDT", "BTC/USDT"])
    assert out["BTC/USDT"] == "BTC/USDT"
    assert out["BTC/USDT:USDT"] == "BTC/USDT"


def test_resolve_keeps_distinct_bases_separate() -> None:
    out = resolve_duplicates(["BTC/USDT", "ETH/USDT"])
    assert out["BTC/USDT"] == "BTC/USDT"
    assert out["ETH/USDT"] == "ETH/USDT"


def test_resolve_single_member_is_canonical() -> None:
    out = resolve_duplicates(["SOL/USDC"])
    assert out["SOL/USDC"] == "SOL/USDC"
