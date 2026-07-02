from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.alerts.offline_baseline import build_offline_baseline_report


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


@pytest.mark.asyncio
async def test_offline_baseline_builds_directional_metrics(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "document_id": "doc-1",
                "published_at": "2026-03-20T12:00:00+00:00",
                "priority": 8,
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
            },
            {
                "document_id": "doc-2",
                "published_at": "2026-03-20T12:00:00+00:00",
                "priority": 4,
                "sentiment_label": "bearish",
                "affected_assets": ["ETH"],
            },
        ],
    )

    async def _mock_change(symbol: str, **_: object) -> tuple[float, float, float] | None:
        if symbol == "BTC/USDT":
            return (100.0, 112.0, 12.0)
        if symbol == "ETH/USDT":
            return (100.0, 90.0, -10.0)
        return None

    with patch(
        "app.alerts.offline_baseline.CoinGeckoAdapter.get_price_change_between",
        new=AsyncMock(side_effect=_mock_change),
    ):
        report = await build_offline_baseline_report(
            input_path=dataset,
            threshold_pct=5.0,
            horizon_hours=24,
        )

    assert report["status"] == "ok"
    assert report["input_rows"] == 2
    assert report["candidates"] == 2
    assert report["resolved_candidates"] == 2
    assert report["directional_resolved"] == 2
    assert report["directional_hits"] == 2
    assert report["directional_hit_rate_pct"] == 100.0
    assert report["high_priority_hit_rate_pct"] == 100.0
    assert report["low_priority_hit_rate_pct"] == 100.0


@pytest.mark.asyncio
async def test_offline_baseline_missing_input_is_reported(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jsonl"
    report = await build_offline_baseline_report(input_path=missing)
    assert report["status"] == "missing_or_empty_input"
    assert report["input_rows"] == 0
    assert report["resolved_candidates"] == 0
