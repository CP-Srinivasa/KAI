"""OKX announcements adapter — pure-mapper contract.

The network fetch is thin (httpx + retry); the testable core is the JSON→
CanonicalDocument mapping and the epoch-ms timestamp parsing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.enums import DocumentType, SourceType
from app.integrations.okx_announcements.adapter import (
    _parse_ptime,
    announcements_to_documents,
)

_FETCHED = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)


def _payload(details: list[dict]) -> dict:
    return {"code": "0", "msg": "", "data": [{"details": details}]}


def test_maps_valid_announcement() -> None:
    docs = announcements_to_documents(
        _payload(
            [
                {
                    "annType": "announcements-new-listings",
                    "title": "OKX to list EXAMPLE (EX) for spot trading",
                    "url": "https://www.okx.com/help/okx-to-list-ex",
                    "pTime": "1780657878051",
                }
            ]
        ),
        source_id="okx_announcements",
        source_name="OKX Announcements",
        fetched_at=_FETCHED,
    )
    assert len(docs) == 1
    d = docs[0]
    assert d.title.startswith("OKX to list EXAMPLE")
    assert d.url == "https://www.okx.com/help/okx-to-list-ex"
    assert d.external_id == d.url  # dedupe anchor
    assert d.raw_text == d.title
    assert d.provider == "okx_announcements"
    assert d.source_type == SourceType.NEWS_API
    assert d.document_type == DocumentType.ARTICLE
    assert d.published_at is not None and d.published_at.tzinfo is not None
    assert d.metadata["ann_type"] == "announcements-new-listings"


def test_skips_entries_without_title_or_url() -> None:
    docs = announcements_to_documents(
        _payload(
            [
                {"title": "", "url": "https://x/1", "pTime": "1780657878051"},
                {"title": "No URL", "url": "", "pTime": "1780657878051"},
                {"title": "Good", "url": "https://x/ok", "pTime": "1780657878051"},
            ]
        ),
        source_id="s",
        source_name="n",
        fetched_at=_FETCHED,
    )
    assert [d.title for d in docs] == ["Good"]


def test_tolerates_empty_and_missing_shapes() -> None:
    assert announcements_to_documents({}, source_id="s", source_name="n", fetched_at=_FETCHED) == []
    assert (
        announcements_to_documents(
            {"data": []}, source_id="s", source_name="n", fetched_at=_FETCHED
        )
        == []
    )
    assert (
        announcements_to_documents(
            {"data": [{"details": None}]}, source_id="s", source_name="n", fetched_at=_FETCHED
        )
        == []
    )


def test_parse_ptime() -> None:
    assert _parse_ptime("1780657878051") == datetime.fromtimestamp(1780657878.051, tz=UTC)
    assert _parse_ptime(None) is None
    assert _parse_ptime("") is None
    assert _parse_ptime("not-a-number") is None
