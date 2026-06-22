"""L402 paywall primitives — crypto correctness + security invariants.

The whole point: access is granted ONLY for a token KAI signed AND a preimage
that hashes to the bound payment_hash. Forged tokens, wrong preimages, expired
tokens, and tampering are all rejected. Pure — no network, no node, no funds.
"""

from __future__ import annotations

import hashlib

import pytest

from app.lightning.l402 import (
    L402Error,
    build_challenge_header,
    mint_token,
    parse_authorization,
    verify,
)

_SECRET = "test-l402-secret"
_PREIMAGE = "11" * 32
_PAYMENT_HASH = hashlib.sha256(bytes.fromhex(_PREIMAGE)).hexdigest()


def test_mint_verify_roundtrip_valid() -> None:
    tok = mint_token(_PAYMENT_HASH, secret=_SECRET, scope="onchain-facts")
    v = verify(tok, _PREIMAGE, secret=_SECRET)
    assert v.valid and v.reason == "ok"
    assert v.payment_hash == _PAYMENT_HASH and v.scope == "onchain-facts"


def test_wrong_preimage_rejected() -> None:
    tok = mint_token(_PAYMENT_HASH, secret=_SECRET)
    v = verify(tok, "22" * 32, secret=_SECRET)  # hashes to a different payment_hash
    assert not v.valid and "preimage does not match" in v.reason


def test_forged_or_tampered_token_rejected() -> None:
    tok = mint_token(_PAYMENT_HASH, secret=_SECRET)
    # wrong secret => bad signature
    assert not verify(tok, _PREIMAGE, secret="other-secret").valid
    # tamper the payload (flip a hex char in the payment_hash field)
    ph, exp, scope_b64, sig = tok.split(".", 3)
    bad = ph[:-1] + ("0" if ph[-1] != "0" else "1")
    assert verify(f"{bad}.{exp}.{scope_b64}.{sig}", _PREIMAGE, secret=_SECRET).valid is False


def test_expired_token_rejected() -> None:
    tok = mint_token(_PAYMENT_HASH, secret=_SECRET, ttl_s=10)
    v = verify(tok, _PREIMAGE, secret=_SECRET, now=10**12)  # far future
    assert not v.valid and v.reason == "token expired"


def test_mint_rejects_bad_inputs() -> None:
    with pytest.raises(L402Error):
        mint_token("nothex", secret=_SECRET)
    with pytest.raises(L402Error):
        mint_token(_PAYMENT_HASH, secret="")  # no secret configured


def test_no_secret_verify_is_invalid_not_crash() -> None:
    assert verify("a.b.c.d", _PREIMAGE, secret="").valid is False


def test_parse_authorization() -> None:
    tok = mint_token(_PAYMENT_HASH, secret=_SECRET)
    t, p = parse_authorization(f"L402 {tok}:{_PREIMAGE}")
    assert t == tok and p == _PREIMAGE
    # legacy LSAT scheme tolerated
    assert parse_authorization(f"LSAT {tok}:{_PREIMAGE}")[0] == tok
    for bad in ("", "Bearer x", "L402 tokenonly", "L402 :preimage"):
        with pytest.raises(L402Error):
            parse_authorization(bad)


def test_challenge_header_shape() -> None:
    h = build_challenge_header("tok123", "lnbc1...")
    assert h.startswith("L402 ") and 'token="tok123"' in h and 'invoice="lnbc1..."' in h
