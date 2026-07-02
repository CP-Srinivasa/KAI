"""Intake-reject tombstones with cooldown (pure, file-based)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.learning.source_reject_tombstone import (
    append_rejection_tombstone,
    load_active_rejections,
)

_NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


def test_append_then_load_returns_url_and_domain_within_cooldown(tmp_path) -> None:
    path = tmp_path / "rejected.jsonl"
    assert append_rejection_tombstone(
        path, url="https://Paywall.com/feed/", reason="access_rejected: paywall", now=_NOW
    )
    active = load_active_rejections(path, _NOW + timedelta(days=1))
    # both the exact normalized URL and the bare domain are skippable keys
    assert "https://paywall.com/feed" in active
    assert "paywall.com" in active


def test_expired_tombstone_is_ignored(tmp_path) -> None:
    path = tmp_path / "rejected.jsonl"
    append_rejection_tombstone(
        path, url="https://stale.com/x", reason="captcha", now=_NOW, cooldown_days=30
    )
    # 31 days later the cooldown has lapsed → the site may be reconsidered.
    assert load_active_rejections(path, _NOW + timedelta(days=31)) == set()
    # still active at day 29.
    assert "https://stale.com/x" in load_active_rejections(path, _NOW + timedelta(days=29))


def test_malformed_url_is_not_written(tmp_path) -> None:
    path = tmp_path / "rejected.jsonl"
    assert append_rejection_tombstone(path, url="not a url", reason="x", now=_NOW) is False
    assert append_rejection_tombstone(path, url="", reason="x", now=_NOW) is False
    assert not path.exists()


def test_load_missing_file_is_empty(tmp_path) -> None:
    assert load_active_rejections(tmp_path / "nope.jsonl", _NOW) == set()
