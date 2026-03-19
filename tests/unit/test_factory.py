"""Tests for app/analysis/factory.py — create_provider()."""

from unittest.mock import MagicMock

import pytest

from app.analysis.factory import create_provider
from app.integrations.anthropic.provider import AnthropicAnalysisProvider
from app.integrations.gemini.provider import GeminiAnalysisProvider
from app.integrations.openai.provider import OpenAIAnalysisProvider

# ── helpers ───────────────────────────────────────────────────────────────────


def _settings(openai_key="", anthropic_key="", gemini_key="") -> MagicMock:
    s = MagicMock()
    s.providers.openai_api_key = openai_key
    s.providers.openai_model = "gpt-4o"
    s.providers.openai_timeout = 30
    s.providers.anthropic_api_key = anthropic_key
    s.providers.anthropic_model = "claude-3-7-sonnet-20250219"
    s.providers.anthropic_timeout = 30
    s.providers.gemini_api_key = gemini_key
    s.providers.gemini_model = "gemini-2.5-flash"
    s.providers.gemini_timeout = 30
    return s


# ── openai ────────────────────────────────────────────────────────────────────


def test_openai_returns_provider_when_key_set():
    p = create_provider("openai", _settings(openai_key="sk-test"))
    assert isinstance(p, OpenAIAnalysisProvider)
    assert p.provider_name == "openai"


def test_openai_returns_none_when_key_missing():
    p = create_provider("openai", _settings())
    assert p is None


# ── anthropic ─────────────────────────────────────────────────────────────────


def test_anthropic_returns_provider_when_key_set():
    p = create_provider("anthropic", _settings(anthropic_key="sk-ant-test"))
    assert isinstance(p, AnthropicAnalysisProvider)
    assert p.provider_name == "anthropic"


def test_anthropic_returns_none_when_key_missing():
    p = create_provider("anthropic", _settings())
    assert p is None


def test_claude_alias_returns_anthropic_provider():
    p = create_provider("claude", _settings(anthropic_key="sk-ant-test"))
    assert isinstance(p, AnthropicAnalysisProvider)


def test_claude_alias_returns_none_when_key_missing():
    p = create_provider("claude", _settings())
    assert p is None


# ── gemini ────────────────────────────────────────────────────────────────────


def test_gemini_returns_provider_when_key_set():
    p = create_provider("gemini", _settings(gemini_key="gk-test"))
    assert isinstance(p, GeminiAnalysisProvider)
    assert p.provider_name == "gemini"


def test_gemini_returns_none_when_key_missing():
    p = create_provider("gemini", _settings())
    assert p is None


# ── unknown provider ──────────────────────────────────────────────────────────


def test_unknown_provider_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported analysis provider"):
        create_provider("gpt5-turbo", _settings())


def test_empty_provider_type_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported analysis provider"):
        create_provider("", _settings())
