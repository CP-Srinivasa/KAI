"""Self-pay L402 helper tests (pure parsing/matching, no node)."""

from __future__ import annotations

import base64

import pytest

from app.lightning.l402 import parse_authorization
from app.lightning.selfpay import (
    build_l402_authorization,
    find_settled_preimage,
    parse_l402_challenge,
    payment_hash_from_token,
)

_TOKEN = "abc123.1893456000.b25jaGFpbg.deadbeefsig"
_BOLT11 = "lnbc100n1pexample..."


def test_parse_challenge_extracts_token_and_invoice() -> None:
    header = f'L402 token="{_TOKEN}", invoice="{_BOLT11}"'
    token, invoice = parse_l402_challenge(header)
    assert token == _TOKEN
    assert invoice == _BOLT11


def test_parse_challenge_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="missing"):
        parse_l402_challenge("")
    with pytest.raises(ValueError, match="not an L402 challenge"):
        parse_l402_challenge("Basic realm=x")


def test_payment_hash_is_token_field_zero() -> None:
    assert payment_hash_from_token(_TOKEN) == "abc123"
    with pytest.raises(ValueError, match="no payment_hash"):
        payment_hash_from_token(".rest.of.token")


def test_authorization_roundtrips_through_the_real_verifier_parser() -> None:
    # build_l402_authorization is the inverse of l402.parse_authorization — the
    # header we send must parse back to the same (token, preimage) the server reads.
    preimage_hex = "ab" * 32
    header = build_l402_authorization(_TOKEN, preimage_hex.upper())  # upper → lowered
    token, preimage = parse_authorization(header)
    assert token == _TOKEN
    assert preimage == preimage_hex


def test_find_settled_preimage_base64_to_hex() -> None:
    raw = bytes.fromhex("ab" * 32)
    invoices = [
        {"payment_request": "other", "settled": True, "r_preimage": "AAAA"},
        {
            "payment_request": _BOLT11,
            "settled": True,
            "r_preimage": base64.b64encode(raw).decode(),
        },
    ]
    assert find_settled_preimage(invoices, payment_request=_BOLT11) == "ab" * 32


def test_find_settled_preimage_hex_passthrough_and_state_field() -> None:
    invoices = [
        {"payment_request": _BOLT11, "state": "SETTLED", "r_preimage": "CD" * 32},
    ]
    assert find_settled_preimage(invoices, payment_request=_BOLT11) == "cd" * 32


def test_find_settled_preimage_none_when_unsettled_or_missing() -> None:
    # matching but not settled → None (keep polling)
    unsettled = [{"payment_request": _BOLT11, "settled": False, "r_preimage": "AAAA"}]
    assert find_settled_preimage(unsettled, payment_request=_BOLT11) is None
    # no matching invoice → None
    assert find_settled_preimage([], payment_request=_BOLT11) is None
    # settled but no preimage yet → None
    no_pre = [{"payment_request": _BOLT11, "settled": True}]
    assert find_settled_preimage(no_pre, payment_request=_BOLT11) is None
