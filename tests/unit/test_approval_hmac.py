"""HMAC-signed callback_data tests (P1 #9 — 2026-05-14)."""

from __future__ import annotations

import hashlib
import hmac as _hmac
from datetime import UTC, datetime, timedelta

from app.ingestion import telegram_channel_approval as approval

_SECRET = "test-secret-do-not-use-in-prod"
_ENV_ID = "ENV-20260514120000-deadbeef"


def _compute_expected_hmac(action: str, env_id: str, ttl_unix: int, secret: str = _SECRET) -> str:
    msg = f"{action}:{env_id}:{ttl_unix}".encode()
    return _hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:8]


# ── build_inline_keyboard ───────────────────────────────────────────────────


def test_build_keyboard_legacy_when_no_secret():
    kb = approval.build_inline_keyboard(_ENV_ID)
    fill_cb = kb[0][0]["callback_data"]
    ignore_cb = kb[0][1]["callback_data"]
    assert fill_cb == f"sig:f:{_ENV_ID}"
    assert ignore_cb == f"sig:i:{_ENV_ID}"


def test_build_keyboard_signed_when_secret_and_ttl():
    ttl_unix = 1746273600
    kb = approval.build_inline_keyboard(_ENV_ID, secret=_SECRET, ttl_deadline_unix=ttl_unix)
    fill_cb = kb[0][0]["callback_data"]
    parts = fill_cb.split(":")
    assert len(parts) == 5
    assert parts[0] == "sig" and parts[1] == "f"
    assert parts[2] == _ENV_ID
    assert int(parts[3]) == ttl_unix
    assert parts[4] == _compute_expected_hmac("f", _ENV_ID, ttl_unix)


def test_build_keyboard_falls_back_to_legacy_when_ttl_missing():
    kb = approval.build_inline_keyboard(_ENV_ID, secret=_SECRET, ttl_deadline_unix=None)
    assert kb[0][0]["callback_data"] == f"sig:f:{_ENV_ID}"


def test_callback_data_under_telegram_64_byte_limit():
    # Realistic worst case: 32-byte secret, longest reasonable ttl, longest env_id.
    ttl_unix = 9999999999
    kb = approval.build_inline_keyboard(_ENV_ID, secret="x" * 64, ttl_deadline_unix=ttl_unix)
    for row in kb:
        for button in row:
            assert len(button["callback_data"].encode("utf-8")) <= 64


# ── parse_callback_data ─────────────────────────────────────────────────────


def test_parse_legacy_form_accepted_without_secret():
    action = approval.parse_callback_data(f"sig:f:{_ENV_ID}")
    assert action is not None
    assert action.action == "fill"
    assert action.envelope_id == _ENV_ID


def test_parse_legacy_form_rejected_with_secret():
    action = approval.parse_callback_data(f"sig:f:{_ENV_ID}", secret=_SECRET)
    assert action is None


def test_parse_signed_token_valid_within_ttl():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    ttl_unix = int((now + timedelta(minutes=30)).timestamp())
    hmac_tag = _compute_expected_hmac("f", _ENV_ID, ttl_unix)
    data = f"sig:f:{_ENV_ID}:{ttl_unix}:{hmac_tag}"
    action = approval.parse_callback_data(data, secret=_SECRET, now=now)
    assert action is not None
    assert action.action == "fill"
    assert action.envelope_id == _ENV_ID


def test_parse_signed_token_rejected_after_ttl():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    ttl_unix = int((now - timedelta(minutes=1)).timestamp())  # already expired
    hmac_tag = _compute_expected_hmac("f", _ENV_ID, ttl_unix)
    data = f"sig:f:{_ENV_ID}:{ttl_unix}:{hmac_tag}"
    action = approval.parse_callback_data(data, secret=_SECRET, now=now)
    assert action is None


def test_parse_signed_token_rejected_on_bad_hmac():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    ttl_unix = int((now + timedelta(minutes=30)).timestamp())
    data = f"sig:f:{_ENV_ID}:{ttl_unix}:deadbeef"  # wrong tag
    action = approval.parse_callback_data(data, secret=_SECRET, now=now)
    assert action is None


def test_parse_signed_token_rejects_action_substitution():
    """A Fill-tag must NOT verify when applied to an Ignore-token."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    ttl_unix = int((now + timedelta(minutes=30)).timestamp())
    fill_tag = _compute_expected_hmac("f", _ENV_ID, ttl_unix)
    spliced = f"sig:i:{_ENV_ID}:{ttl_unix}:{fill_tag}"  # action flipped to Ignore
    assert approval.parse_callback_data(spliced, secret=_SECRET, now=now) is None


def test_parse_signed_token_with_bad_int_ttl():
    data = f"sig:f:{_ENV_ID}:not-a-number:deadbeef"
    assert approval.parse_callback_data(data, secret=_SECRET) is None


def test_parse_rejects_unknown_action_code():
    assert approval.parse_callback_data(f"sig:x:{_ENV_ID}") is None


def test_parse_rejects_wrong_prefix():
    assert approval.parse_callback_data(f"foo:f:{_ENV_ID}") is None


def test_parse_signed_accepts_without_secret_when_format_signed():
    """Migration runway: a signed token must still parse when secret is empty
    so an in-flight click from a strict-mode prior boot doesn't break after
    a config flip-flop."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    ttl_unix = int((now + timedelta(minutes=30)).timestamp())
    data = f"sig:f:{_ENV_ID}:{ttl_unix}:doesnotmatter"
    action = approval.parse_callback_data(data, secret=None, now=now)
    assert action is not None
    assert action.action == "fill"
