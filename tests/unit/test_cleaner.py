"""Tests for normalization.cleaner — URL, title, hash functions."""

from __future__ import annotations

import pytest

from app.normalization.cleaner import (
    clean_text,
    content_hash,
    normalize_title,
    normalize_url,
    title_hash,
)

# ── clean_text ────────────────────────────────────────────────────────────────


def test_clean_text_strips_html() -> None:
    assert clean_text("<p>Hello <b>World</b></p>") == "Hello World"


def test_clean_text_collapses_whitespace() -> None:
    assert clean_text("Hello   World\n\nFoo") == "Hello World Foo"


def test_clean_text_none_returns_none() -> None:
    assert clean_text(None) is None


def test_clean_text_empty_returns_none() -> None:
    assert clean_text("") is None
    assert clean_text("   ") is None


# ── normalize_url — basic ─────────────────────────────────────────────────────


def test_normalize_url_lowercases_scheme_and_host() -> None:
    assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"


def test_normalize_url_strips_trailing_slash() -> None:
    assert normalize_url("https://example.com/path/") == "https://example.com/path"


def test_normalize_url_strips_fragment() -> None:
    assert normalize_url("https://example.com/page#section") == "https://example.com/page"


def test_normalize_url_strips_www() -> None:
    assert normalize_url("https://www.example.com/page") == "https://example.com/page"


def test_normalize_url_www_and_no_www_are_equal() -> None:
    assert normalize_url("https://www.coindesk.com/article") == normalize_url(
        "https://coindesk.com/article"
    )


# ── normalize_url — tracking param stripping ──────────────────────────────────


@pytest.mark.parametrize(
    "url,expected_contains,expected_not_contains",
    [
        (
            "https://example.com/article?id=123&utm_source=twitter&utm_medium=social",
            "id=123",
            "utm_source",
        ),
        (
            "https://example.com/article?fbclid=abc&page=2",
            "page=2",
            "fbclid",
        ),
        (
            "https://example.com/article?gclid=xyz&ref=homepage&q=bitcoin",
            "q=bitcoin",
            "gclid",
        ),
        (
            "https://example.com/article?mc_cid=abc123&mc_eid=def456&section=news",
            "section=news",
            "mc_cid",
        ),
    ],
)
def test_normalize_url_strips_tracking_params(
    url: str, expected_contains: str, expected_not_contains: str
) -> None:
    normalized = normalize_url(url)
    assert expected_contains in normalized
    assert expected_not_contains not in normalized


def test_normalize_url_same_article_different_utm() -> None:
    """Same article linked from Twitter and Newsletter must deduplicate."""
    url_twitter = "https://coindesk.com/article?utm_source=twitter&utm_medium=social"
    url_email = "https://coindesk.com/article?utm_source=newsletter&utm_campaign=daily"
    assert normalize_url(url_twitter) == normalize_url(url_email)


def test_normalize_url_sorts_query_params() -> None:
    url_a = "https://example.com/feed?b=2&a=1"
    url_b = "https://example.com/feed?a=1&b=2"
    assert normalize_url(url_a) == normalize_url(url_b)


def test_normalize_url_keeps_content_params() -> None:
    url = "https://example.com/feed?id=123"
    assert "id=123" in normalize_url(url)


# ── normalize_title ───────────────────────────────────────────────────────────


def test_normalize_title_lowercases() -> None:
    assert normalize_title("Bitcoin Is BULLISH") == "bitcoin is bullish"


def test_normalize_title_collapses_whitespace() -> None:
    assert normalize_title("  Hello   World  ") == "hello world"


def test_normalize_title_strips_punctuation() -> None:
    result = normalize_title("Bitcoin hits $100K! (New ATH)")
    assert "$" not in result
    assert "!" not in result
    assert "(" not in result
    assert "100k" in result


def test_normalize_title_unicode_normalization() -> None:
    # Accented characters → ASCII equivalent
    result = normalize_title("Krypto: Überblick über die Märkte")
    assert "uber" in result or "uberblick" in result  # ü → u after NFKD
    assert "ü" not in result


def test_normalize_title_same_title_different_punctuation() -> None:
    t1 = normalize_title("Bitcoin Hits $100,000!")
    t2 = normalize_title("Bitcoin Hits $100,000")
    assert t1 == t2


def test_normalize_title_same_title_different_case() -> None:
    assert normalize_title("BITCOIN RALLY") == normalize_title("bitcoin rally")


# ── title_hash ────────────────────────────────────────────────────────────────


def test_title_hash_is_hex_64() -> None:
    h = title_hash("Bitcoin Hits $100K")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_title_hash_deterministic() -> None:
    assert title_hash("Bitcoin") == title_hash("Bitcoin")


def test_title_hash_case_insensitive() -> None:
    assert title_hash("BITCOIN") == title_hash("bitcoin")


def test_title_hash_punctuation_insensitive() -> None:
    assert title_hash("Bitcoin hits $100K!") == title_hash("Bitcoin hits 100K")


def test_title_hash_different_titles_differ() -> None:
    assert title_hash("Bitcoin rallies") != title_hash("Ethereum rallies")


# ── content_hash ──────────────────────────────────────────────────────────────


def test_content_hash_is_hex() -> None:
    h = content_hash("https://example.com", "title", "text")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_content_hash_deterministic() -> None:
    h1 = content_hash("https://example.com", "title", "text")
    h2 = content_hash("https://example.com", "title", "text")
    assert h1 == h2


def test_content_hash_differs_on_content() -> None:
    h1 = content_hash("https://example.com", "title", "text A")
    h2 = content_hash("https://example.com", "title", "text B")
    assert h1 != h2


def test_content_hash_none_text() -> None:
    h = content_hash("https://example.com", "title", None)
    assert len(h) == 64


def test_content_hash_utm_url_equals_clean_url() -> None:
    """UTM params must not affect content_hash — same article, same hash."""
    h1 = content_hash("https://example.com/article?utm_source=tw", "title", "text")
    h2 = content_hash("https://example.com/article", "title", "text")
    assert h1 == h2


def test_content_hash_www_equals_no_www() -> None:
    h1 = content_hash("https://www.coindesk.com/article", "title", "text")
    h2 = content_hash("https://coindesk.com/article", "title", "text")
    assert h1 == h2
