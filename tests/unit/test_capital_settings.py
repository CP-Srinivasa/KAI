"""CapitalSettings defaults are inert (ADR 0013, fail-closed).

The settings object EXISTS so the reserve policy is configurable, but defaults to
fully inert and is NOT wired into any consumer yet (no behaviour change). Apply is
gated at the call site (HOTP + edge-validation-gate), never by these flags alone.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.capital_settings import CapitalSettings


def test_defaults_are_fully_inert() -> None:
    s = CapitalSettings(_env_file=None)
    assert s.segmentation_enabled is False
    assert s.apply_enabled is False
    assert s.profit_split_pct == 0.0
    assert s.reserve_target_usd == 0.0
    assert s.long_term_target_usd == 0.0


def test_split_pct_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        CapitalSettings(_env_file=None, profit_split_pct=1.5)
