"""Unit tests for app.execution.fees (NEO-P-106, V14)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.execution import fees


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    fees.reset_cache()
    yield
    fees.reset_cache()


def test_lookup_known_venue_binance_returns_yaml_taker():
    f = fees.lookup_taker_fee("binance")
    assert f.venue == "binance"
    assert f.role == "taker"
    assert f.bps_applied == pytest.approx(10.0)
    assert f.table_version != "fallback"


def test_lookup_normalizes_case_and_whitespace():
    f = fees.lookup_taker_fee("  Binance  ")
    assert f.venue == "binance"


def test_lookup_unknown_venue_uses_default_taker():
    f = fees.lookup_taker_fee("kraken")
    # default_taker_pct in YAML is 0.60 (worst-case) for GENUINELY unknown
    # venues — unchanged by Sprint B. Only the explicit `paper` entry is now
    # realistic; a venue with no YAML row still gets the conservative default.
    assert f.bps_applied == pytest.approx(60.0)
    assert f.venue == "kraken"


def test_lookup_paper_uses_realistic_default():
    # Sprint B (CostModel): paper is now an explicit YAML venue = Binance-Spot
    # 10 bp/side, NOT the 60 bp worst-case. Operator decision 2026-06-01.
    f = fees.lookup_taker_fee("paper")
    assert f.bps_applied == pytest.approx(10.0)


def test_lookup_empty_venue_falls_back_to_paper_default():
    # empty venue -> "paper" -> realistic 10 bp (paper has an explicit entry).
    f = fees.lookup_taker_fee("")
    assert f.venue == "paper"
    assert f.bps_applied == pytest.approx(10.0)


def test_lookup_corrupt_config_falls_back_hard(tmp_path: Path):
    bad = tmp_path / "broken.yaml"
    bad.write_text("not: valid: yaml: at: all: ::\n", encoding="utf-8")
    f = fees.lookup_taker_fee("binance", config_path=bad)
    # Hard fallback path: 0.60 (worst-case)
    assert f.bps_applied == pytest.approx(60.0)
    assert f.table_version == "fallback"


def test_lookup_missing_config_falls_back_hard(tmp_path: Path):
    missing = tmp_path / "does_not_exist.yaml"
    f = fees.lookup_taker_fee("binance", config_path=missing)
    assert f.bps_applied == pytest.approx(60.0)
    assert f.table_version == "fallback"


def test_lookup_coinbase_is_higher_than_binance():
    """Smoke: ehrliche Fee-Differenz zwischen Venues sollte sichtbar sein."""
    binance = fees.lookup_taker_fee("binance")
    coinbase = fees.lookup_taker_fee("coinbase")
    assert coinbase.bps_applied > binance.bps_applied


def test_lookup_maker_fee_uses_yaml_maker_rate():
    f = fees.lookup_fee("okx", "maker")
    assert f.venue == "okx"
    assert f.role == "maker"
    assert f.bps_applied == pytest.approx(8.0)


def test_lookup_invalid_role_falls_back_to_taker():
    f = fees.lookup_fee("okx", "post_onlyish")
    assert f.role == "taker"
    assert f.bps_applied == pytest.approx(10.0)


def test_lookup_unknown_venue_uses_default_maker():
    f = fees.lookup_fee("kraken", "maker")
    assert f.venue == "kraken"
    assert f.role == "maker"
    assert f.bps_applied == pytest.approx(60.0)


@pytest.mark.parametrize(
    ("order_type", "limit_price", "expected"),
    [
        ("market", None, "taker"),
        ("limit", None, "taker"),
        ("limit", 100.0, "maker"),
        (" LIMIT ", 100.0, "maker"),
    ],
)
def test_infer_fee_role(order_type, limit_price, expected):
    assert fees.infer_fee_role(order_type, limit_price) == expected


def test_lookup_order_fee_uses_limit_with_price_as_maker():
    f = fees.lookup_order_fee("okx", order_type="limit", limit_price=100.0)
    assert f.role == "maker"
    assert f.bps_applied == pytest.approx(8.0)


def test_yaml_table_has_required_metadata():
    """Die ausgelieferte config/venue_fees.yaml hat die Pflichtfelder."""
    table = fees._load_table()
    assert "version" in table
    assert "effective_from" in table
    assert "effective_until" in table
    assert isinstance(table.get("venues"), dict)
    assert len(table["venues"]) >= 4  # binance, okx, coinbase, bybit
