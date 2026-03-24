"""Superseded compatibility shim for legacy portfolio surface imports.

Canonical portfolio/positions/exposure runtime projections live in
`app.execution.portfolio_read`.

This module intentionally exposes no alternate state-replay or PaperPortfolio
projection path to avoid parallel architecture drift.
"""

from __future__ import annotations

from app.execution.portfolio_read import (
    ExposureSummary,
    PortfolioSnapshot,
    PositionSummary,
)


class PortfolioSurfaceSupersededError(RuntimeError):
    """Raised when legacy portfolio_surface builders are called."""


# Legacy type aliases kept for import compatibility in older tests/scripts.
PositionSnapshot = PositionSummary
PortfolioSummary = PortfolioSnapshot


def build_portfolio_summary(*args: object, **kwargs: object) -> PortfolioSnapshot:
    """Superseded legacy shim.

    Use `app.execution.portfolio_read.build_portfolio_snapshot()` and
    `app.execution.portfolio_read.build_positions_summary()` instead.
    """

    raise PortfolioSurfaceSupersededError(
        "portfolio_surface.build_portfolio_summary is superseded; "
        "use app.execution.portfolio_read.build_portfolio_snapshot "
        "and app.execution.portfolio_read.build_positions_summary."
    )


def build_exposure_summary(*args: object, **kwargs: object) -> ExposureSummary:
    """Superseded legacy shim.

    Use `app.execution.portfolio_read.build_exposure_summary()` on a canonical
    `PortfolioSnapshot` instead.
    """

    raise PortfolioSurfaceSupersededError(
        "portfolio_surface.build_exposure_summary is superseded; "
        "use app.execution.portfolio_read.build_exposure_summary."
    )


__all__ = [
    "PortfolioSurfaceSupersededError",
    "PositionSnapshot",
    "PortfolioSummary",
    "ExposureSummary",
    "build_portfolio_summary",
    "build_exposure_summary",
]
