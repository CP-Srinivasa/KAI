"""L402 paywall primitives — crypto correctness + security invariants.

The whole point: access is granted ONLY for a token KAI signed AND a preimage
that hashes to the bound payment_hash. Forged tokens, wrong preimages, expired
tokens, and tampering are all rejected. Pure — no network, no node, no funds.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re

import pytest

from app.lightning import macaroon_v2 as mv2
from app.lightning.l402 import (
    L402Error,
    build_challenge_header,
    mint_token,
    parse_authorization,
    token_expiry,
    token_payment_hash,
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


def _mac(token: str) -> mv2.Macaroon:
    return mv2.decode(base64.b64decode(token))


def _b64(mac: mv2.Macaroon) -> str:
    return base64.b64encode(mv2.encode(mac)).decode()


def _client_appends(token: str, caveat: str) -> str:
    """Was ein Client ohne Root-Key kann: einen Caveat anhaengen (aperture: preimage=)."""
    mac = _mac(token)
    sig = hmac.new(mac.signature, caveat.encode(), hashlib.sha256).digest()
    return _b64(mv2.Macaroon(mac.identifier, (*mac.caveats, caveat.encode()), sig))


def test_forged_or_tampered_token_rejected() -> None:
    tok = mint_token(_PAYMENT_HASH, secret=_SECRET, scope="verdicts")
    # wrong secret => bad signature
    assert not verify(tok, _PREIMAGE, secret="other-secret").valid
    mac = _mac(tok)
    # payment_hash im Identifier umgeschrieben
    forged_id = mac.identifier[:2] + bytes(32) + mac.identifier[34:]
    forged = _b64(mv2.Macaroon(forged_id, mac.caveats, mac.signature))
    assert verify(forged, _PREIMAGE, secret=_SECRET).reason == "bad signature"
    # Scope-Caveat ausgetauscht (verdicts -> fee-series)
    swapped = tuple(c.replace(b"=verdicts", b"=fee-series") for c in mac.caveats)
    swapped_token = _b64(mv2.Macaroon(mac.identifier, swapped, mac.signature))
    assert verify(swapped_token, _PREIMAGE, secret=_SECRET).reason == "bad signature"
    # Caveat entfernt (valid_until weg)
    dropped = _b64(mv2.Macaroon(mac.identifier, mac.caveats[:-1], mac.signature))
    assert verify(dropped, _PREIMAGE, secret=_SECRET).reason == "bad signature"


def test_expired_token_rejected() -> None:
    tok = mint_token(_PAYMENT_HASH, secret=_SECRET, ttl_s=10)
    v = verify(tok, _PREIMAGE, secret=_SECRET, now=10**12)  # far future
    assert not v.valid and v.reason == "token expired"


def test_expired_token_can_be_fully_verified_for_existing_replay_only() -> None:
    token = mint_token(_PAYMENT_HASH, secret=_SECRET, ttl_s=-1, scope="timestamp:abc")
    verdict = verify(token, _PREIMAGE, secret=_SECRET, allow_expired=True)
    assert verdict.valid is True
    assert verdict.reason == "ok_expired"
    assert verdict.payment_hash == _PAYMENT_HASH and verdict.scope == "timestamp:abc"

    wrong = verify(token, "00" * 32, secret=_SECRET, allow_expired=True)
    assert wrong.valid is False
    assert wrong.reason == "preimage does not match payment_hash"


def test_mint_rejects_bad_inputs() -> None:
    with pytest.raises(L402Error):
        mint_token("nothex", secret=_SECRET)
    with pytest.raises(L402Error):
        mint_token(_PAYMENT_HASH, secret="")  # no secret configured


def test_no_secret_verify_is_invalid_not_crash() -> None:
    assert verify("a.b.c.d", _PREIMAGE, secret="").valid is False
    assert verify("kein-macaroon", _PREIMAGE, secret=_SECRET).valid is False


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
    assert h == 'L402 macaroon="tok123", invoice="lnbc1..."'


def test_token_expiry_reads_the_signed_expiry() -> None:
    token = mint_token(_PAYMENT_HASH, secret=_SECRET, ttl_s=3900, scope="verdicts", now=1000)
    assert token_expiry(token) == 4900


def test_token_expiry_rejects_a_malformed_token() -> None:
    with pytest.raises(L402Error):
        token_expiry("kein-token")


# --- Audit A5: aperture/lnget-kompatibles L402 --------------------------------------

#: lnget ``l402/header.go`` (Stand 27.09.2026), Muster unveraendert uebernommen.
_LNGET_CHALLENGE = re.compile(r'(?i)(LSAT|L402)\s+macaroon="([^"]+)",\s*invoice="([^"]+)"')


def test_the_challenge_parses_like_lnget_and_the_identifier_like_aperture() -> None:
    token = mint_token(_PAYMENT_HASH, secret=_SECRET, scope="verdicts")
    match = _LNGET_CHALLENGE.search(build_challenge_header(token, "lnbc100n1x"))
    assert match is not None and match.group(3) == "lnbc100n1x"
    ident = mv2.decode(base64.b64decode(match.group(2))).identifier  # StdEncoding wie lnget
    # aperture DecodeIdentifier: uint16 BE Version 0, payment_hash (32), token_id (32)
    assert int.from_bytes(ident[:2], "big") == 0 and len(ident) == 66
    assert ident[2:34].hex() == _PAYMENT_HASH
    assert token_payment_hash(token) == _PAYMENT_HASH


def test_aperture_paid_macaroon_with_preimage_caveat_is_accepted() -> None:
    """aperture ``Token.PaidMacaroon`` haengt ``preimage=<hex>`` an, bevor es sendet."""
    token = mint_token(_PAYMENT_HASH, secret=_SECRET, scope="verdicts")
    paid = _client_appends(token, f"preimage={_PREIMAGE}")
    verdict = verify(*parse_authorization(f"L402 {paid}:{_PREIMAGE}"), secret=_SECRET)
    assert verdict.valid and verdict.scope == "verdicts"
    wrong = _client_appends(token, "preimage=" + "22" * 32)
    assert verify(wrong, _PREIMAGE, secret=_SECRET).reason == "preimage caveat mismatch"


def test_a_client_can_only_narrow_never_widen() -> None:
    token = mint_token(_PAYMENT_HASH, secret=_SECRET, scope="verdicts", now=1000, ttl_s=3600)
    shorter = _client_appends(token, "kai-oracle_valid_until=2000")
    assert verify(shorter, _PREIMAGE, secret=_SECRET, now=2500).reason == "token expired"
    longer = _client_appends(token, "kai-oracle_valid_until=999999")
    assert verify(longer, _PREIMAGE, secret=_SECRET, now=5000).reason == "token expired"
    other_scope = _client_appends(token, "kai-oracle_capabilities=fee-series")
    assert verify(other_scope, _PREIMAGE, secret=_SECRET, now=1500).valid is False
    unknown = _client_appends(token, "irgendwas=egal")  # Spec: unbekannt -> ueberspringen
    assert verify(unknown, _PREIMAGE, secret=_SECRET, now=1500).valid is True


def test_legacy_tokens_minted_before_a5_still_verify() -> None:
    """Ausgegebene Zugaenge und gespeicherte Timestamp-Jobs nutzen das alte Format."""
    scope_b64 = base64.urlsafe_b64encode(b"timestamp:abc").decode().rstrip("=")
    payload = f"{_PAYMENT_HASH}.1000.{scope_b64}"
    sig = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    legacy = f"{payload}.{sig}"
    verdict = verify(legacy, _PREIMAGE, secret=_SECRET, now=5000, allow_expired=True)
    assert verdict.valid and verdict.reason == "ok_expired" and verdict.scope == "timestamp:abc"
    assert token_expiry(legacy) == 1000


def test_url_safe_base64_is_accepted_like_lnget() -> None:
    token = mint_token(_PAYMENT_HASH, secret=_SECRET, scope="verdicts")
    url_safe = base64.urlsafe_b64encode(base64.b64decode(token)).decode().rstrip("=")
    assert verify(url_safe, _PREIMAGE, secret=_SECRET).valid is True


def test_third_party_caveats_are_refused() -> None:
    mac = _mac(mint_token(_PAYMENT_HASH, secret=_SECRET, scope="verdicts"))
    raw = mv2.encode(mac)
    # Caveat-Abschnitt mit Verification-ID (Feld 4) vor das Ende der Caveats setzen.
    end_of_caveats = len(raw) - (1 + 2 + 32)  # ... EOS | 0x06 0x20 sig
    third = bytes([2, 3]) + b"x=y" + bytes([4, 1, 7, 0])
    tampered = raw[:end_of_caveats] + third + raw[end_of_caveats:]
    with pytest.raises(mv2.MacaroonError, match="third-party"):
        mv2.decode(tampered)
    assert verify(base64.b64encode(tampered).decode(), _PREIMAGE, secret=_SECRET).valid is False


#: Unabhaengiger Vektor aus pymacaroons 0.13 (V2; pymacaroons schreibt ein leeres
#: Location-Feld, go-macaroon nicht): Root ``b"r" * 32``, Identifier
#: ``00 00 || 00..1f || ab * 32``, die drei KAI-Caveats plus ein von pymacaroons
#: angehaengtes ``preimage=11..11``. Erzeugt am 27.09.2026.
_PYMACAROONS_VECTOR = (
    "02010002420000000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e"
    "1fabababababababababababababababababababababababababababababababab0002157365"
    "7276696365733d6b61692d6f7261636c653a300002206b61692d6f7261636c655f6361706162"
    "696c69746965733d76657264696374730002216b61692d6f7261636c655f76616c69645f756e"
    "74696c3d31373930343630303030000249707265696d6167653d313131313131313131313131"
    "3131313131313131313131313131313131313131313131313131313131313131313131313131"
    "313131313131313131313131313100000620aad3d8de0ff91c6815c9688875aa757a8a208f9c"
    "64715462d1a60178d50deec6"
)


def test_macaroon_v2_matches_an_independent_implementation() -> None:
    raw = bytes.fromhex("".join(_PYMACAROONS_VECTOR))
    mac = mv2.decode(raw)
    assert mac.caveats[-1] == b"preimage=" + b"1" * 64
    assert mv2.signature_valid(mac, b"r" * 32) is True
    assert mv2.signature_valid(mac, b"s" * 32) is False
    # KAI kodiert wie go-macaroon: ohne leeres Location-Feld, sonst bytegleich
    assert mv2.encode(mac) == raw.replace(b"\x02\x01\x00\x02", b"\x02\x02", 1)
