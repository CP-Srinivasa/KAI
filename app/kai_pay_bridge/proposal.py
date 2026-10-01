"""Format und Signatur der KI-Zahlungsvorschlaege.

Byte-kompatibel zu ``packages/proposal/proposal.ts`` (kai-pay):

Quelle:     ``kaisrc1.<b64url(JSON {v:1, name, pub})>``
            pub = SPKI (DER) eines ECDSA-P-256-Schluessels
Vorschlag:  ``kaiprop1.<b64url(JSON Nutzlast)>.<b64url(Signatur)>``
            Signatur = ECDSA P-256 / SHA-256 im IEEE-P1363-Format (r||s, 64 Byte) ueber die
            ASCII-Bytes von ``"kaiprop1.<Nutzlast>"`` - signiert wird der kodierte Text selbst,
            keine JSON-Kanonisierung noetig.
Kennung:    die ersten 16 Byte von SHA-256(SPKI) als Hex (32 Zeichen); Anzeige als
            Fingerabdruck in Vierergruppen.

Die Grenzen (Laufzeit, Betrag, Textlaengen, verbotene Zeichen) sind dieselben wie in der Wallet -
ein Vorschlag, den die Wallet ablehnen wuerde, wird hier gar nicht erst signiert.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

PROP_PREFIX = "kaiprop1."
SRC_PREFIX = "kaisrc1."
MAX_TTL_S = 7 * 24 * 3600
MAX_SAT = 1_000_000
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs"})
_UNSAFE_CHARS = frozenset("ᅟᅠㅤﾠ")  # unsichtbare Hangul-Fueller
_WS = re.compile(r"\s")


class ProposalError(ValueError):
    """Vorschlag oder Quelle verletzt das Format - wird nicht signiert."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ProposalError("b64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _js_length(text: str) -> int:
    """Laenge wie JavaScript ``String.length`` (UTF-16-Einheiten) - die Wallet zaehlt so."""
    return len(text.encode("utf-16-le")) // 2


def safe_text(text: object, max_len: int) -> bool:
    """Wie ``safeText`` der Wallet: nicht leer, Laengengrenze, keine Steuer-/Richtungszeichen."""
    if not isinstance(text, str) or not text or _js_length(text) > max_len:
        return False
    return not any(
        unicodedata.category(c) in _UNSAFE_CATEGORIES or c in _UNSAFE_CHARS for c in text
    )


def key_id(spki: bytes) -> str:
    return hashlib.sha256(spki).digest()[:16].hex()


def fingerprint(kid: str) -> str:
    return "-".join(kid[i : i + 4] for i in range(0, len(kid), 4)).upper()


def spki_of(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def encode_source(name: str, spki: bytes) -> str:
    """Quellen-Code fuer die Wallet (oeffentlich - enthaelt nur den oeffentlichen Schluessel)."""
    if not safe_text(name, 40):
        raise ProposalError("name")
    body = json.dumps(
        {"v": 1, "name": name, "pub": b64url(spki)}, ensure_ascii=False, separators=(",", ":")
    )
    return SRC_PREFIX + b64url(body.encode("utf-8"))


@dataclass(frozen=True)
class Proposal:
    id: str
    src: str
    iat: int
    exp: int
    to: str
    sat: int
    purpose: str


def _check(p: Proposal) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{22}", p.id) or not re.fullmatch(r"[0-9a-f]{32}", p.src):
        raise ProposalError("id")
    if p.exp <= p.iat or p.exp - p.iat > MAX_TTL_S:
        raise ProposalError("ttl")
    if isinstance(p.sat, bool) or not isinstance(p.sat, int) or not 0 < p.sat <= MAX_SAT:
        raise ProposalError("sat")
    if not safe_text(p.to, 2000) or _WS.search(p.to):
        raise ProposalError("to")
    if not safe_text(p.purpose, 140):
        raise ProposalError("purpose")


def sign_proposal(
    key: ec.EllipticCurvePrivateKey,
    *,
    to: str,
    sat: int,
    purpose: str,
    ttl_s: int = 24 * 3600,
    now: int | None = None,
) -> str:
    """Signiert einen Vorschlag. :class:`ProposalError`, wenn die Wallet ihn ablehnen wuerde."""
    if not isinstance(key.curve, ec.SECP256R1):
        raise ProposalError("curve")
    iat = int(time.time()) if now is None else now
    p = Proposal(
        id=b64url(secrets.token_bytes(16)),
        src=key_id(spki_of(key)),
        iat=iat,
        exp=iat + ttl_s,
        to=to.strip(),
        sat=sat,
        purpose=purpose.strip(),
    )
    _check(p)
    payload = json.dumps(
        {
            "v": 1,
            "id": p.id,
            "src": p.src,
            "iat": p.iat,
            "exp": p.exp,
            "to": p.to,
            "sat": p.sat,
            "purpose": p.purpose,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    signed = PROP_PREFIX + b64url(payload.encode("utf-8"))
    r, s = decode_dss_signature(key.sign(signed.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    return f"{signed}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def verify_proposal(token: str, spki: bytes) -> Proposal:
    """Gegenprobe (Tests, Diagnose): Form und Signatur; die Laufzeit prueft die Wallet."""
    if not token.startswith(PROP_PREFIX):
        raise ProposalError("prefix")
    parts = token[len(PROP_PREFIX) :].split(".")
    if len(parts) != 2:
        raise ProposalError("parts")
    raw = json.loads(unb64url(parts[0]).decode("utf-8"))
    if set(raw) != {"v", "id", "src", "iat", "exp", "to", "sat", "purpose"} or raw["v"] != 1:
        raise ProposalError("fields")
    p = Proposal(**{k: raw[k] for k in ("id", "src", "iat", "exp", "to", "sat", "purpose")})
    _check(p)
    if p.src != key_id(spki):
        raise ProposalError("source")
    sig = unb64url(parts[1])
    if len(sig) != 64:
        raise ProposalError("sig")
    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    pub = serialization.load_der_public_key(spki)
    if not isinstance(pub, ec.EllipticCurvePublicKey):
        raise ProposalError("key")
    try:
        pub.verify(der, (PROP_PREFIX + parts[0]).encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except Exception as exc:  # cryptography.exceptions.InvalidSignature
        raise ProposalError("signature") from exc
    return p
