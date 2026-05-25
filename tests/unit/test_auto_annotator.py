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
    _primary_symbol,
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


def test_primary_symbol_pins_bare_asset_to_usdt_pair() -> None:
    rec = _make_audit(assets=["BTC"])

    assert _primary_symbol(rec) == "BTC/USDT"


async def test_bullish_price_up_is_hit(tmp_path: Path) -> None:
    """Bullish alert + price went up > threshold = hit."""
    _write_audit(tmp_path, _make_audit(sentiment="bullish"))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        # pct_change=2.5 means 2.5% up (adapter returns percent)
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 66625.0, 2.5))

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "hit"
    assert "bullish" in (results[0].note or "")


async def test_bearish_price_down_is_hit(tmp_path: Path) -> None:
    """Bearish alert + price went down > threshold = hit."""
    _write_audit(tmp_path, _make_audit(sentiment="bearish"))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 63700.0, -2.0))

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "hit"


async def test_bearish_price_up_is_miss(tmp_path: Path) -> None:
    """Bearish alert + price went up > threshold = miss."""
    _write_audit(tmp_path, _make_audit(sentiment="bearish"))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 66300.0, 2.0))

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "miss"


async def test_small_move_is_inconclusive(tmp_path: Path) -> None:
    """Move below threshold = inconclusive."""
    _write_audit(tmp_path, _make_audit(sentiment="bullish"))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 65300.0, 0.46))

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

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert results == []
    adapter.get_price_change_between.assert_not_called()


async def test_skips_too_recent(tmp_path: Path) -> None:
    """Alert dispatched less than min_age_hours ago is skipped."""
    _write_audit(tmp_path, _make_audit(hours_ago=2.0))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
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
        dispatched_at=(datetime.now(UTC) - timedelta(hours=12)).isoformat(),
        sentiment_label="neutral",
        affected_assets=["BTC/USDT"],
        directional_eligible=False,
    )
    _write_audit(tmp_path, rec)

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert results == []


async def test_dry_run_does_not_write(tmp_path: Path) -> None:
    """In dry_run mode, no annotation file is written."""
    _write_audit(tmp_path, _make_audit())

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 66300.0, 2.0))

        results = await auto_annotate_pending(
            tmp_path,
            min_age_hours=6,
            dry_run=True,
        )

    assert len(results) == 1
    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    assert not outcomes_path.exists()


async def test_skips_too_old(tmp_path: Path) -> None:
    """Alert older than max_age_hours is skipped (stale window)."""
    _write_audit(tmp_path, _make_audit(hours_ago=100.0))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(
            tmp_path,
            min_age_hours=6,
            max_age_hours=48,
        )

    assert results == []
    adapter.get_price_change_between.assert_not_called()


async def test_scaled_threshold_long_window(tmp_path: Path, monkeypatch) -> None:
    """Multi-window: 1.5% in short windows IS a hit (thr=0.7%) at 1h-window.

    2026-05-25 DS-V-MW: With multi-window evaluation, a sustained +1.5%
    crosses the 1h-window threshold (0.7%) and is recorded as hit@1h.
    The legacy single-30h-window interpretation (1.5% < 2.0% thr →
    inconclusive) no longer applies because short windows are now
    checked first and have a lower threshold.
    """
    monkeypatch.setattr("app.alerts.auto_annotator._API_DELAY_SECONDS", 0)
    _write_audit(tmp_path, _make_audit(sentiment="bullish", hours_ago=30.0))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 65975.0, 1.5))

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "hit"
    assert results[0].hit_at_window == "1h"


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
        json.dumps(annotation) + "\n",
        encoding="utf-8",
    )

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
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
        json.dumps(
            {
                "document_id": "reeval-doc",
                "outcome": "inconclusive",
                "annotated_at": (datetime.now(UTC) - timedelta(hours=20)).isoformat(),
            }
        )
        + "\n",
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
            tmp_path,
            min_age_hours=4,
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
        json.dumps(
            {
                "document_id": "no-reeval",
                "outcome": "inconclusive",
                "annotated_at": datetime.now(UTC).isoformat(),
            }
        )
        + "\n",
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
        json.dumps(
            {
                "document_id": "final-doc",
                "outcome": "hit",
                "annotated_at": datetime.now(UTC).isoformat(),
            }
        )
        + "\n",
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
        )

    assert results == []
    adapter.get_price_change_between.assert_not_called()


# ── D-138: Stale inconclusive backfill ──────────────────────────────────


