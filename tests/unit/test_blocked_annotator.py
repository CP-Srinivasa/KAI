"""Tests for the blocked-alert auto-annotator (D-148 recall proxy)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.alerts.blocked_annotator import auto_annotate_blocked
from app.alerts.blocked_audit import (
    BlockedAlertRecord,
    BlockedOutcomeAnnotation,
    append_blocked_alert,
    append_blocked_outcome,
    load_blocked_outcomes,
)


def _write_blocked(tmp_path: Path, record: BlockedAlertRecord) -> None:
    append_blocked_alert(record, tmp_path)


def _make_blocked(
    doc_id: str = "doc-1",
    sentiment: str = "bullish",
    assets: list[str] | None = None,
    hours_ago: float = 12.0,
    block_reason: str = "low_precision_source",
) -> BlockedAlertRecord:
    ts = datetime.now(UTC) - timedelta(hours=hours_ago)
    return BlockedAlertRecord(
        document_id=doc_id,
        block_reason=block_reason,
        blocked_at=ts.isoformat(),
        sentiment_label=sentiment,
        blocked_assets=assets or ["BTC/USDT"],
        source_name="unknown",
    )


async def test_bullish_price_up_is_would_have_hit(tmp_path: Path) -> None:
    """Bullish blocked alert + price went up > threshold = hit (= recall loss)."""
    _write_blocked(tmp_path, _make_blocked(sentiment="bullish"))

    with patch("app.alerts.blocked_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 66625.0, 2.5))
        results = await auto_annotate_blocked(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "hit"
    assert "blocked:low_precision_source" in (results[0].note or "")


async def test_bearish_price_down_is_would_have_hit(tmp_path: Path) -> None:
    _write_blocked(tmp_path, _make_blocked(sentiment="bearish"))

    with patch("app.alerts.blocked_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 63700.0, -2.0))
        results = await auto_annotate_blocked(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "hit"


async def test_bullish_price_down_is_correct_block(tmp_path: Path) -> None:
    """Bullish blocked + price went down = miss (= correctly blocked)."""
    _write_blocked(tmp_path, _make_blocked(sentiment="bullish"))

    with patch("app.alerts.blocked_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 63000.0, -3.0))
        results = await auto_annotate_blocked(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "miss"


async def test_flat_price_is_inconclusive(tmp_path: Path) -> None:
    _write_blocked(tmp_path, _make_blocked(sentiment="bullish"))

    with patch("app.alerts.blocked_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 65100.0, 0.15))
        results = await auto_annotate_blocked(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "inconclusive"


async def test_skips_alerts_younger_than_min_age(tmp_path: Path) -> None:
    _write_blocked(tmp_path, _make_blocked(hours_ago=2.0))

    with patch("app.alerts.blocked_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 66625.0, 2.5))
        results = await auto_annotate_blocked(tmp_path, min_age_hours=6)

    assert results == []


async def test_skips_already_resolved_doc(tmp_path: Path) -> None:
    _write_blocked(tmp_path, _make_blocked(doc_id="doc-1"))
    append_blocked_outcome(
        BlockedOutcomeAnnotation(document_id="doc-1", outcome="hit"),
        tmp_path,
    )

    with patch("app.alerts.blocked_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 66625.0, 2.5))
        results = await auto_annotate_blocked(tmp_path, min_age_hours=6)

    assert results == []


async def test_skips_record_without_blocked_assets(tmp_path: Path) -> None:
    rec = _make_blocked()
    # Bypass the default-fallback in _make_blocked by constructing directly.
    rec = BlockedAlertRecord(
        document_id=rec.document_id,
        block_reason=rec.block_reason,
        blocked_at=rec.blocked_at,
        sentiment_label=rec.sentiment_label,
        blocked_assets=[],
        source_name=rec.source_name,
    )
    append_blocked_alert(rec, tmp_path)

    with patch("app.alerts.blocked_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        results = await auto_annotate_blocked(tmp_path, min_age_hours=6)

    assert results == []


async def test_skips_non_directional_sentiment(tmp_path: Path) -> None:
    _write_blocked(tmp_path, _make_blocked(sentiment="neutral"))

    with patch("app.alerts.blocked_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        results = await auto_annotate_blocked(tmp_path, min_age_hours=6)

    assert results == []


async def test_dry_run_does_not_persist(tmp_path: Path) -> None:
    _write_blocked(tmp_path, _make_blocked(sentiment="bullish"))

    with patch("app.alerts.blocked_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 66625.0, 2.5))
        results = await auto_annotate_blocked(tmp_path, min_age_hours=6, dry_run=True)

    assert len(results) == 1
    # Nothing should have been written to blocked_outcomes.jsonl.
    assert load_blocked_outcomes(tmp_path) == []


async def test_price_unavailable_is_skipped(tmp_path: Path) -> None:
    _write_blocked(tmp_path, _make_blocked(sentiment="bullish"))

    with patch("app.alerts.blocked_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(return_value=None)
        results = await auto_annotate_blocked(tmp_path, min_age_hours=6)

    assert results == []
    assert load_blocked_outcomes(tmp_path) == []
