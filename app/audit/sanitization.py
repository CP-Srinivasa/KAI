"""Secret-redaction and string-truncation for audit writers.

Goal
----

KAI's Operator-Directive forbids structured logs from carrying:

  • full LLM raw chain-of-thought,
  • sensitive prompts,
  • secrets (API keys, bearer tokens, OAuth, AWS, etc.).

This module provides two orthogonal primitives — *redaction* (pattern-based
secret masking) and *truncation* (length cap with explicit marker) — that
all audit writers can compose. Pure Python, no external deps.

Design
------

- **Pattern-based**, not heuristic ML. False-positives stay localized to
  named patterns; the operator can review + extend the list.
- **Idempotent**: applying redaction twice yields the same output.
- **Preserves structure**: redactor walks ``dict``/``list``/``tuple``
  recursively and only touches string values. Numeric IDs / version_ids
  are untouched.
- **Marker-explicit**: redacted values become ``[REDACTED:<pattern_name>]``,
  truncated values become ``…[<n> chars truncated]``. A reader always
  knows what was hidden — no silent loss.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

DEFAULT_MAX_STRING_LENGTH: Final[int] = 500
TRUNCATION_MARKER_TEMPLATE: Final[str] = "…[{n} chars truncated]"
REDACTION_TEMPLATE: Final[str] = "[REDACTED:{name}]"


@dataclass(frozen=True)
class SecretPattern:
    """Named regex describing a secret shape we want to mask."""

    name: str
    pattern: re.Pattern[str]


# ─── Built-in patterns ────────────────────────────────────────────────────────
#
# The patterns below are deliberately conservative: they prefer false-negatives
# (missed secrets) over false-positives (over-redaction). Operators add to this
# list via DEFAULT_PATTERNS, never narrow it.
#
# All patterns ignore-case where it's safe.

DEFAULT_PATTERNS: Final[tuple[SecretPattern, ...]] = (
    # AWS access key (AKIA…/ASIA…/AGPA…/etc., 20 chars total) — high precision
    # because of the strict 4-letter prefix.
    SecretPattern(
        name="aws_access_key",
        pattern=re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ANSA)[A-Z0-9]{16}\b"),
    ),
    # NOTE: the bare 40-char base64-ish AWS-secret pattern is deliberately
    # NOT in the default set (Neo-F-004): it false-positives on SHA-1 hex,
    # git commit IDs, base32 fragments, version_id strings, etc. Operators
    # who *know* their stream contains AWS secret access keys can opt in
    # via ``EXTRA_AWS_PATTERNS`` below.
    # Generic Bearer token in HTTP-style headers ("Authorization: Bearer ...")
    SecretPattern(
        name="bearer_token",
        pattern=re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{16,}"),
    ),
    # OAuth-style basic auth in URLs ("https://user:secret@host")
    SecretPattern(
        name="basic_auth_url",
        pattern=re.compile(r"(?i)(https?|ftp)://[^\s:/]+:[^\s@/]+@"),
    ),
    # JWT (3 base64 segments)
    SecretPattern(
        name="jwt",
        pattern=re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    # OpenAI / Anthropic API keys (sk-..., sk-ant-..., etc.)
    SecretPattern(
        name="provider_api_key",
        pattern=re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{16,}\b"),
    ),
    # GitHub personal access token (ghp_…, gho_…, ghs_…, etc.)
    SecretPattern(
        name="github_token",
        pattern=re.compile(r"\bghp_[A-Za-z0-9]{36}\b|\bgh[osr]_[A-Za-z0-9]{36}\b"),
    ),
    # Telegram bot token (digits:Base64ish)
    SecretPattern(
        name="telegram_bot_token",
        pattern=re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,40}\b"),
    ),
    # Slack token (xoxb-…, xoxp-…, xoxa-…, xoxs-…)
    SecretPattern(
        name="slack_token",
        pattern=re.compile(r"\bxox[abps]-[A-Za-z0-9-]{10,}\b"),
    ),
    # Generic "<KEY>=<long_value>" where KEY contains 'key', 'token', 'secret', 'password'
    SecretPattern(
        name="env_secret_assignment",
        pattern=re.compile(
            r"(?i)\b(?:[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)\s*[:=]\s*['\"]?([A-Za-z0-9._\-+/=]{12,})['\"]?"
        ),
    ),
)

# ─── Opt-in extra patterns ────────────────────────────────────────────────────
#
# Operators add these via ``SanitizationConfig().with_extra_patterns(EXTRA_*)``
# when their stream is known to contain values of the relevant shape. Keeping
# them out of the default set avoids false-positives on benign hashes.

EXTRA_AWS_PATTERNS: Final[tuple[SecretPattern, ...]] = (
    # Bare AWS secret access key — 40 chars, base64-ish, no prefix.
    # WARNING: false-positives on SHA-1 hex (40 chars) and similar.
    SecretPattern(
        name="aws_secret_key",
        pattern=re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9])"),
    ),
)


@dataclass(frozen=True)
class SanitizationConfig:
    """Knobs for sanitization. All explicit, audit-friendly."""

    max_string_length: int = DEFAULT_MAX_STRING_LENGTH
    patterns: tuple[SecretPattern, ...] = DEFAULT_PATTERNS

    def with_extra_patterns(self, extras: Sequence[SecretPattern]) -> SanitizationConfig:
        """Add operator-supplied patterns. They are applied **before** the
        defaults so that a more specific custom pattern wins over the broader
        built-ins (e.g. a project-internal token shape matches before the
        generic ``env_secret_assignment`` swallow-all)."""
        return SanitizationConfig(
            max_string_length=self.max_string_length,
            patterns=tuple(extras) + tuple(self.patterns),
        )


# ─── Primitives ───────────────────────────────────────────────────────────────


def redact_secrets(text: str, *, patterns: Sequence[SecretPattern]) -> str:
    """Replace every match of every pattern with a named marker.

    Patterns are applied in order. Idempotent — applying twice produces the
    same result (markers don't match any pattern).
    """
    if not text:
        return text
    out = text
    for sp in patterns:
        # Capture the full match — even when the pattern uses groups for matching
        # context (we still want to redact the whole match).
        out = sp.pattern.sub(REDACTION_TEMPLATE.format(name=sp.name), out)
    return out


def truncate_string(text: str, *, max_length: int) -> str:
    """Cut a string to `max_length` chars and append a truncation marker.

    `max_length` covers the *visible content*; the marker is appended after.
    Strings already ≤ max_length are returned unchanged.
    """
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    if len(text) <= max_length:
        return text
    head = text[:max_length]
    n_dropped = len(text) - max_length
    return head + TRUNCATION_MARKER_TEMPLATE.format(n=n_dropped)


def sanitize_string(text: str, *, config: SanitizationConfig | None = None) -> str:
    """Apply redaction *then* truncation. Order matters: we don't want to
    leak the prefix of a secret that happens to fall before the cap."""
    cfg = config or SanitizationConfig()
    redacted = redact_secrets(text, patterns=cfg.patterns)
    return truncate_string(redacted, max_length=cfg.max_string_length)


# ─── Recursive walker ─────────────────────────────────────────────────────────


def sanitize_value(value: Any, *, config: SanitizationConfig | None = None) -> Any:
    """Recursively sanitize a JSON-like structure.

    String values get redacted + truncated. Numbers, bools, None pass through.
    Mappings / sequences are walked. Frozen sequences (tuple) are preserved
    as such. Other types are returned unchanged.
    """
    cfg = config or SanitizationConfig()
    return _sanitize(value, cfg)


def _sanitize(value: Any, cfg: SanitizationConfig) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return sanitize_string(value, config=cfg)
    if isinstance(value, bool):  # bool is a subclass of int; check first
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Mapping):
        # Neo-F-003 fix: sanitize string keys too. A caller that passes
        # `{"sk-ant-...": "value"}` (e.g. from repr() of a provider response)
        # would otherwise leak the secret as a dict key in the JSONL.
        return {
            (sanitize_string(k, config=cfg) if isinstance(k, str) else k): _sanitize(v, cfg)
            for k, v in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_sanitize(v, cfg) for v in value)
    if isinstance(value, (list, set, frozenset)):
        return [_sanitize(v, cfg) for v in value]
    # Unknown / opaque type — coerce to its string repr, then sanitize, so
    # nothing escapes the redaction net.
    return sanitize_string(repr(value), config=cfg)


__all__ = [
    "DEFAULT_MAX_STRING_LENGTH",
    "DEFAULT_PATTERNS",
    "EXTRA_AWS_PATTERNS",
    "REDACTION_TEMPLATE",
    "TRUNCATION_MARKER_TEMPLATE",
    "SanitizationConfig",
    "SecretPattern",
    "redact_secrets",
    "sanitize_string",
    "sanitize_value",
    "truncate_string",
]
