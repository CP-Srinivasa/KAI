"""L402 pay-per-call paywall primitives (UC-2/3/4 foundation).

L402 flow (accounts-free, machine-payable), per the Lightning Labs spec:
  1. Client hits a protected resource with no/invalid auth.
  2. Server mints a Lightning invoice (payment_hash H) + a macaroon binding H
     (+ scope + expiry as caveats), and answers ``402`` with
     ``WWW-Authenticate: L402 macaroon="<base64>", invoice="<bolt11>"``.
  3. Client pays the invoice → learns the preimage P (where sha256(P) == H).
  4. Client retries with ``Authorization: L402 <base64 macaroon>:<preimage_hex>``.
  5. Server grants iff: macaroon chain valid + caveats satisfied + sha256(P) == H.

Audit A5 (27.09.2026): the token used to be a KAI-only dotted string with a
``token=`` challenge — ``lnget``/aperture clients could not use it. It is now a
real V2 macaroon (:mod:`app.lightning.macaroon_v2`) in aperture's format:

* identifier = ``uint16 version 0 || payment_hash (32) || token_id (32)``
* caveats ``services=kai-oracle:0``, ``kai-oracle_capabilities=<scope>``,
  ``kai-oracle_valid_until=<unix>``; a client may APPEND caveats (aperture adds
  ``preimage=<hex>``) — every ``preimage`` caveat must match the presented
  preimage, unknown caveats are skipped (spec), none can be removed.
* root key per token = ``HMAC(l402_secret, label || identifier)`` — stateless.

Tokens minted before A5 (``<ph>.<expiry>.<scope_b64url>.<sig_hex>``) still
verify: already issued access and stored timestamp jobs replay with them.

Pure, capital-free, fully-testable — no network, no node, no funds.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import struct
import time
from dataclasses import dataclass

from app.lightning.macaroon_v2 import MacaroonError, decode, encode, mint, signature_valid

_DEFAULT_TTL_S = 3600
SERVICE = "kai-oracle"
_ROOT_KEY_LABEL = b"kai-l402-root-v1|"
_IDENTIFIER_V0 = 0
_IDENTIFIER_LEN = 2 + 32 + 32
_CAVEAT_SERVICES = "services"
_CAVEAT_CAPABILITIES = f"{SERVICE}_capabilities"
_CAVEAT_VALID_UNTIL = f"{SERVICE}_valid_until"
_CAVEAT_PREIMAGE = "preimage"


class L402Error(ValueError):
    """Malformed token / header — never leaks secret material."""


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _is_legacy(token: str) -> bool:
    """Pre-A5 tokens carry dots; base64 never does."""
    return "." in token


def _root_key(secret: str, identifier: bytes) -> bytes:
    return hmac.new(secret.encode("utf-8"), _ROOT_KEY_LABEL + identifier, hashlib.sha256).digest()


def _check_payment_hash(payment_hash_hex: str) -> str:
    ph = payment_hash_hex.strip().lower()
    if len(ph) != 64 or not all(c in "0123456789abcdef" for c in ph):
        raise L402Error("payment_hash must be 32-byte hex")
    return ph


def mint_token(
    payment_hash_hex: str,
    *,
    secret: str,
    ttl_s: int = _DEFAULT_TTL_S,
    scope: str = "",
    now: int | None = None,
) -> str:
    """Mint a base64 L402 macaroon binding a payment_hash (+ scope + expiry)."""
    if not secret:
        raise L402Error("l402 secret not configured")
    ph = _check_payment_hash(payment_hash_hex)
    if "," in scope or "\n" in scope:
        raise L402Error("scope must not contain ',' or newlines")
    expiry = (now if now is not None else int(time.time())) + int(ttl_s)
    identifier = struct.pack(">H", _IDENTIFIER_V0) + bytes.fromhex(ph) + os.urandom(32)
    caveats = (
        f"{_CAVEAT_SERVICES}={SERVICE}:0".encode(),
        f"{_CAVEAT_CAPABILITIES}={scope}".encode(),
        f"{_CAVEAT_VALID_UNTIL}={expiry}".encode(),
    )
    mac = mint(_root_key(secret, identifier), identifier, caveats)
    return base64.b64encode(encode(mac)).decode("ascii")


def _decode_macaroon_b64(token: str) -> bytes:
    """Standard base64 first (spec), then URL-safe without padding (as lnget does)."""
    try:
        return base64.b64decode(token, validate=True)
    except (binascii.Error, ValueError):
        try:
            return _unb64u(token)
        except (binascii.Error, ValueError) as exc:
            raise L402Error("macaroon is not base64") from exc


def _caveats(token: str) -> tuple[str, list[tuple[str, str]], bytes, bytes]:
    """Decode a macaroon token → (payment_hash, [(key, value)], identifier, raw)."""
    raw = _decode_macaroon_b64(token)
    try:
        mac = decode(raw)
    except MacaroonError as exc:
        raise L402Error(f"malformed macaroon: {exc}") from exc
    ident = mac.identifier
    if len(ident) != _IDENTIFIER_LEN or struct.unpack(">H", ident[:2])[0] != _IDENTIFIER_V0:
        raise L402Error("unsupported macaroon identifier")
    pairs: list[tuple[str, str]] = []
    for caveat in mac.caveats:
        key, sep, value = caveat.decode("utf-8", errors="replace").partition("=")
        if sep:
            pairs.append((key.strip(), value.strip()))
    return ident[2:34].hex(), pairs, ident, raw


def token_expiry(token: str) -> int:
    """The signed expiry (epoch s) of a token KAI just minted — for display only.

    Does NOT verify the signature; access decisions always go through ``verify``.
    """
    if _is_legacy(token):
        try:
            return int(token.split(".", 3)[1])
        except (IndexError, ValueError) as exc:
            raise L402Error("malformed token") from exc
    _ph, pairs, _ident, _raw = _caveats(token)
    try:
        return min(int(v) for k, v in pairs if k == _CAVEAT_VALID_UNTIL)
    except ValueError as exc:
        raise L402Error("macaroon carries no valid_until caveat") from exc


def token_payment_hash(token: str) -> str:
    """The payment_hash a macaroon token binds (from its identifier) — no signature check."""
    ph, _pairs, _ident, _raw = _caveats(token)
    return ph


def build_challenge_header(token: str, invoice: str) -> str:
    """The ``WWW-Authenticate`` value for the 402 response (L402 spec)."""
    return f'L402 macaroon="{token}", invoice="{invoice}"'


def parse_authorization(header: str) -> tuple[str, str]:
    """Parse ``L402 <token>:<preimage_hex>`` (also tolerates the legacy ``LSAT``
    scheme). Several comma-separated macaroons: the first is KAI's. Returns
    ``(token, preimage_hex)``; raises L402Error on malformed."""
    if not header:
        raise L402Error("missing Authorization header")
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].upper() not in ("L402", "LSAT"):
        raise L402Error("not an L402 Authorization header")
    token, _, preimage = parts[1].rpartition(":")
    token = token.split(",", 1)[0].strip()
    if not token or not preimage.strip():
        raise L402Error("expected '<token>:<preimage_hex>'")
    return token, preimage.strip().lower()


@dataclass(frozen=True)
class L402Verdict:
    valid: bool
    reason: str = ""
    payment_hash: str = ""
    scope: str = ""


def _sig(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_legacy(token: str, secret: str) -> tuple[L402Verdict | None, str, str, int]:
    try:
        ph, expiry_s, scope_b64, sig = token.split(".", 3)
    except ValueError:
        return L402Verdict(False, "malformed token"), "", "", 0
    payload = f"{ph}.{expiry_s}.{scope_b64}"
    if not hmac.compare_digest(sig, _sig(secret, payload)):
        return L402Verdict(False, "bad signature"), "", "", 0
    try:
        return None, ph, _unb64u(scope_b64).decode("utf-8"), int(expiry_s)
    except (ValueError, UnicodeDecodeError):
        return L402Verdict(False, "malformed token fields"), "", "", 0


def _verify_macaroon(
    token: str, secret: str, preimage_hex: str
) -> tuple[L402Verdict | None, str, str, int]:
    try:
        ph, pairs, ident, raw = _caveats(token)
    except L402Error as exc:
        return L402Verdict(False, str(exc)), "", "", 0
    if not signature_valid(decode(raw), _root_key(secret, ident)):
        return L402Verdict(False, "bad signature"), "", "", 0
    services = [v for k, v in pairs if k == _CAVEAT_SERVICES]
    if not services or any(
        f"{SERVICE}:" not in {s.strip()[: len(SERVICE) + 1] for s in v.split(",")} for v in services
    ):
        return L402Verdict(False, "service not granted", payment_hash=ph), "", "", 0
    capabilities = [v for k, v in pairs if k == _CAVEAT_CAPABILITIES]
    if not capabilities:
        return L402Verdict(False, "no capability caveat", payment_hash=ph), "", "", 0
    scope = capabilities[0]
    if any(scope not in {c.strip() for c in v.split(",")} for v in capabilities):
        return L402Verdict(False, "capability narrowed away", payment_hash=ph), "", "", 0
    try:
        expiry = min(int(v) for k, v in pairs if k == _CAVEAT_VALID_UNTIL)
    except ValueError:
        return L402Verdict(False, "no valid_until caveat", payment_hash=ph), "", "", 0
    if any(v.lower() != preimage_hex for k, v in pairs if k == _CAVEAT_PREIMAGE):
        return L402Verdict(False, "preimage caveat mismatch", payment_hash=ph), "", "", 0
    return None, ph, scope, expiry


def verify(
    token: str,
    preimage_hex: str,
    *,
    secret: str,
    now: int | None = None,
    allow_expired: bool = False,
) -> L402Verdict:
    """Verify a token+preimage. Never raises — returns an honest verdict."""
    if not secret:
        return L402Verdict(False, "l402 secret not configured")
    preimage_hex = preimage_hex.strip().lower()
    if _is_legacy(token):
        early, ph, scope, expiry = _verify_legacy(token, secret)
    else:
        early, ph, scope, expiry = _verify_macaroon(token, secret, preimage_hex)
    if early is not None:
        return early
    expired = (now if now is not None else int(time.time())) > expiry
    if expired and not allow_expired:
        return L402Verdict(False, "token expired", payment_hash=ph, scope=scope)
    try:
        preimage = bytes.fromhex(preimage_hex)
    except ValueError:
        return L402Verdict(False, "preimage not hex", payment_hash=ph, scope=scope)
    if hashlib.sha256(preimage).hexdigest() != ph:
        return L402Verdict(
            False, "preimage does not match payment_hash", payment_hash=ph, scope=scope
        )
    return L402Verdict(True, "ok_expired" if expired else "ok", payment_hash=ph, scope=scope)


__all__ = [
    "SERVICE",
    "L402Error",
    "L402Verdict",
    "build_challenge_header",
    "mint_token",
    "parse_authorization",
    "token_expiry",
    "token_payment_hash",
    "verify",
]
