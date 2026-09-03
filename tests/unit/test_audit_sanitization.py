"""Unit tests for the audit sanitization primitives."""

from __future__ import annotations

import re

import pytest

from app.audit.sanitization import (
    DEFAULT_PATTERNS,
    REDACTION_TEMPLATE,
    SanitizationConfig,
    SecretPattern,
    redact_secrets,
    sanitize_string,
    sanitize_value,
    truncate_string,
)

# ============================================================================
# truncate_string
# ============================================================================


def test_truncate_below_limit_unchanged():
    assert truncate_string("hello", max_length=10) == "hello"


def test_truncate_above_limit_appends_marker():
    out = truncate_string("a" * 100, max_length=10)
    assert out.startswith("a" * 10)
    assert "90 chars truncated" in out


def test_truncate_zero_length_raises():
    with pytest.raises(ValueError, match="must be positive"):
        truncate_string("x", max_length=0)


# ============================================================================
# Pattern-based redaction
# ============================================================================


def test_redacts_aws_access_key():
    text = "creds: AKIAIOSFODNN7EXAMPLE end"
    out = redact_secrets(text, patterns=DEFAULT_PATTERNS)
    assert "AKIA" not in out
    assert "[REDACTED:aws_access_key]" in out


def test_redacts_aws_secret_key_only_via_opt_in_extra():
    """Bare 40-char AWS secret pattern is opt-in (Neo-F-004).

    With DEFAULT only: env_secret_assignment catches `AWS_SECRET=…` because
    of the KEY-name match — but a bare 40-char string in free text would
    NOT be redacted. Opt-in via EXTRA_AWS_PATTERNS catches the bare form.
    """
    from app.audit.sanitization import EXTRA_AWS_PATTERNS

    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    bare_text = f"value: {secret}"
    # Default does NOT redact a bare 40-char token (no KEY context)
    out_default = redact_secrets(bare_text, patterns=DEFAULT_PATTERNS)
    assert secret in out_default
    # Opt-in extras DO redact it
    cfg = SanitizationConfig().with_extra_patterns(EXTRA_AWS_PATTERNS)
    out_extra = redact_secrets(bare_text, patterns=cfg.patterns)
    assert secret not in out_extra
    assert "[REDACTED:aws_secret_key]" in out_extra


def test_redacts_bearer_token():
    text = "Authorization: Bearer abcdef1234567890XYZ"
    out = redact_secrets(text, patterns=DEFAULT_PATTERNS)
    assert "abcdef1234567890XYZ" not in out
    assert "[REDACTED:bearer_token]" in out


def test_redacts_basic_auth_url():
    text = "https://alice:hunter2@host.example.org/path"
    out = redact_secrets(text, patterns=DEFAULT_PATTERNS)
    assert "alice:hunter2" not in out
    assert "[REDACTED:basic_auth_url]" in out


def test_redacts_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdef1234567890"
    out = redact_secrets(f"token={jwt}", patterns=DEFAULT_PATTERNS)
    assert jwt not in out
    assert "[REDACTED:jwt]" in out


def test_redacts_provider_api_key():
    out = redact_secrets("key=sk-ant-abc123def4567890", patterns=DEFAULT_PATTERNS)
    assert "sk-ant-abc" not in out
    assert "[REDACTED:provider_api_key]" in out


# --- GitHub tokens ---------------------------------------------------------
#
# Until 2026-09-03 this file had NO test for the ``github_token`` pattern at
# all. That is why the pattern could carry four of six prefixes at a hard-coded
# length of 36 without anyone noticing -- the same gap that showed up on the CI
# side on 2026-09-01 and was closed there in #849.
#
# Synthetic fixtures. None of these values was ever valid; they are long enough
# to hit the patterns and typed arbitrarily.

GITHUB_FIXTURES = {
    "ghp_": "ghp_L1k2J3h4G5f6D7s8A9p0O1i2U3y4T5r6E7w8",
    "gho_": "gho_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "ghs_": "ghs_Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2",
    "ghu_": "ghu_Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6J7k8",
    "ghr_": "ghr_M1n2B3v4C5x6Z7a8S9d0F1g2H3j4K5l6P7o8",
    "github_pat_": "github_pat_11ABCDEFG0aB1cD2eF3gH4_i5J6k7L8m9N0oP1qR2sT3uV4wX5yZ6",
}


@pytest.mark.parametrize("prefix", sorted(GITHUB_FIXTURES))
def test_redacts_every_github_token_prefix(prefix):
    token = GITHUB_FIXTURES[prefix]
    out = redact_secrets(f"auth failed for {token}", patterns=DEFAULT_PATTERNS)
    assert "[REDACTED:github_token]" in out
    assert token not in out
    assert token[len(prefix) : len(prefix) + 12] not in out


def test_redacts_fine_grained_pat_that_the_old_length_36_pattern_missed():
    """Regression for the concrete defect: a fine-grained PAT is far longer
    than 36 chars, so ``{36}`` never matched it -- not even partially."""
    token = GITHUB_FIXTURES["github_pat_"]
    assert len(token) > 36 + len("github_pat_")
    assert "[REDACTED:github_token]" in redact_secrets(token, patterns=DEFAULT_PATTERNS)


def test_still_redacts_a_classic_36_char_token():
    """The widening must not lose the length that used to work."""
    token = "ghp_" + "a" * 36
    assert "[REDACTED:github_token]" in redact_secrets(token, patterns=DEFAULT_PATTERNS)


