"""Tests für die pure Source-Scout-Logik (Phase 3, kein I/O/Netzwerk)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.learning.source_scout import (
    ScoutProposal,
    dedup_against_registry,
    feed_health_score,
    parse_feed_health,
    rank_proposals,
)

NOW = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)


def test_feed_health_score_bounds() -> None:
    assert feed_health_score(None, None) is None  # ungeprobt
    assert feed_health_score(0, None) == 0.0  # leer = wertlos
    # frisch (≤2d) + viel Volumen → nahe 1
    assert feed_health_score(20, 1.0) == 1.0
    # frisch aber wenig Volumen → Frische dominiert
    assert feed_health_score(2, 0.5) == round(0.7 * 1.0 + 0.3 * (2 / 20), 4)
    # stale (>30d) → Frische 0, nur Volumen-Anteil
    assert feed_health_score(20, 40.0) == round(0.3 * 1.0, 4)
    # kein Datum → vorsichtige Frische 0.3
    assert feed_health_score(10, None) == round(0.7 * 0.3 + 0.3 * 0.5, 4)


def test_parse_feed_health_rss() -> None:
    feed = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>a</title><pubDate>Tue, 23 Jun 2026 12:00:00 +0000</pubDate></item>
      <item><title>b</title><pubDate>Mon, 22 Jun 2026 12:00:00 +0000</pubDate></item>
    </channel></rss>"""
    count, age = parse_feed_health(feed, NOW)
    assert count == 2
    assert age == 1.0  # jüngstes Item ist 1 Tag alt


def test_parse_feed_health_atom() -> None:
    feed = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>a</title><updated>2026-06-24T00:00:00Z</updated></entry>
    </feed>"""
    count, age = parse_feed_health(feed, NOW)
    assert count == 1
    assert age == 0.5  # 12h alt


def test_parse_feed_health_invalid_and_empty() -> None:
    assert parse_feed_health("not xml at all", NOW) == (0, None)
    assert parse_feed_health("<rss><channel></channel></rss>", NOW) == (0, None)
    # Items ohne Datum → count, aber kein Alter
    feed = "<rss><channel><item><title>x</title></item></channel></rss>"
    assert parse_feed_health(feed, NOW) == (1, None)


def _c(url: str, provider: str | None = None) -> ScoutProposal:
    return ScoutProposal(url=url, access="rss", source_type="rss_feed", provider=provider)


def test_dedup_drops_known_url_provider_and_batch_dupes() -> None:
    cands = [
        _c("https://new.example/feed", "newone"),
        _c("https://Coindesk.com/rss/", "x"),  # URL schon registriert (case/slash-normalisiert)
        _c("https://other.example/feed", "decrypt"),  # provider schon registriert
        _c("https://new.example/feed", "dupe"),  # Batch-Dublette (URL)
        _c("not a url", "bad"),
    ]
    kept, dropped = dedup_against_registry(
        cands,
        existing_normalized_urls={"https://coindesk.com/rss"},
        existing_providers={"decrypt"},
    )
    assert [c.provider for c in kept] == ["newone"]
    reasons = dict(dropped)
    assert reasons["https://Coindesk.com/rss/"] == "duplicate_url"
    assert reasons["https://other.example/feed"] == "duplicate_provider:decrypt"
    assert reasons["https://new.example/feed"] == "duplicate_url"
    assert reasons["not a url"] == "malformed_url"


def test_rank_scored_first_unprobed_last() -> None:
    a = ScoutProposal(url="u1", access="rss", source_type="rss_feed", provider="a", score=0.4)
    b = ScoutProposal(url="u2", access="rss", source_type="rss_feed", provider="b", score=0.9)
    c = ScoutProposal(url="u3", access="rss", source_type="rss_feed", provider="c", score=None)
    ranked = rank_proposals([a, b, c])
    assert [p.provider for p in ranked] == ["b", "a", "c"]
