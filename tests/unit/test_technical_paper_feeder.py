"""Unit tests for app/observability/technical_paper_feeder.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import app.observability.technical_paper_feeder as feeder
from app.core.settings import AppSettings, TechnicalPaperSettings
from app.orchestrator.models import CycleStatus, LoopCycle


@pytest.mark.asyncio
async def test_feeder_disabled_in_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """When disabled in settings, the feeder should return enabled=False and do nothing."""
    settings = AppSettings()
    settings.technical_paper = TechnicalPaperSettings(enabled=False)
    monkeypatch.setattr(feeder, "get_settings", lambda: settings)

    # If disabled, we should not touch files or loop
    result = await feeder.run_feeder(
        ledger_path=Path("nonexistent_ledger.jsonl"),
        fed_path=Path("nonexistent_fed.jsonl"),
    )
    assert result == {"enabled": False}


@pytest.mark.asyncio
async def test_feeder_filters_and_feeds_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When enabled, the feeder processes and filters candidates correctly:
    - Must be kind='technical'
    - Must be side='long' (LONG-only invariant)
    - Must not have been rejected (gate_would_reject is not True)
    - Must be fresh (<= freshness_max_age_hours)
    - Must not be already fed (idempotency)
    """
    settings = AppSettings()
    settings.technical_paper = TechnicalPaperSettings(
        enabled=True,
        min_strength=0.7,
        freshness_max_age_hours=24,
    )
    monkeypatch.setattr(feeder, "get_settings", lambda: settings)

    now_utc = datetime(2026, 6, 19, 10, 0, 0, tzinfo=UTC)

    # Prepare candidate ledger records
    fresh_time = (now_utc - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    stale_time = (now_utc - timedelta(hours=26)).isoformat().replace("+00:00", "Z")

    candidates = [
        # 1. Non-technical candidate
        {
            "candidate_id": "c1",
            "candidate_kind": "fundamental",
            "symbol": "BTC/USDT",
            "side": "long",
            "ts_utc": fresh_time,
        },
        # 2. Technical, but SHORT side
        {
            "candidate_id": "c2",
            "candidate_kind": "technical",
            "symbol": "ETH/USDT",
            "side": "short",
            "ts_utc": fresh_time,
            "signal_confidence": 0.8,
        },
        # 3. Technical, long, but gate_would_reject is True
        {
            "candidate_id": "c3",
            "candidate_kind": "technical",
            "symbol": "SOL/USDT",
            "side": "long",
            "gate_would_reject": True,
            "ts_utc": fresh_time,
            "signal_confidence": 0.8,
        },
        # 4. Technical, long, not rejected, but stale
        {
            "candidate_id": "c4",
            "candidate_kind": "technical",
            "symbol": "ADA/USDT",
            "side": "long",
            "ts_utc": stale_time,
            "signal_confidence": 0.8,
        },
        # 5. Technical, long, fresh, not rejected, but already fed
        {
            "candidate_id": "c5",
            "candidate_kind": "technical",
            "symbol": "DOT/USDT",
            "side": "long",
            "ts_utc": fresh_time,
            "signal_confidence": 0.8,
        },
        # 6. Valid candidate (lowercase 'long')
        {
            "candidate_id": "c6",
            "candidate_kind": "technical",
            "symbol": "AVAX/USDT",
            "side": "long",
            "ts_utc": fresh_time,
            "signal_confidence": 0.85,
        },
        # 7. Weak candidate (no signal_confidence -> 0.0 strength, below 0.7)
        {
            "candidate_id": "c7",
            "candidate_kind": "technical",
            "symbol": "LINK/USDT",
            "side": "LONG",
            "ts_utc": fresh_time,
        },
    ]

    ledger_file = tmp_path / "shadow_candidate_ledger.jsonl"
    with open(ledger_file, "w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c) + "\n")

    # Already fed list
    fed_file = tmp_path / "technical_paper_fed.jsonl"
    with open(fed_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({"candidate_id": "c5", "fed_at": fresh_time}) + "\n")

    # Mock the trading loop
    seen_runs = []

    async def fake_run_once(*, symbol, mode, analysis_result, analysis_source):
        seen_runs.append(
            {
                "symbol": symbol,
                "mode": mode,
                "analysis_result": analysis_result,
                "analysis_source": analysis_source,
            }
        )
        return LoopCycle(
            cycle_id=f"cyc_{symbol.split('/')[0]}",
            started_at=now_utc.isoformat(),
            completed_at=now_utc.isoformat(),
            symbol=symbol,
            status=CycleStatus.COMPLETED,
            fill_simulated=True,
        )

    with patch(
        "app.observability.technical_paper_feeder.run_trading_loop_once",
        AsyncMock(side_effect=fake_run_once),
    ):
        result = await feeder.run_feeder(
            ledger_path=ledger_file,
            fed_path=fed_file,
            now=now_utc,
        )

    # Check summary return values
    # Total processed: kind == 'technical' is True for c2, c3, c4, c5, c6, c7 (6 candidates)
    assert result["enabled"] is True
    assert result["processed_candidates"] == 6
    assert result["fed"] == 1  # c6
    assert result["skipped_already"] == 1  # c5
    assert result["skipped_short"] == 1  # c2
    assert result["skipped_rejected"] == 1  # c3
    assert result["skipped_stale"] == 1  # c4
    assert result["skipped_weak"] == 1  # c7
    assert result["failed"] == 0

    # Verify trading loop calls
    assert len(seen_runs) == 1
    assert seen_runs[0]["symbol"] == "AVAX/USDT"
    assert seen_runs[0]["mode"] == "paper"
    assert seen_runs[0]["analysis_source"] == "technical_paper"
    assert seen_runs[0]["analysis_result"].sentiment_label.value == "bullish"
    assert seen_runs[0]["analysis_result"].confidence_score == 0.85

    # Verify that fed registry now includes c6 (and c5) but not c7
    fed_ids = feeder.load_fed_ids(fed_file)
    assert fed_ids == {"c5", "c6"}


@pytest.mark.asyncio
async def test_feeder_respects_max_per_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-run cap bounds how many candidates a single tick feeds; the rest
    stay unfed (picked up next tick) — Pi-safe measured ramp, no first-tick burst."""
    settings = AppSettings()
    settings.technical_paper = TechnicalPaperSettings(
        enabled=True, min_strength=0.0, freshness_max_age_hours=48, max_per_run=2
    )
    monkeypatch.setattr(feeder, "get_settings", lambda: settings)

    now_utc = datetime(2026, 6, 19, 10, 0, 0, tzinfo=UTC)
    fresh = (now_utc - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    candidates = [
        {
            "candidate_id": f"e{i}",
            "candidate_kind": "technical",
            "symbol": f"T{i}/USDT",
            "side": "long",
            "ts_utc": fresh,
            "signal_confidence": 0.9,
        }
        for i in range(5)
    ]
    ledger_file = tmp_path / "ledger.jsonl"
    ledger_file.write_text("\n".join(json.dumps(c) for c in candidates) + "\n", encoding="utf-8")
    fed_file = tmp_path / "fed.jsonl"

    seen: list[str] = []

    async def fake_run_once(*, symbol, mode, analysis_result, analysis_source):
        seen.append(symbol)
        return LoopCycle(
            cycle_id=f"cyc_{symbol}",
            started_at=now_utc.isoformat(),
            completed_at=now_utc.isoformat(),
            symbol=symbol,
            status=CycleStatus.COMPLETED,
            fill_simulated=True,
        )

    with patch(
        "app.observability.technical_paper_feeder.run_trading_loop_once",
        AsyncMock(side_effect=fake_run_once),
    ):
        result = await feeder.run_feeder(ledger_path=ledger_file, fed_path=fed_file, now=now_utc)

    assert result["fed"] == 2
    assert result["stopped_at_cap"] is True
    assert len(seen) == 2  # only 2 loop cycles this tick, not all 5
    assert len(feeder.load_fed_ids(fed_file)) == 2  # remaining 3 stay unfed