@pytest.mark.parametrize(
    "benign",
    [
        "the gho_ prefix denotes OAuth tokens",
        "the exposed token was gho_****",
        "the exposed token was gho_<redacted>",
        "ghost_writer = True",
        "github_page_url is not a secret",
        "gho_abc123",
    ],
)
def test_github_pattern_does_not_fire_on_benign_text(benign):
    """False-positive controls matter as much as the positive ones: a redactor
    that mangles every mention of ``gho_`` makes the incident itself
    undocumentable, and an over-eager pattern is the one that gets removed."""
    assert "[REDACTED:github_token]" not in redact_secrets(benign, patterns=DEFAULT_PATTERNS)


def test_github_redaction_is_idempotent():
    once = redact_secrets(GITHUB_FIXTURES["gho_"], patterns=DEFAULT_PATTERNS)
    assert redact_secrets(once, patterns=DEFAULT_PATTERNS) == once


def test_redacts_telegram_bot_token():
    token = "123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    out = redact_secrets(f"token={token}", patterns=DEFAULT_PATTERNS)
    assert "ABC-DEF" not in out


def test_redacts_env_secret_assignment():
    out = redact_secrets("OPENAI_API_KEY=verysecretvalue123", patterns=DEFAULT_PATTERNS)
    assert "verysecretvalue123" not in out


def test_redaction_is_idempotent():
    text = "AKIAIOSFODNN7EXAMPLE and Bearer abcdef1234567890"
    once = redact_secrets(text, patterns=DEFAULT_PATTERNS)
    twice = redact_secrets(once, patterns=DEFAULT_PATTERNS)
    assert once == twice


def test_redacts_with_custom_pattern():
    custom = SecretPattern(name="kai_internal", pattern=re.compile(r"INTERNAL_[A-Z0-9]{8}"))
    cfg = SanitizationConfig().with_extra_patterns([custom])
    out = redact_secrets("token: INTERNAL_ABC12345", patterns=cfg.patterns)
    assert "[REDACTED:kai_internal]" in out


def test_redaction_preserves_non_secret_text():
    out = redact_secrets("plain BTC analysis text", patterns=DEFAULT_PATTERNS)
    assert out == "plain BTC analysis text"


# ============================================================================
# sanitize_string (combine redact + truncate)
# ============================================================================


def test_sanitize_string_redacts_then_truncates():
    cfg = SanitizationConfig(max_string_length=30)
    text = "AKIAIOSFODNN7EXAMPLE plus a long suffix that will be truncated"
    out = sanitize_string(text, config=cfg)
    # The secret got redacted, and the result was truncated
    assert "AKIA" not in out
    assert "chars truncated" in out


def test_sanitize_string_does_not_leak_secret_via_truncation():
    """If the secret sits past max_length it must be redacted *first* so that
    truncation cannot reveal a head-fragment of the secret."""
    secret = "AKIAIOSFODNN7EXAMPLE"
    text = "x" * 100 + " " + secret
    cfg = SanitizationConfig(max_string_length=200)
    out = sanitize_string(text, config=cfg)
    assert secret not in out


# ============================================================================
# Recursive walker
# ============================================================================


def test_sanitize_value_walks_dict_recursively():
    payload = {
        "headers": {"Authorization": "Bearer abcdef1234567890XYZ"},
        "params": ["plain", "AKIAIOSFODNN7EXAMPLE"],
        "score": 0.85,
        "active": True,
        "noop": None,
    }
    out = sanitize_value(payload)
    # Strings redacted everywhere
    assert "abcdef1234567890XYZ" not in str(out["headers"])
    assert "AKIA" not in str(out["params"])
    # Numbers / bool / None pass through
    assert out["score"] == 0.85
    assert out["active"] is True
    assert out["noop"] is None


def test_sanitize_value_preserves_tuple_type():
    out = sanitize_value(("plain", "Bearer abcdef1234567890XYZ"))
    assert isinstance(out, tuple)
    assert "abcdef1234567890XYZ" not in out[1]


def test_sanitize_value_handles_set_and_frozenset():
    out = sanitize_value({"plain", "Bearer abcdef1234567890XYZ"})
    assert isinstance(out, list)  # sets aren't JSON-serializable; we coerce


def test_sanitize_value_truncates_long_strings_in_payload():
    cfg = SanitizationConfig(max_string_length=20)
    out = sanitize_value({"long": "x" * 100}, config=cfg)
    assert "chars truncated" in out["long"]


def test_sanitize_value_falls_back_to_repr_for_opaque_types():
    class Opaque:
        def __repr__(self) -> str:
            return "<opaque AKIAIOSFODNN7EXAMPLE>"

    out = sanitize_value(Opaque())
    assert isinstance(out, str)
    assert "AKIA" not in out  # repr was sanitized
    assert REDACTION_TEMPLATE.format(name="aws_access_key") in out


def test_sanitize_value_redacts_secrets_in_dict_keys():
    """Neo-F-003 fix: a secret used as a dict-key must NOT leak into the
    audit JSONL just because string-redaction was only applied to values."""
    payload = {
        "AKIAIOSFODNN7EXAMPLE": "metadata",
        "Bearer abcdef1234567890XYZ": "another",
        "plain_key": "plain_value",
    }
    out = sanitize_value(payload)
    flat = "|".join(out.keys())
    assert "AKIA" not in flat
    assert "abcdef1234567890XYZ" not in flat
    assert "plain_key" in out
    # Value-side still works
    assert out["plain_key"] == "plain_value"


def test_sanitize_value_handles_non_string_dict_keys():
    """Numeric / tuple keys pass through unchanged; only string keys get
    sanitized."""
    payload = {1: "a", (2, 3): "b", "key": "c"}
    out = sanitize_value(payload)
    assert 1 in out
    assert (2, 3) in out
    assert "key" in out
