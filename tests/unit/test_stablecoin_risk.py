"""Unit tests for the stablecoin risk registry (app/trading/stablecoin_risk.py).

Covers: curated load + vocabulary normalisation, the not-evaluable honesty path
(uncurated / under-curated entries), the unknown-symbol stub, and that the
shipped config loads with the seven risk dimensions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.trading.stablecoin_risk import (
    StablecoinRiskRegistry,
    get_stablecoin_risk_registry,
)

REGISTRY_YML = """
version: 1
stablecoins:
  USDC:
    name: USD Coin
    issuer: Circle
    peg_target: usd
    depeg_risk: low
    reserves_quality: strong
    transparency: attested
    custody_model: regulated_custodian
    regulatory_status: regulated
    liquidity_tier: very_high
    overall_risk_tier: low
  WEIRD:
    name: WeirdCoin
    depeg_risk: catastrophic   # not in vocabulary -> unknown
    reserves_quality: strong
    liquidity_tier: high
    # only 2 known dims -> not evaluable
"""


@pytest.fixture()
def registry(tmp_path: Path) -> StablecoinRiskRegistry:
    p = tmp_path / "stablecoin_risk.yaml"
    p.write_text(REGISTRY_YML, encoding="utf-8")
    return StablecoinRiskRegistry.load(path=p)


def test_curated_entry_loads_all_dimensions(registry: StablecoinRiskRegistry) -> None:
    usdc = registry.get("USDC/USDT")  # pair form normalises to base
    assert usdc is not None
    assert usdc.issuer == "circle"
    assert usdc.peg_target == "USD"
    assert usdc.depeg_risk == "low"
    assert usdc.reserves_quality == "strong"
    assert usdc.regulatory_status == "regulated"
    assert usdc.evaluable is True
    assert usdc.known_dimensions() == 7


def test_out_of_vocab_value_becomes_unknown(registry: StablecoinRiskRegistry) -> None:
    weird = registry.get("WEIRD")
    assert weird is not None
    assert weird.depeg_risk == "unknown"  # "catastrophic" rejected, not honoured
    # 2 known risk dims (reserves_quality, liquidity_tier) < min -> not evaluable
    assert weird.evaluable is False


def test_uncurated_symbol_is_not_evaluable_stub(registry: StablecoinRiskRegistry) -> None:
    stub = registry.assess("FOOUSD")
    assert stub.evaluable is False
    assert stub.overall_risk_tier == "unknown"
    assert stub.known_dimensions() == 0
    assert registry.get("FOOUSD") is None


def test_shipped_config_loads_with_dimensions() -> None:
    reg = get_stablecoin_risk_registry(reload=True)
    usdc = reg.get("USDC")
    assert usdc is not None
    assert usdc.overall_risk_tier in {"low", "medium", "high"}
    # TUSD is intentionally curated as not-evaluable (insufficient disclosure).
    tusd = reg.get("TUSD")
    assert tusd is not None
    assert tusd.overall_risk_tier == "unknown"
    assert tusd.evaluable is False
