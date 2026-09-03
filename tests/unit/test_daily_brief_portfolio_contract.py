"""STAB-2026-09-01 §24 — the daily brief reads the CANONICAL portfolio.

The brief printed "Paper Portfolio: nicht verfuegbar" while the dashboard showed a
healthy portfolio built from the same audit replay. There was never a second
reader: ``build_portfolio_snapshot`` is the only one. The two consumers differed
in ONE argument. ``build_portfolio_snapshot``'s signature default was the literal
``"coingecko"``; the operator endpoint had been patched (F-05) to pass
``get_settings().market_data_provider`` (``"fallback"``), while
``daily_briefing.py`` called it bare and silently inherited coingecko.

Measured on the Pi at 2026-09-01T08:46Z, one process, one audit file:

    provider=coingecko -> available=False  0 of 6 priced  market_value=0.00
    provider=fallback  -> available=True   6 of 6 priced  market_value=5299.56

These tests pin the contract in both directions: a valid canonical snapshot makes
"unavailable" impossible, and an unavailable one must name a typed cause.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.alerts.daily_briefing import (
    BriefingData,
    build_daily_briefing_with_portfolio,
    classify_portfolio_unavailable,
)
from app.execution.portfolio_read import build_portfolio_snapshot


class _Snapshot:
    """Minimal stand-in for PortfolioSnapshot."""

    def __init__(self, **kw: Any) -> None:
        self.available: bool = kw.get("available", True)
        self.error: str | None = kw.get("error")
        self.cash_usd: float = kw.get("cash_usd", 6021.31)
        self.total_market_value_usd: float = kw.get("total_market_value_usd", 5299.56)
        self.total_equity_usd: float = kw.get("total_equity_usd", 11320.87)
        self.realized_pnl_usd: float = kw.get("realized_pnl_usd", 1702.48)
        self.position_count: int = kw.get("position_count", 6)
        self.positions: list[Any] = kw.get("positions", [])


# --------------------------------------------------------------------------
# The provider default — the actual root cause
# --------------------------------------------------------------------------
def test_bare_snapshot_call_can_no_longer_mean_coingecko() -> None:
    """A bare call must resolve the SYSTEM provider, not a hard-coded venue."""
    sig = inspect.signature(build_portfolio_snapshot)
    default = sig.parameters["provider"].default
    assert default is None, (
        "provider must default to None (resolved to settings.market_data_provider); "
        f"a literal default ({default!r}) makes every bare caller a different population"
    )


@pytest.mark.asyncio
async def test_brief_passes_the_system_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSITIVE CONTROL: the brief asks for the same provider the dashboard uses."""
    seen: dict[str, Any] = {}

    async def fake_snapshot(**kwargs: Any) -> _Snapshot:
        seen.update(kwargs)
        return _Snapshot()

    monkeypatch.setattr("app.execution.portfolio_read.build_portfolio_snapshot", fake_snapshot)
    monkeypatch.setattr(
        "app.alerts.daily_briefing.build_daily_briefing",
        lambda **_: BriefingData(generated_at="2026-09-01T00:00:00Z", lookback_hours=24),
    )

    data = await build_daily_briefing_with_portfolio()

    from app.core.settings import get_settings

    assert seen.get("provider") == get_settings().market_data_provider
    assert data.portfolio_available is True
    assert data.portfolio_status_reason == "PASS"
    assert "nicht verfuegbar" not in data.to_text()


@pytest.mark.asyncio
async def test_valid_canonical_snapshot_makes_unavailable_impossible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CANONICAL_PORTFOLIO_VALID = YES => DAILY_BRIEF_PORTFOLIO_UNAVAILABLE = IMPOSSIBLE."""

    async def fake_snapshot(**_: Any) -> _Snapshot:
        return _Snapshot(available=True)

    monkeypatch.setattr("app.execution.portfolio_read.build_portfolio_snapshot", fake_snapshot)
    monkeypatch.setattr(
        "app.alerts.daily_briefing.build_daily_briefing",
        lambda **_: BriefingData(generated_at="2026-09-01T00:00:00Z", lookback_hours=24),
    )

    text = (await build_daily_briefing_with_portfolio()).to_text()
    assert "Paper Portfolio (live)" in text
    assert "nicht verfuegbar" not in text


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS — unavailable must name a typed cause
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unavailable_snapshot_reports_a_typed_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_snapshot(**_: Any) -> _Snapshot:
        return _Snapshot(
            available=False,
            error="market_data_unavailable_for_open_positions",
            total_market_value_usd=0.0,
        )

    monkeypatch.setattr("app.execution.portfolio_read.build_portfolio_snapshot", fake_snapshot)
    monkeypatch.setattr(
        "app.alerts.daily_briefing.build_daily_briefing",
        lambda **_: BriefingData(generated_at="2026-09-01T00:00:00Z", lookback_hours=24),
    )

    data = await build_daily_briefing_with_portfolio()
    assert data.portfolio_available is False
    assert data.portfolio_status_reason == "MARKET_DATA_UNAVAILABLE"
    text = data.to_text()
    # It may still say unavailable — but never GENERICALLY.
    assert "MARKET_DATA_UNAVAILABLE" in text
    assert "market_data_unavailable_for_open_positions" in text


@pytest.mark.asyncio
async def test_reader_exception_is_typed_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(**_: Any) -> _Snapshot:
        raise FileNotFoundError("artifacts/paper_execution_audit.jsonl")

    monkeypatch.setattr("app.execution.portfolio_read.build_portfolio_snapshot", boom)
    monkeypatch.setattr(
        "app.alerts.daily_briefing.build_daily_briefing",
        lambda **_: BriefingData(generated_at="2026-09-01T00:00:00Z", lookback_hours=24),
    )

    data = await build_daily_briefing_with_portfolio()
    assert data.portfolio_status_reason == "FILE_MISSING"
    assert "FILE_MISSING" in data.to_text()


def test_generic_unavailable_line_is_gone() -> None:
    """No rendering path may emit a bare 'nicht verfuegbar' with no reason."""
    data = BriefingData(generated_at="2026-09-01T00:00:00Z", lookback_hours=24)
    data.portfolio_available = False
    data.portfolio_status_reason = "NO_CANONICAL_SNAPSHOT"
    line = next(line for line in data.to_text().splitlines() if "Paper Portfolio" in line)
    assert line.strip() != "Paper Portfolio: nicht verfuegbar"
    assert "NO_CANONICAL_SNAPSHOT" in line


@pytest.mark.parametrize(
    ("error", "positions", "expected"),
    [
        ("market_data_unavailable_for_open_positions", 6, "MARKET_DATA_UNAVAILABLE"),
        ("epoch mismatch: pre-reset state", 3, "EPOCH_MISMATCH"),
        ("replay aborted at line 12", 3, "REPLAY_FAILED"),
        ("no such file or directory", 0, "FILE_MISSING"),
        ("schema invalid: missing side", 1, "SCHEMA_INVALID"),
        ("", 0, "NO_POSITIONS"),
        ("", 4, "NO_CANONICAL_SNAPSHOT"),
        ("something nobody mapped yet", 2, "NO_CANONICAL_SNAPSHOT"),
    ],
)
def test_classifier_never_returns_an_untyped_blank(
    error: str, positions: int, expected: str
) -> None:
    snap = _Snapshot(available=False, error=error or None, position_count=positions)
    assert classify_portfolio_unavailable(snap) == expected
