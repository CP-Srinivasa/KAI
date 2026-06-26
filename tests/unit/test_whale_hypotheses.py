"""Whale-flow decider correctness + None-safety.

Each decider must return the theorised side only past the z trigger, 0 inside the
band, and 0 for a None feature (warm-up / no flow) — never a fabricated side.
"""

from __future__ import annotations

from app.analysis.features.feature_matrix import FeatureRow
from app.research.whale_hypotheses import FLOW_Z_TRIGGER, whale_hypotheses


def _row(*, coin_z: float | None = None, stable_z: float | None = None) -> FeatureRow:
    return FeatureRow(
        timestamp_utc="t",
        close=100.0,
        log_return=None,
        rsi_14=None,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
        coin_netflow_z=coin_z,
        stable_netflow_z=stable_z,
    )


_DECIDERS = dict(whale_hypotheses())
_HI = FLOW_Z_TRIGGER + 0.5
_LO = -FLOW_Z_TRIGGER - 0.5


def test_set_has_four_paired_deciders() -> None:
    assert set(_DECIDERS) == {
        "coin_inflow_short",
        "coin_outflow_long",
        "stable_inflow_long",
        "stable_outflow_short",
    }


def test_none_features_never_trade() -> None:
    blank = _row()
    for decide in _DECIDERS.values():
        assert decide(blank) == 0


def test_coin_inflow_short_only_on_high_z() -> None:
    d = _DECIDERS["coin_inflow_short"]
    assert d(_row(coin_z=_HI)) == -1  # heavy inflow → short
    assert d(_row(coin_z=0.0)) == 0  # inside band → no trade
    assert d(_row(coin_z=_LO)) == 0  # outflow is the other decider's job


def test_coin_outflow_long_only_on_low_z() -> None:
    d = _DECIDERS["coin_outflow_long"]
    assert d(_row(coin_z=_LO)) == 1  # heavy outflow → long
    assert d(_row(coin_z=0.0)) == 0
    assert d(_row(coin_z=_HI)) == 0


def test_stable_inflow_long_only_on_high_z() -> None:
    d = _DECIDERS["stable_inflow_long"]
    assert d(_row(stable_z=_HI)) == 1  # dry powder arriving → long
    assert d(_row(stable_z=0.0)) == 0
    assert d(_row(stable_z=_LO)) == 0


def test_stable_outflow_short_only_on_low_z() -> None:
    d = _DECIDERS["stable_outflow_short"]
    assert d(_row(stable_z=_LO)) == -1  # buying power leaving → short
    assert d(_row(stable_z=0.0)) == 0
    assert d(_row(stable_z=_HI)) == 0
