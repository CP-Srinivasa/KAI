"""Legacy shim tests for superseded execution portfolio_surface module."""

from __future__ import annotations

import pytest

import app.execution.portfolio_surface as legacy_surface
from app.execution.portfolio_read import (
    ExposureSummary as CanonicalExposureSummary,
)
from app.execution.portfolio_read import (
    PortfolioSnapshot as CanonicalPortfolioSnapshot,
)
from app.execution.portfolio_read import (
    PositionSummary as CanonicalPositionSummary,
)
from app.execution.portfolio_surface import (
    ExposureSummary,
    PortfolioSummary,
    PortfolioSurfaceSupersededError,
    PositionSnapshot,
    build_exposure_summary,
    build_portfolio_summary,
)


def test_legacy_aliases_point_to_canonical_models() -> None:
    assert PositionSnapshot is CanonicalPositionSummary
    assert PortfolioSummary is CanonicalPortfolioSnapshot
    assert ExposureSummary is CanonicalExposureSummary


def test_build_portfolio_summary_is_superseded_shim() -> None:
    with pytest.raises(PortfolioSurfaceSupersededError, match="superseded"):
        build_portfolio_summary(portfolio=object())


def test_build_exposure_summary_is_superseded_shim() -> None:
    with pytest.raises(PortfolioSurfaceSupersededError, match="superseded"):
        build_exposure_summary(portfolio=object())


def test_legacy_shim_exports_only_compatibility_surface() -> None:
    assert set(legacy_surface.__all__) == {
        "PortfolioSurfaceSupersededError",
        "PositionSnapshot",
        "PortfolioSummary",
        "ExposureSummary",
        "build_portfolio_summary",
        "build_exposure_summary",
    }
