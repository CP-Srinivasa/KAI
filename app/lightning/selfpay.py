"""Self-pay helpers for the L402 truth-oracle learning loop (pure, read-only).

Turns a server ``402`` challenge into the retry ``Authorization`` header once the
invoice settles — the parsing/matching half of the operator self-pay dry-run
(``scripts/ln_selfpay_test.py``). No node, no HTTP, no capital: the imperative
shell (HTTP + lnd polling) lives in the script, so this stays unit-testable.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from app.lightning.l402 import L402Error, token_payment_hash

# Server challenge (L402 spec, Audit A5): `L402 macaroon="<b64>", invoice="<bolt11>"`;
# pre-A5 servers sent `token="<t>"` — both parse. Neither value carries quotes.
_CHALLENGE_RE = re.compile(
    r'(?:macaroon|token)="([^"]+)".*?invoice="([^"]+)"', re.IGNORECASE | re.DOTALL
)
_HEX64_RE = re.compile(r"[0-9a-fA-F]{64}")


def parse_l402_challenge(www_authenticate: str) -> tuple[str, str]:
    """Parse a ``WWW-Authenticate: L402 macaroon="…", invoice="…"`` header.

    Returns ``(token, bolt11_invoice)``; raises ``ValueError`` on a malformed one.
    """
    if not www_authenticate:
        raise ValueError("missing WWW-Authenticate header")
    match = _CHALLENGE_RE.search(www_authenticate)
    if not match:
        raise ValueError(f"not an L402 challenge: {www_authenticate!r}")
    return match.group(1), match.group(2)


def payment_hash_from_token(token: str) -> str:
    """The payment_hash bound by the token: from the macaroon identifier (L402),
    or field 0 of a pre-A5 ``ph.expiry.scope.sig`` token."""
    if "." not in token:
        try:
            return token_payment_hash(token)
        except L402Error as exc:
            raise ValueError(str(exc)) from exc
    ph = token.split(".", 1)[0].strip().lower()
    if not ph:
        raise ValueError("token carries no payment_hash")
    return ph


def build_l402_authorization(token: str, preimage_hex: str) -> str:
    """The retry header value: ``L402 <token>:<preimage_hex>`` (inverse of
    :func:`app.lightning.l402.parse_authorization`)."""
    return f"L402 {token}:{preimage_hex.strip().lower()}"


def _preimage_to_hex(value: str) -> str:
    """lnd REST returns ``r_preimage`` base64; L402 wants lowercase hex. A value
    that is already 64 hex chars is passed through (some builds return hex)."""
    candidate = value.strip()
    if _HEX64_RE.fullmatch(candidate):
        return candidate.lower()
    return base64.b64decode(candidate).hex()


def find_settled_preimage(invoices: list[dict[str, Any]], *, payment_request: str) -> str | None:
    """From an lnd ``list_invoices`` array, return the hex preimage of the SETTLED
    invoice matching ``payment_request`` — else ``None`` (no match / not yet settled).

    Matches on the exact BOLT11 string (robust vs base64/hex ``r_hash`` encoding).
    """
    for inv in invoices:
        if str(inv.get("payment_request", "")) != payment_request:
            continue
        settled = inv.get("settled") is True or str(inv.get("state", "")) == "SETTLED"
        if not settled:
            return None
        preimage = inv.get("r_preimage")
        if not isinstance(preimage, str) or not preimage:
            return None
        return _preimage_to_hex(preimage)
    return None


__all__ = [
    "build_l402_authorization",
    "find_settled_preimage",
    "parse_l402_challenge",
    "payment_hash_from_token",
]
