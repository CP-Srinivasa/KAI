"""Unit tests for the Fear & Greed sentiment adapter + TTL cache (read-only).

Covers fail-closed fetch behaviour (happy / non-200 / empty payload) and the
non-blocking cache (cold → warming-up, background refresh populates).
"""

from __future__ import annotations

import httpx
import pytest

from app.market_data import sentiment as sent


@pytest.fixture(autouse=True)
def _clean_cache():
    sent.reset_cache_for_tests()
    yield
    sent.reset_cache_for_tests()


def _transport(status: int, body: object) -> httpx.MockTransport:
    def handler(_req: httpx.Request) -> httpx.Response:
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=str(body))

    return httpx.MockTransport(handler)


async def test_fetch_happy() -> None:
    t = _transport(
        200,
        {"data": [{"value": "54", "value_classification": "Neutral", "timestamp": "1718630000"}]},
    )
    snap = await sent.fetch_sentiment(transport=t)
    assert snap.available is True
    assert snap.value == 54 and snap.classification == "Neutral"
    assert snap.timestamp_utc.endswith("Z") and snap.source == "alternative.me"


async def test_fetch_non_200_is_fail_closed() -> None:
    snap = await sent.fetch_sentiment(transport=_transport(503, "down"))
    assert snap.available is False and "http 503" in snap.reason


async def test_fetch_empty_payload_is_fail_closed() -> None:
    snap = await sent.fetch_sentiment(transport=_transport(200, {"data": []}))
    assert snap.available is False and snap.reason


async def test_cache_cold_then_warms(monkeypatch) -> None:
    async def _fake() -> sent.SentimentSnapshot:
        return sent.SentimentSnapshot(available=True, value=72, classification="Greed")

    monkeypatch.setattr(sent, "fetch_sentiment", _fake)

    snap, age = await sent.get_cached_sentiment()
    assert snap.available is False and age is None  # cold: never blocks

    await sent._refresh_task

    snap, age = await sent.get_cached_sentiment()
    assert snap.available is True and snap.value == 72 and age is not None


async def test_cache_keeps_last_good_on_failure(monkeypatch) -> None:
    seq = [
        sent.SentimentSnapshot(available=True, value=40, classification="Fear"),
        sent.SentimentSnapshot.unavailable("fetch failed"),
    ]

    async def _fake() -> sent.SentimentSnapshot:
        return seq.pop(0)

    monkeypatch.setattr(sent, "fetch_sentiment", _fake)

    await sent.get_cached_sentiment()
    await sent._refresh_task
    assert (await sent.get_cached_sentiment())[0].value == 40

    monkeypatch.setattr(sent, "_TTL_SECONDS", -1.0)  # force refresh on next read
    await sent.get_cached_sentiment()
    await sent._refresh_task
    snap, _ = await sent.get_cached_sentiment()
    assert snap.available is True and snap.value == 40  # last good retained