async def test_stale_inconclusive_is_backfilled(tmp_path: Path, monkeypatch) -> None:
    """Stale inconclusive is re-evaluated; multi-window finds hit at 168h.

    2026-05-25 DS-V-MW: With multi-window, "stale" no longer means a single
    fixed 7d eval — each sub-window (1h/4h/24h/72h/168h) measures dispatch
    + N hours. Here we mock small moves in short windows and a 5% move
    only in the 168h window to verify the long-horizon path still works.
    """
    monkeypatch.setattr("app.alerts.auto_annotator._API_DELAY_SECONDS", 0)
    _write_audit(tmp_path, _make_audit(doc_id="stale-doc", hours_ago=240.0))

    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    outcomes_path.write_text(
        json.dumps(
            {
                "document_id": "stale-doc",
                "outcome": "inconclusive",
                "annotated_at": (datetime.now(UTC) - timedelta(hours=200)).isoformat(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        # Quiet short windows, +5% in 168h window only.
        adapter.get_price_change_between = AsyncMock(
            side_effect=[
                (60000.0, 60240.0, 0.4),  # 1h thr=0.7 → no hit
                (60000.0, 60300.0, 0.5),  # 4h thr=0.7 → no hit
                (60000.0, 60600.0, 1.0),  # 24h thr=1.5 → no hit
                (60000.0, 60900.0, 1.5),  # 72h thr=2.5 → no hit
                (60000.0, 63000.0, 5.0),  # 168h thr=2.5 → HIT
            ]
        )

        results = await auto_annotate_pending(
            tmp_path,
            min_age_hours=4,
            backfill_batch=10,
        )

    assert len(results) == 1
    assert results[0].outcome == "hit"
    assert results[0].hit_at_window == "168h"
    assert "backfill" in (results[0].note or "")
    # 5 API calls: all sub-windows traversed because hit lives at the end.
    assert adapter.get_price_change_between.call_count == 5
    # Last call window is 168h.
    last_call = adapter.get_price_change_between.call_args
    end_utc = last_call.kwargs["end_utc"]
    start_utc = last_call.kwargs["start_utc"]
    window_h = (end_utc - start_utc).total_seconds() / 3600
    assert 167 < window_h < 169


async def test_stale_backfill_respects_batch_limit(tmp_path: Path) -> None:
    """Batch limit prevents processing too many stale inconclusives."""
    # 5 stale inconclusives.
    outcomes_lines = []
    for i in range(5):
        _write_audit(
            tmp_path,
            _make_audit(doc_id=f"stale-{i}", hours_ago=200.0 + i),
        )
        outcomes_lines.append(
            json.dumps(
                {
                    "document_id": f"stale-{i}",
                    "outcome": "inconclusive",
                    "annotated_at": (datetime.now(UTC) - timedelta(hours=150)).isoformat(),
                }
            )
        )

    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    outcomes_path.write_text(
        "\n".join(outcomes_lines) + "\n",
        encoding="utf-8",
    )

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter",
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(
            return_value=(60000.0, 63000.0, 5.0),
        )

        results = await auto_annotate_pending(
            tmp_path,
            min_age_hours=4,
            backfill_batch=2,
        )

    # Only 2 of 5 stale inconclusives processed.
    assert len(results) == 2


async def test_never_annotated_beyond_max_age_skipped(tmp_path: Path) -> None:
    """Never-annotated alerts beyond 72h are NOT backfilled (only inconclusives)."""
    _write_audit(
        tmp_path,
        _make_audit(doc_id="old-fresh", hours_ago=200.0),
    )
    # No outcome annotation at all.

    with patch(
        "app.alerts.auto_annotator.CoinGeckoAdapter",
    ) as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock()

        results = await auto_annotate_pending(
            tmp_path,
            min_age_hours=4,
            backfill_batch=10,
        )

    assert results == []
    adapter.get_price_change_between.assert_not_called()


# ---------------------------------------------------------------------------
# 2026-05-25 DS-V-MW: Multi-Window-Outcome
# ---------------------------------------------------------------------------


async def test_multi_window_hit_at_1h_early_exits(tmp_path: Path, monkeypatch) -> None:
    """Bullish alert with +2% in 1h window hits immediately; later windows not called."""
    monkeypatch.setattr("app.alerts.auto_annotator._API_DELAY_SECONDS", 0)
    _write_audit(tmp_path, _make_audit(doc_id="mw-1h", hours_ago=12.0))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 66300.0, 2.0))

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "hit"
    assert results[0].hit_at_window == "1h"
    # Early exit: only 1 API call, not 5.
    assert adapter.get_price_change_between.call_count == 1


async def test_multi_window_hit_at_24h_after_quiet_short_windows(
    tmp_path: Path, monkeypatch
) -> None:
    """1h+4h below threshold, 24h crosses → hit_at_window=24h, 3 API calls."""
    monkeypatch.setattr("app.alerts.auto_annotator._API_DELAY_SECONDS", 0)
    _write_audit(tmp_path, _make_audit(doc_id="mw-24h", sentiment="bullish", hours_ago=30.0))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        # 1h: +0.4% (< 0.7% thr) → no hit. 4h: +0.5% (< 0.7%) → no hit.
        # 24h: +2.0% (>= 1.5% thr) → hit_at_window="24h", break.
        adapter.get_price_change_between = AsyncMock(
            side_effect=[
                (65000.0, 65260.0, 0.4),
                (65000.0, 65325.0, 0.5),
                (65000.0, 66300.0, 2.0),
            ]
        )

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "hit"
    assert results[0].hit_at_window == "24h"
    assert adapter.get_price_change_between.call_count == 3


async def test_multi_window_all_inconclusive_no_hit_no_miss(tmp_path: Path, monkeypatch) -> None:
    """All 5 windows below threshold and same direction → inconclusive, 5 API calls."""
    monkeypatch.setattr("app.alerts.auto_annotator._API_DELAY_SECONDS", 0)
    _write_audit(tmp_path, _make_audit(doc_id="mw-incon", sentiment="bullish", hours_ago=200.0))
    # Seed an inconclusive so the alert qualifies for stale-reeval.
    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    outcomes_path.write_text(
        json.dumps(
            {
                "document_id": "mw-incon",
                "outcome": "inconclusive",
                "annotated_at": (datetime.now(UTC) - timedelta(hours=30)).isoformat(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        # All small positive moves (< respective threshold).
        adapter.get_price_change_between = AsyncMock(
            side_effect=[
                (65000.0, 65195.0, 0.3),  # 1h thr=0.7
                (65000.0, 65260.0, 0.4),  # 4h thr=0.7
                (65000.0, 65650.0, 1.0),  # 24h thr=1.5
                (65000.0, 65975.0, 1.5),  # 72h thr=2.5
                (65000.0, 66300.0, 2.0),  # 168h thr=2.5
            ]
        )

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "inconclusive"
    assert results[0].hit_at_window is None
    assert adapter.get_price_change_between.call_count == 5


async def test_multi_window_miss_via_opposite_cross(tmp_path: Path, monkeypatch) -> None:
    """Bullish alert but price dropped > threshold in any window → miss."""
    monkeypatch.setattr("app.alerts.auto_annotator._API_DELAY_SECONDS", 0)
    _write_audit(tmp_path, _make_audit(doc_id="mw-miss", sentiment="bullish", hours_ago=200.0))
    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    outcomes_path.write_text(
        json.dumps(
            {
                "document_id": "mw-miss",
                "outcome": "inconclusive",
                "annotated_at": (datetime.now(UTC) - timedelta(hours=30)).isoformat(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        # 1h: -1.5% — opposite-direction cross (bullish expected but dropped).
        # 4h: -0.5%, 24h: -1.0%, 72h: -2.0%, 168h: -1.5% — no positive hit ever.
        adapter.get_price_change_between = AsyncMock(
            side_effect=[
                (65000.0, 64025.0, -1.5),
                (65000.0, 64675.0, -0.5),
                (65000.0, 64350.0, -1.0),
                (65000.0, 63700.0, -2.0),
                (65000.0, 64025.0, -1.5),
            ]
        )

        results = await auto_annotate_pending(tmp_path, min_age_hours=6)

    assert len(results) == 1
    assert results[0].outcome == "miss"
    assert results[0].hit_at_window is None


async def test_multi_window_future_windows_skipped_no_api_call(tmp_path: Path, monkeypatch) -> None:
    """Alert 5h old → only 1h+4h windows reachable; 24h/72h/168h skipped without API call."""
    monkeypatch.setattr("app.alerts.auto_annotator._API_DELAY_SECONDS", 0)
    _write_audit(tmp_path, _make_audit(doc_id="mw-fresh", sentiment="bullish", hours_ago=5.0))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        # 1h: +0.4% (< 0.7%), 4h: +0.5% (< 0.7%). 24h/72h/168h not yet elapsed.
        adapter.get_price_change_between = AsyncMock(
            side_effect=[
                (65000.0, 65260.0, 0.4),
                (65000.0, 65325.0, 0.5),
            ]
        )

        results = await auto_annotate_pending(tmp_path, min_age_hours=4)

    assert len(results) == 1
    assert results[0].outcome == "inconclusive"
    # 2 API calls (1h + 4h), not 5 — future windows skip without call.
    assert adapter.get_price_change_between.call_count == 2


async def test_multi_window_hit_serializes_hit_at_window_field(tmp_path: Path, monkeypatch) -> None:
    """Hit annotation persists hit_at_window in JSONL output."""
    monkeypatch.setattr("app.alerts.auto_annotator._API_DELAY_SECONDS", 0)
    _write_audit(tmp_path, _make_audit(doc_id="mw-serialize", sentiment="bullish", hours_ago=12.0))

    with patch("app.alerts.auto_annotator.CoinGeckoAdapter") as mock_cls:
        adapter = mock_cls.return_value
        adapter.get_ticker = AsyncMock(return_value=None)
        adapter.get_price_change_between = AsyncMock(return_value=(65000.0, 66300.0, 2.0))

        await auto_annotate_pending(tmp_path, min_age_hours=6)

    outcomes_path = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    raw = outcomes_path.read_text(encoding="utf-8").strip()
    data = json.loads(raw)
    assert data["outcome"] == "hit"
    assert data["hit_at_window"] == "1h"
