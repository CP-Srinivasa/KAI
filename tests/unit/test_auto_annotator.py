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
from app.alerts.auto_annotator import (
    _scaled_threshold,
    auto_annotate_pending,
)


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


async def test_skips_too_old(tmp_path: Path) -> None:
    """Alert older than max_age_hours is skipped (stale window)."""
    _write_audit(tmp_path, _make_audit(hours_ago=100.0))

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(
            tmp_path, min_age_hours=6, max_age_hours=48,
        )

    assert results == []
    adapter.get_price_change_between.assert_not_called()


async def test_scaled_threshold_long_window(tmp_path: Path) -> None:
    """At 30h evaluation window, threshold scales to 2% — +1.5% is inconclusive."""
    _write_audit(tmp_path, _make_audit(sentiment="bullish", hours_ago=30.0))

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        # +1.5% over 30h — below scaled threshold of 2%
        adapter.get_price_change_between = AsyncMock(
            return_value=(65000.0, 65975.0, 1.5)
        )

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "inconclusive"


async def test_dedup_by_document_id_ignores_asset(tmp_path: Path) -> None:
    """Already-annotated document_id is skipped even with different asset."""
    _write_audit(tmp_path, _make_audit(doc_id="dup-doc", assets=["ETH/USDT"]))

    # Existing annotation for same doc_id but asset=BTC/USDT
    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    annotation = {
        "document_id": "dup-doc",
        "outcome": "hit",
        "annotated_at": "2026-03-30T12:00:00+00:00",
        "asset": "BTC/USDT",
    }
    outcomes_path.write_text(
        json.dumps(annotation) + "\n", encoding="utf-8",
    )

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter"
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert results == []
    adapter.get_price_change_between.assert_not_called()


# ---------------------------------------------------------------------------
# D-132: Volatility-adaptive thresholds
# ---------------------------------------------------------------------------


def test_scaled_threshold_short_window() -> None:
    """Short window (<=8h) uses 0.7x base."""
    assert _scaled_threshold(6.0, 1.0) == 0.7


def test_scaled_threshold_medium_window() -> None:
    """12h window uses 1.0x base."""
    assert _scaled_threshold(12.0, 1.0) == 1.0


def test_scaled_threshold_24h_window() -> None:
    """24h window uses 1.5x base."""
    assert _scaled_threshold(24.0, 1.0) == 1.5


def test_scaled_threshold_48h_window() -> None:
    """48h window uses 2.0x base."""
    assert _scaled_threshold(48.0, 1.0) == 2.0


def test_scaled_threshold_72h_window() -> None:
    """72h window uses 2.5x base."""
    assert _scaled_threshold(72.0, 1.0) == 2.5


def test_scaled_threshold_low_volatility() -> None:
    """Low volatility (<1%) scales threshold down to 0.6x."""
    result = _scaled_threshold(12.0, 1.0, volatility_24h=0.5)
    assert abs(result - 0.6) < 0.01


def test_scaled_threshold_high_volatility() -> None:
    """High volatility (>3%) scales threshold up."""
    result = _scaled_threshold(12.0, 1.0, volatility_24h=5.0)
    assert result > 1.0
    assert result <= 1.5


def test_scaled_threshold_floor() -> None:
    """Threshold never goes below 0.3%."""
    result = _scaled_threshold(6.0, 0.1, volatility_24h=0.1)
    assert result >= 0.3


# ---------------------------------------------------------------------------
# D-132: Re-evaluation of inconclusive annotations
# ---------------------------------------------------------------------------


async def test_reevals_inconclusive_after_24h(tmp_path: Path) -> None:
    """Inconclusive annotation re-evaluated when alert >24h old."""
    _write_audit(
        tmp_path,
        _make_audit(doc_id="reeval-doc", hours_ago=30.0),
    )

    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    outcomes_path.write_text(
        json.dumps({
            "document_id": "reeval-doc",
            "outcome": "inconclusive",
            "annotated_at": (
                datetime.now(UTC) - timedelta(hours=20)
            ).isoformat(),
        }) + "\n",
        encoding="utf-8",
    )

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter",
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(
            return_value=(65000.0, 67000.0, 3.1),
        )

        results = await auto_annotate_pending(
            tmp_path, min_age_hours=4,
        )

    assert len(results) == 1
    assert results[0].outcome == "hit"
    assert "reeval" in (results[0].note or "")


async def test_skips_reeval_if_disabled(tmp_path: Path) -> None:
    """With reeval_inconclusive=False, inconclusives not retried."""
    _write_audit(
        tmp_path,
        _make_audit(doc_id="no-reeval", hours_ago=30.0),
    )

    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    outcomes_path.write_text(
        json.dumps({
            "document_id": "no-reeval",
            "outcome": "inconclusive",
            "annotated_at": datetime.now(UTC).isoformat(),
        }) + "\n",
        encoding="utf-8",
    )

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter",
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(
            tmp_path,
            min_age_hours=4,
            reeval_inconclusive=False,
        )

    assert results == []


async def test_skips_hit_miss_reeval(tmp_path: Path) -> None:
    """Hit/miss annotations are never re-evaluated."""
    _write_audit(
        tmp_path,
        _make_audit(doc_id="final-doc", hours_ago=30.0),
    )

    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    outcomes_path.write_text(
        json.dumps({
            "document_id": "final-doc",
            "outcome": "hit",
            "annotated_at": datetime.now(UTC).isoformat(),
        }) + "\n",
        encoding="utf-8",
    )

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter",
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(
            tmp_path, min_age_hours=4,
        )

    assert results == []
    adapter.get_price_change_between.assert_not_called()
