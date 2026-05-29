"""RSS adapter wedge-hardening tests (2026-05-29 kai-server CPU-wedge).

The D-122 full-text fallback called trafilatura synchronously in the event loop;
a feed batch with many empty-body / 403 article URLs starved kai-server for
minutes. These tests lock in the two guards: the per-fetch fallback budget and
the off-loop (to_thread) doc-conversion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import app.ingestion.rss.adapter as rss_adapter
from app.core.enums import SourceStatus, SourceType
from app.ingestion.base.interfaces import SourceMetadata
from app.ingestion.rss.adapter import RSSFeedAdapter


def _make_adapter() -> RSSFeedAdapter:
    metadata = SourceMetadata(
        source_id="test-feed",
        source_name="Test Feed",
        source_type=SourceType.RSS_FEED,
        url="https://example.com/feed",
        status=SourceStatus.ACTIVE,
    )
    return RSSFeedAdapter(metadata, timeout=5, max_retries=1)


def _empty_body_feed(n: int) -> bytes:
    items = "".join(
        f"""
    <item>
      <title>Empty body article {i}</title>
      <link>https://example.com/article-{i}</link>
      <description></description>
      <guid>https://example.com/article-{i}</guid>
    </item>"""
        for i in range(n)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>F</title><link>https://example.com</link>
<description>d</description>{items}</channel></rss>""".encode()


@pytest.mark.asyncio
async def test_full_text_budget_caps_fallback_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rss_adapter, "_FULL_TEXT_MAX_PER_FETCH", 2)
    adapter = _make_adapter()
    calls: list[str] = []

    def _count(url: str) -> None:
        calls.append(url)
        return None

    with (
        patch.object(adapter, "_fetch_full_text", side_effect=_count),
        patch.object(adapter, "_fetch_raw", new=AsyncMock(return_value=_empty_body_feed(6))),
    ):
        result = await adapter.fetch()

    assert result.success is True
    # 6 empty-body entries, but the budget stops fallback after 2 fetches.
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_budget_resets_per_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rss_adapter, "_FULL_TEXT_MAX_PER_FETCH", 1)
    adapter = _make_adapter()
    calls: list[str] = []
    with (
        patch.object(adapter, "_fetch_full_text", side_effect=lambda url: calls.append(url)),
        patch.object(adapter, "_fetch_raw", new=AsyncMock(return_value=_empty_body_feed(3))),
    ):
        await adapter.fetch()
        await adapter.fetch()
    # 1 per fetch × 2 fetches = 2 — counter resets each call, no accumulation.
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_fetch_still_returns_documents_off_loop() -> None:
    adapter = _make_adapter()
    with patch.object(adapter, "_fetch_raw", new=AsyncMock(return_value=_empty_body_feed(2))):
        result = await adapter.fetch()
    assert result.success is True
    assert len(result.documents) == 2


def test_fetch_full_text_fail_open_on_none() -> None:
    # trafilatura returns None (e.g. 403) → fail-open None, never raises.
    with patch.dict("sys.modules"):
        import types

        fake = types.ModuleType("trafilatura")
        fake.fetch_url = lambda url, config=None: None  # type: ignore[attr-defined]
        fake.extract = lambda downloaded: None  # type: ignore[attr-defined]
        settings_mod = types.ModuleType("trafilatura.settings")
        settings_mod.use_config = lambda: _FakeConfig()  # type: ignore[attr-defined]
        with patch.dict("sys.modules", {"trafilatura": fake, "trafilatura.settings": settings_mod}):
            assert RSSFeedAdapter._fetch_full_text("https://example.com/x") is None


class _FakeConfig:
    def set(self, *args: object) -> None:
        pass
