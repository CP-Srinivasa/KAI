"""Macaroons im V2-Binaerformat — nur das, was L402 braucht (Audit A5, 27.09.2026).

Kompatibel zu ``gopkg.in/macaroon.v2`` (aperture, lnget) und ``pymacaroons``:

* Schluessel: ``derived = HMAC-SHA256(key="macaroons-key-generator", msg=root_key)``
* Signatur: ``sig = HMAC(derived, identifier)``, je First-Party-Caveat
  ``sig = HMAC(sig, caveat_id)`` — ein Client kann Caveats ANHAENGEN (aperture
  haengt ``preimage=<hex>`` an), aber keinen entfernen.
* V2-Binaerform: Version ``0x02``, Felder ``<typ><uvarint laenge><bytes>``
  (1 = Location, 2 = Identifier, 4 = Verification-ID, 6 = Signatur), ``0x00``
  beendet einen Abschnitt.

Third-Party-Caveats (Verification-ID gesetzt) werden abgelehnt: KAI stellt keine
aus und kann sie nicht pruefen. Reines Modul — kein Netz, kein Zustand.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

_KEY_GENERATOR = b"macaroons-key-generator"
_VERSION_V2 = 2
_FIELD_EOS = 0
_FIELD_LOCATION = 1
_FIELD_IDENTIFIER = 2
_FIELD_VID = 4
_FIELD_SIGNATURE = 6
_SIG_LEN = 32


class MacaroonError(ValueError):
    """Kaputte oder nicht unterstuetzte Macaroon-Bytes."""


@dataclass(frozen=True)
class Macaroon:
    identifier: bytes
    caveats: tuple[bytes, ...]
    signature: bytes
    location: str = ""


def _hmac(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def signature_for(root_key: bytes, identifier: bytes, caveats: tuple[bytes, ...]) -> bytes:
    """Die HMAC-Kette ueber Identifier und First-Party-Caveats."""
    sig = _hmac(_hmac(_KEY_GENERATOR, root_key), identifier)
    for caveat in caveats:
        sig = _hmac(sig, caveat)
    return sig


def mint(root_key: bytes, identifier: bytes, caveats: tuple[bytes, ...]) -> Macaroon:
    return Macaroon(identifier, caveats, signature_for(root_key, identifier, caveats))


def signature_valid(mac: Macaroon, root_key: bytes) -> bool:
    expected = signature_for(root_key, mac.identifier, mac.caveats)
    return hmac.compare_digest(expected, mac.signature)


def _uvarint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _field(kind: int, data: bytes) -> bytes:
    return bytes([kind]) + _uvarint(len(data)) + data


def encode(mac: Macaroon) -> bytes:
    """V2-Binaerform (wie ``macaroon.MarshalBinary`` in Go)."""
    out = bytearray([_VERSION_V2])
    if mac.location:
        out += _field(_FIELD_LOCATION, mac.location.encode("utf-8"))
    out += _field(_FIELD_IDENTIFIER, mac.identifier)
    out.append(_FIELD_EOS)
    for caveat in mac.caveats:
        out += _field(_FIELD_IDENTIFIER, caveat)
        out.append(_FIELD_EOS)
    out.append(_FIELD_EOS)
    out += _field(_FIELD_SIGNATURE, mac.signature)
    return bytes(out)


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise MacaroonError("truncated macaroon")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def uvarint(self) -> int:
        shift = value = 0
        for _ in range(10):
            b = self.byte()
            value |= (b & 0x7F) << shift
            if b < 0x80:
                return value
            shift += 7
        raise MacaroonError("varint too long")

    def take(self, length: int) -> bytes:
        if length < 0 or self.pos + length > len(self.data):
            raise MacaroonError("truncated macaroon field")
        chunk = self.data[self.pos : self.pos + length]
        self.pos += length
        return chunk

    def section(self) -> dict[int, bytes]:
        """Felder bis zum EOS-Byte; ein Feldtyp darf nur einmal vorkommen."""
        fields: dict[int, bytes] = {}
        while True:
            kind = self.byte()
            if kind == _FIELD_EOS:
                return fields
            if kind in fields:
                raise MacaroonError(f"duplicate field {kind}")
            fields[kind] = self.take(self.uvarint())


def decode(raw: bytes) -> Macaroon:
    """V2-Binaerform lesen. Wirft :class:`MacaroonError` bei allem Unerwarteten."""
    reader = _Reader(raw)
    if reader.byte() != _VERSION_V2:
        raise MacaroonError("not a V2 macaroon")
    head = reader.section()
    if _FIELD_IDENTIFIER not in head or set(head) - {_FIELD_LOCATION, _FIELD_IDENTIFIER}:
        raise MacaroonError("bad macaroon header")
    caveats: list[bytes] = []
    while True:
        if reader.pos < len(reader.data) and reader.data[reader.pos] == _FIELD_EOS:
            reader.pos += 1
            break
        section = reader.section()
        if _FIELD_VID in section:
            raise MacaroonError("third-party caveats are not supported")
        if _FIELD_IDENTIFIER not in section or set(section) - {_FIELD_LOCATION, _FIELD_IDENTIFIER}:
            raise MacaroonError("bad caveat")
        caveats.append(section[_FIELD_IDENTIFIER])
    if reader.byte() != _FIELD_SIGNATURE:
        raise MacaroonError("missing signature")
    signature = reader.take(reader.uvarint())
    if len(signature) != _SIG_LEN or reader.pos != len(raw):
        raise MacaroonError("bad signature field")
    location = head.get(_FIELD_LOCATION, b"").decode("utf-8", errors="strict")
    return Macaroon(head[_FIELD_IDENTIFIER], tuple(caveats), signature, location)


__all__ = [
    "Macaroon",
    "MacaroonError",
    "decode",
    "encode",
    "mint",
    "signature_for",
    "signature_valid",
]
