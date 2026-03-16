"""Tests for app/normalization/cleaner.py"""
from __future__ import annotations

import pytest

from app.normalization.cleaner import (
    clean_text,
    extract_title_from_text,
    normalize_title,
    normalize_unicode,
    normalize_whitespace,
    remove_control_chars,
    remove_urls,
    strip_html,
    truncate,
)


class TestStripHtml:
    def test_removes_tags(self) -> None:
        assert strip_html("<p>Hello <b>world</b></p>") == " Hello  world  "

    def test_no_tags(self) -> None:
        assert strip_html("plain text") == "plain text"

    def test_empty(self) -> None:
        assert strip_html("") == ""

    def test_self_closing(self) -> None:
        result = strip_html("line1<br/>line2")
        assert "line1" in result and "line2" in result


class TestNormalizeWhitespace:
    def test_collapses_spaces(self) -> None:
        assert normalize_whitespace("hello   world") == "hello world"

    def test_collapses_newlines(self) -> None:
        assert normalize_whitespace("hello\n\nworld") == "hello world"

    def test_strips_leading_trailing(self) -> None:
        assert normalize_whitespace("  hello  ") == "hello"

    def test_tabs(self) -> None:
        assert normalize_whitespace("a\t\tb") == "a b"


class TestRemoveControlChars:
    def test_removes_null(self) -> None:
        assert "\x00" not in remove_control_chars("hello\x00world")

    def test_preserves_newline_tab(self) -> None:
        result = remove_control_chars("hello\nworld\ttab")
        assert "hello" in result
        assert "world" in result

    def test_empty(self) -> None:
        assert remove_control_chars("") == ""


class TestNormalizeUnicode:
    def test_nfc_normalization(self) -> None:
        # Decomposed 'é' → composed 'é'
        decomposed = "e\u0301"  # e + combining acute accent
        result = normalize_unicode(decomposed)
        assert result == "\xe9"  # composed é

    def test_passthrough_ascii(self) -> None:
        assert normalize_unicode("hello") == "hello"


class TestRemoveUrls:
    def test_removes_http(self) -> None:
        result = remove_urls("check https://example.com for more")
        assert "https://example.com" not in result
        assert "check" in result

    def test_removes_www(self) -> None:
        result = remove_urls("visit www.example.com today")
        assert "www.example.com" not in result

    def test_no_urls(self) -> None:
        assert remove_urls("no urls here") == "no urls here"


class TestTruncate:
    def test_no_truncation_when_short(self) -> None:
        text = "hello world"
        assert truncate(text, max_chars=100) == text

    def test_truncates_at_word_boundary(self) -> None:
        text = "hello world foo bar"
        result = truncate(text, max_chars=11)
        assert result.endswith("…")
        assert "hello" in result

    def test_appends_ellipsis(self) -> None:
        result = truncate("a b c d e f", max_chars=5)
        assert result.endswith("…")

    def test_exact_length(self) -> None:
        text = "exactly"
        assert truncate(text, max_chars=7) == text


class TestCleanText:
    def test_full_pipeline(self) -> None:
        raw = "  <p>Hello <b>world</b></p>\n\n  "
        result = clean_text(raw)
        assert "<p>" not in result
        assert "<b>" not in result
        assert "Hello" in result
        assert "world" in result

    def test_empty_returns_empty(self) -> None:
        assert clean_text("") == ""
        assert clean_text(None) == ""  # type: ignore[arg-type]

    def test_strip_urls_optional(self) -> None:
        raw = "Read https://example.com for details"
        result_with = clean_text(raw, strip_urls=True)
        result_without = clean_text(raw, strip_urls=False)
        assert "https://example.com" not in result_with
        assert "https://example.com" in result_without

    def test_max_chars(self) -> None:
        raw = "word " * 1000
        result = clean_text(raw, max_chars=50)
        # The result should be at most 50 chars + ellipsis
        assert len(result) <= 52  # 50 chars + "…"

    def test_unicode_in_pipeline(self) -> None:
        raw = "Caf\u00e9 <br/> au lait"
        result = clean_text(raw)
        assert "Café" in result


class TestExtractTitleFromText:
    def test_first_meaningful_line(self) -> None:
        text = "\n\nHello World, this is a title\nsome other content"
        assert extract_title_from_text(text) == "Hello World, this is a title"

    def test_skips_short_lines(self) -> None:
        text = "hi\nThis is a proper title line here"
        result = extract_title_from_text(text)
        assert result == "This is a proper title line here"

    def test_max_len(self) -> None:
        text = "A" * 300
        result = extract_title_from_text(text, max_len=50)
        assert len(result) == 50

    def test_empty_text(self) -> None:
        assert extract_title_from_text("") == ""


class TestNormalizeTitle:
    def test_lowercases(self) -> None:
        assert normalize_title("BITCOIN") == "bitcoin"

    def test_removes_punctuation(self) -> None:
        result = normalize_title("Bitcoin: The Future!")
        assert ":" not in result
        assert "!" not in result

    def test_normalizes_whitespace(self) -> None:
        result = normalize_title("  Bitcoin   Price  ")
        assert "  " not in result

    def test_unicode(self) -> None:
        result = normalize_title("Café au Lait")
        assert result == "café au lait"
