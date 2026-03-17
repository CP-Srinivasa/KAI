from app.normalization.cleaner import clean_text, content_hash, normalize_title, normalize_url


def test_clean_text_strips_html() -> None:
    assert clean_text("<p>Hello <b>World</b></p>") == "Hello World"


def test_clean_text_collapses_whitespace() -> None:
    assert clean_text("Hello   World\n\nFoo") == "Hello World Foo"


def test_clean_text_none_returns_none() -> None:
    assert clean_text(None) is None


def test_clean_text_empty_returns_none() -> None:
    assert clean_text("") is None
    assert clean_text("   ") is None


def test_normalize_url_lowercases() -> None:
    assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"


def test_normalize_url_strips_trailing_slash() -> None:
    assert normalize_url("https://example.com/path/") == "https://example.com/path"


def test_normalize_url_strips_fragment() -> None:
    assert normalize_url("https://example.com/page#section") == "https://example.com/page"


def test_normalize_url_keeps_query_params() -> None:
    url = "https://example.com/feed?id=123"
    assert "id=123" in normalize_url(url)


def test_normalize_title_lowercases() -> None:
    assert normalize_title("Bitcoin Is BULLISH") == "bitcoin is bullish"


def test_normalize_title_collapses_whitespace() -> None:
    assert normalize_title("  Hello   World  ") == "hello world"


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
