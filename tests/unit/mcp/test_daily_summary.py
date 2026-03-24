"""Daily operator summary + fail-closed tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import app.agents.tools.canonical_read as _canonical_read_module
from app.agents.mcp_server import get_daily_operator_summary


@pytest.mark.asyncio
async def test_get_daily_operator_summary_aggregates_canonical_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC).isoformat()

    async def fake_readiness(**_kwargs: object) -> dict[str, object]:
        return {"readiness_status": "warning"}

    async def fake_recent_cycles(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "recent_trading_cycles_summary",
            "recent_cycles": [
                {
                    "status": "no_signal",
                    "symbol": "BTC/USDT",
                    "completed_at": now,
                }
            ],
        }

    async def fake_portfolio(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "paper_portfolio_snapshot",
            "position_count": 2,
            "total_equity_usd": 10_000.0,
        }

    async def fake_exposure(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "paper_exposure_summary",
            "gross_exposure_usd": 2_500.0,
            "mark_to_market_status": "ok",
        }

    async def fake_decision_pack(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "operator_decision_pack",
            "overall_status": "warning",
        }

    async def fake_review_journal(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "review_journal_summary",
            "open_count": 3,
        }

    monkeypatch.setattr(
        _canonical_read_module,
        "get_operational_readiness_summary",
        fake_readiness,
    )
    monkeypatch.setattr(_canonical_read_module, "get_recent_trading_cycles", fake_recent_cycles)
    monkeypatch.setattr(_canonical_read_module, "get_paper_portfolio_snapshot", fake_portfolio)
    monkeypatch.setattr(_canonical_read_module, "get_paper_exposure_summary", fake_exposure)
    monkeypatch.setattr(_canonical_read_module, "get_decision_pack_summary", fake_decision_pack)
    monkeypatch.setattr(_canonical_read_module, "get_review_journal_summary", fake_review_journal)

    payload = await get_daily_operator_summary()

    assert payload["report_type"] == "daily_operator_summary"
    assert payload["readiness_status"] == "warning"
    assert payload["cycle_count_today"] == 1
    assert payload["last_cycle_status"] == "no_signal"
    assert payload["last_cycle_symbol"] == "BTC/USDT"
    assert payload["position_count"] == 2
    assert payload["total_exposure_pct"] == 25.0
    assert payload["mark_to_market_status"] == "ok"
    assert payload["decision_pack_status"] == "warning"
    assert payload["open_incidents"] == 3
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert set(payload["sources"]) == {
        "readiness_summary",
        "recent_cycles",
        "portfolio_snapshot",
        "exposure_summary",
        "decision_pack_summary",
        "review_journal_summary",
    }


@pytest.mark.asyncio
async def test_get_daily_operator_summary_degrades_fail_closed_on_surface_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC).isoformat()

    async def failing_readiness(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("readiness unavailable")

    async def fake_recent_cycles(**_kwargs: object) -> dict[str, object]:
        return {"recent_cycles": [{"status": "no_signal", "completed_at": now}]}

    async def fake_portfolio(**_kwargs: object) -> dict[str, object]:
        return {"position_count": 0, "total_equity_usd": 0.0}

    async def fake_exposure(**_kwargs: object) -> dict[str, object]:
        return {"gross_exposure_usd": 0.0, "mark_to_market_status": "unknown"}

    async def fake_decision_pack(**_kwargs: object) -> dict[str, object]:
        return {"overall_status": "clear"}

    async def fake_review_journal(**_kwargs: object) -> dict[str, object]:
        return {"open_count": 0}

    monkeypatch.setattr(
        _canonical_read_module,
        "get_operational_readiness_summary",
        failing_readiness,
    )
    monkeypatch.setattr(_canonical_read_module, "get_recent_trading_cycles", fake_recent_cycles)
    monkeypatch.setattr(_canonical_read_module, "get_paper_portfolio_snapshot", fake_portfolio)
    monkeypatch.setattr(_canonical_read_module, "get_paper_exposure_summary", fake_exposure)
    monkeypatch.setattr(_canonical_read_module, "get_decision_pack_summary", fake_decision_pack)
    monkeypatch.setattr(_canonical_read_module, "get_review_journal_summary", fake_review_journal)

    payload = await get_daily_operator_summary()

    assert payload["report_type"] == "daily_operator_summary"
    assert payload["readiness_status"] == "unknown"
    assert payload["decision_pack_status"] == "clear"
    assert "readiness_summary" not in payload["sources"]
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
