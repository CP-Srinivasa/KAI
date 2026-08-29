"""Structural plan validation at the money-journal writer boundary (G2).

The cause of the G2 finding was not a clever attack. On 2026-08-05 a manual run
from the operator workstation wrote four rows into the PRODUCTION money journal
carrying the suite's own fixture values — ``node_pubkey="02ab"``,
``funding_txid_str="deadbeef"`` — and the v1→v2 migration then sealed them into
the hash chain. The chain did its job: it proves nothing was altered afterwards.
It never claimed the values were true.

So the guard belongs where the row is born, and it is split in two on purpose:

**Hard rejection** — only for values that cannot exist on Bitcoin, no matter the
path, the network or the caller. A 4-character node pubkey is not "unlikely", it
is impossible: a compressed secp256k1 point is exactly 33 bytes. There is no
legitimate call these checks can block, which is the entire reason they may be
fail-closed on the capital path.

**Soft flags** — for values that look wrong but might be legitimate. ``sat_per_vbyte
== 0`` is the live example: it appears in the fixtures, but whether zero means
"use the node default" on some path is UNVERIFIED. A detector that hard-rejects an
unverified suspicion blocks real work, so this one only annotates the row.

The distinction is the lesson from the bit-exact detectors that fired three times
on innocent data: a guard without false-positive hardening costs more trust than
it buys, and every guard needs a positive control proving it lets the real thing
through.
"""

from __future__ import annotations

from typing import Any

#: A compressed secp256k1 public key: 33 bytes = 66 hex chars, prefix 02 or 03.
_PUBKEY_HEX_LEN = 66
#: A Bitcoin txid is a 32-byte hash rendered as 64 hex characters.
_TXID_HEX_LEN = 64
#: BIP-173 fixes the shortest possible bech32 address at 14 characters; the
#: shortest base58 address is longer still. Anything below this is not an address.
_MIN_ADDRESS_LEN = 14

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _is_hex(value: str) -> bool:
    return bool(value) and all(ch in _HEX_DIGITS for ch in value)


def _pubkey_defect(field: str, value: Any) -> str | None:
    text = str(value or "")
    if not text:
        return None  # absence is another layer's business, not this one's
    if len(text) != _PUBKEY_HEX_LEN or not _is_hex(text):
        return (
            f"{field} is not a 33-byte compressed pubkey "
            f"({len(text)} chars, expected {_PUBKEY_HEX_LEN} hex)"
        )
    if text[:2] not in ("02", "03"):
        return f"{field} has prefix {text[:2]!r}; a compressed pubkey starts 02 or 03"
    return None


def _txid_defect(field: str, value: Any) -> str | None:
    text = str(value or "")
    if not text:
        return None
    if len(text) != _TXID_HEX_LEN or not _is_hex(text):
        return f"{field} is not a txid ({len(text)} chars, expected {_TXID_HEX_LEN} hex)"
    return None


def _address_defect(field: str, value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) < _MIN_ADDRESS_LEN:
        return (
            f"{field} is too short to be an address ({len(text)} chars, minimum {_MIN_ADDRESS_LEN})"
        )
    return None


def plan_structural_defects(action: str, plan: dict[str, Any]) -> list[str]:
    """Impossible values in a value-layer plan. Empty list = nothing provably wrong.

    Deliberately silent about anything it cannot decide with certainty: a missing
    field, an implausible amount, a peer we do not like. Those are policy, and
    policy does not belong in a structural guard.
    """
    defects: list[str] = []
    for field in ("node_pubkey_hex", "dest_pubkey_hex", "node_pubkey"):
        if field in plan:
            defect = _pubkey_defect(field, plan[field])
            if defect:
                defects.append(defect)
    for field in ("funding_txid_str", "txid", "closing_txid"):
        if field in plan:
            defect = _txid_defect(field, plan[field])
            if defect:
                defects.append(defect)
    for field in ("addr", "address", "recipient"):
        if field in plan:
            defect = _address_defect(field, plan[field])
            if defect:
                defects.append(defect)
    return defects


def plan_soft_flags(action: str, plan: dict[str, Any]) -> list[str]:
    """Suspicious-but-possible values. NEVER a rejection — an annotation only."""
    flags: list[str] = []
    if "sat_per_vbyte" in plan:
        try:
            if int(plan["sat_per_vbyte"]) == 0:
                flags.append("implausible_fee: sat_per_vbyte=0")
        except (TypeError, ValueError):
            flags.append("implausible_fee: sat_per_vbyte is not an integer")
    return flags


__all__ = ["plan_soft_flags", "plan_structural_defects"]
