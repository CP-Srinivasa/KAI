"""Unlock-pressure decider correctness + None-safety."""

from __future__ import annotations

from app.analysis.features.feature_matrix import FeatureRow
from app.research.unlock_hypotheses import UNLOCK_Z_TRIGGER, unlock_hypotheses


def _row(*, unlock_z: float | None = None) -> FeatureRow:
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
        unlock_frac_fwd_z=unlock_z,
    )


_DECIDERS = dict(unlock_hypotheses())
_HI = UNLOCK_Z_TRIGGER + 0.5
_LO = -UNLOCK_Z_TRIGGER - 0.5


def test_set_has_two_paired_deciders() -> None:
    assert set(_DECIDERS) == {"unlock_imminent_short", "unlock_quiet_long"}


def test_none_feature_never_trades() -> None:
    blank = _row()
    for decide in _DECIDERS.values():
        assert decide(blank) == 0


def test_unlock_imminent_short_only_on_high_z() -> None:
    d = _DECIDERS["unlock_imminent_short"]
    assert d(_row(unlock_z=_HI)) == -1  # large imminent unlock → short
    assert d(_row(unlock_z=0.0)) == 0
    assert d(_row(unlock_z=_LO)) == 0


def test_unlock_quiet_long_only_on_low_z() -> None:
    d = _DECIDERS["unlock_quiet_long"]
    assert d(_row(unlock_z=_LO)) == 1  # unusually low overhang → long
    assert d(_row(unlock_z=0.0)) == 0
    assert d(_row(unlock_z=_HI)) == 0
