"""L402 pay-per-call paywall primitives (UC-2/3/4 foundation).

L402 flow (accounts-free, machine-payable):
  1. Client hits a protected resource with no/invalid auth.
  2. Server mints a Lightning invoice (payment_hash H) + a SIGNED access token
     binding H (+ expiry + scope), and answers ``402`` with
     ``WWW-Authenticate: L402 token="<t>", invoice="<bolt11>"``.
  3. Client pays the invoice → learns the preimage P (where sha256(P) == H).
  4. Client retries with ``Authorization: L402 <token>:<preimage_hex>``.
  5. Server grants iff: token HMAC valid + not expired + sha256(P) == H.

This module is the PURE, capital-free, fully-testable core — no network, no node,
no funds. Invoice MINTING (the only node write) lives in the gated value layer;
PAYMENT happens off-system. KAI only signs + verifies tokens here.

Token wire form: ``<payment_hash_hex>.<expiry_epoch>.<scope_b64url>.<sig_hex>``
where ``sig = HMAC-SHA256(secret, "<payment_hash_hex>.<expiry>.<scope_b64url>")``.
A self-contained signed token (not a full macaroon) — equivalent gating, zero
extra deps; can be upgraded to caveated macaroons later without changing callers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass

_DEFAULT_TTL_S = 3600


class L402Error(ValueError):
    """Malformed token / header — never leaks secret material."""


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sig(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def mint_token(
    payment_hash_hex: str, *, secret: str, ttl_s: int = _DEFAULT_TTL_S, scope: str = ""
) -> str:
    """Sign an access token binding a payment_hash (+ expiry + scope)."""
    if not secret:
        raise L402Error("l402 secret not configured")
    ph = payment_hash_hex.strip().lower()
    if len(ph) != 64 or not all(c in "0123456789abcdef" for c in ph):
        raise L402Error("payment_hash must be 32-byte hex")
    expiry = int(time.time()) + int(ttl_s)
    scope_b64 = _b64u(scope.encode("utf-8"))
    payload = f"{ph}.{expiry}.{scope_b64}"
    return f"{payload}.{_sig(secret, payload)}"


def build_challenge_header(token: str, invoice: str) -> str:
    """The ``WWW-Authenticate`` value for the 402 response."""
    return f'L402 token="{token}", invoice="{invoice}"'


def parse_authorization(header: str) -> tuple[str, str]:
    """Parse ``L402 <token>:<preimage_hex>`` (also tolerates the legacy ``LSAT``
    scheme). Returns ``(token, preimage_hex)``; raises L402Error on malformed."""
    if not header:
        raise L402Error("missing Authorization header")
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].upper() not in ("L402", "LSAT"):
        raise L402Error("not an L402 Authorization header")
    token, _, preimage = parts[1].partition(":")
    if not token or not preimage:
        raise L402Error("expected '<token>:<preimage_hex>'")
    return token.strip(), preimage.strip().lower()


@dataclass(frozen=True)
class L402Verdict:
    valid: bool
    reason: str = ""
    payment_hash: str = ""
    scope: str = ""


def verify(token: str, preimage_hex: str, *, secret: str, now: int | None = None) -> L402Verdict:
    """Verify a token+preimage. Never raises — returns an honest verdict."""
    if not secret:
        return L402Verdict(False, "l402 secret not configured")
    try:
        ph, expiry_s, scope_b64, sig = token.split(".", 3)
    except ValueError:
        return L402Verdict(False, "malformed token")
    payload = f"{ph}.{expiry_s}.{scope_b64}"
    if not hmac.compare_digest(sig, _sig(secret, payload)):
        return L402Verdict(False, "bad signature")
    try:
        expiry = int(expiry_s)
        scope = _unb64u(scope_b64).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return L402Verdict(False, "malformed token fields")
    if (now if now is not None else int(time.time())) > expiry:
        return L402Verdict(False, "token expired", payment_hash=ph, scope=scope)
    try:
        preimage = bytes.fromhex(preimage_hex)
    except ValueError:
        return L402Verdict(False, "preimage not hex", payment_hash=ph, scope=scope)
    if hashlib.sha256(preimage).hexdigest() != ph:
        return L402Verdict(
            False, "preimage does not match payment_hash", payment_hash=ph, scope=scope
        )
    return L402Verdict(True, "ok", payment_hash=ph, scope=scope)
