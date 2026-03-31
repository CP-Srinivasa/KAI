"""Tests for the auto-annotation agent (app.alerts.auto_annotator)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.alerts.audit import (
    ALERT_AUDIT_JSONL_FILENAME,
    ALERT_OUTCOMES_JSONL_FILENAME,
    AlertAuditRecord,
    append_alert_audit,
)
from app.alerts.auto_annotator import auto_annotate_pending


def _write_audit(tmp_path: Path, record: AlertAuditRecord) -> None:
    append_alert_audit(record, tmp_path / ALERT_AUDIT_JSONL_FILENAME)


def _make_audit(
    doc_id: str = "doc-1",
    sentiment: str = "bullish",
    assets: list[str] | None = None,
    hours_ago: float = 12.0,
) -> AlertAuditRecord:
    ts = datetime.now(UTC) - timedelta(hours=hours_ago)
    return AlertAuditRecord(
        document_id=doc_id,
        channel="telegram",
        message_id="dry_run",
        is_digest=False,
        dispatched_at=ts.isoformat(),
        sentiment_label=sentiment,
        affected_assets=assets or ["BTC/USDT"],
        directional_eligible=True,
    )


async def test_bullish_price_up_is_hit(tmp_path: Path) -> None:
    """Bullish alert + price went up > threshold = hit."""
    _write_audit(tmp_path, _make_audit(sentiment="bullish"))

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        # pct_change=2.5 means 2.5% up (adapter returns percent)
        adapter.get_price_change_between = AsyncMock(
            return_value=(65000.0, 66625.0, 2.5)
        )

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "hit"
    assert "bullish" in (results[0].note or "")


async def test_bearish_price_down_is_hit(tmp_path: Path) -> None:
    """Bearish alert + price went down > threshold = hit."""
    _write_audit(tmp_path, _make_audit(sentiment="bearish"))

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock(
            return_value=(65000.0, 63700.0, -2.0)
        )

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "hit"


async def test_bearish_price_up_is_miss(tmp_path: Path) -> None:
    """Bearish alert + price went up > threshold = miss."""
    _write_audit(tmp_path, _make_audit(sentiment="bearish"))

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock(
            return_value=(65000.0, 66300.0, 2.0)
        )

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "miss"


async def test_small_move_is_inconclusive(tmp_path: Path) -> None:
    """Move below threshold = inconclusive."""
    _write_audit(tmp_path, _make_audit(sentiment="bullish"))

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock(
            return_value=(65000.0, 65300.0, 0.46)
        )

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "inconclusive"


async def test_skips_already_annotated(tmp_path: Path) -> None:
    """Alert with existing annotation is skipped."""
    _write_audit(tmp_path, _make_audit(doc_id="doc-already"))

    # Write an existing outcome annotation
    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    annotation = {
        "document_id": "doc-already",
        "outcome": "hit",
        "annotated_at": datetime.now(UTC).isoformat(),
        "asset": "BTC/USDT",
    }
    outcomes_path.write_text(json.dumps(annotation) + "\n", encoding="utf-8")

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert results == []
    adapter.get_price_change_between.assert_not_called()


async def test_skips_too_recent(tmp_path: Path) -> None:
    """Alert dispatched less than min_age_hours ago is skipped."""
    _write_audit(tmp_path, _make_audit(hours_ago=2.0))

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert results == []


async def test_skips_non_directional(tmp_path: Path) -> None:
    """Non-directional alert is skipped."""
    rec = AlertAuditRecord(
        document_id="doc-neutral",
        channel="telegram",
        message_id="dry_run",
        is_digest=False,
        dispatched_at=(
            datetime.now(UTC) - timedelta(hours=12)
        ).isoformat(),
        sentiment_label="neutral",
        affected_assets=["BTC/USDT"],
        directional_eligible=False,
    )
    _write_audit(tmp_path, rec)

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert results == []


async def test_dry_run_does_not_write(tmp_path: Path) -> None:
    """In dry_run mode, no annotation file is written."""
    _write_audit(tmp_path, _make_audit())

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock(
            return_value=(65000.0, 66300.0, 2.0)
        )

        results = await auto_annotate_pending(
            tmp_path, min_age_hours=6, dry_run=True,
        )

    assert len(results) == 1
    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    assert not outcomes_path.exists()
