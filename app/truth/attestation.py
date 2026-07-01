"""Verifiable truth attestation (ADR 0013, Tier-1 frontier primitive).

A deterministic content hash over a signal + its provenance. The payload is
canonicalised (sorted keys at every depth, no incidental whitespace, UTF-8) and
hashed with SHA-256, so ANY third party can recompute the hash from the published
payload and verify the claim was not altered after the fact — verifiable truth
instead of trust. PURE: no I/O, no clock, no state; anchoring an attestation
(e.g. OTS/on-chain) is a separate, gated concern at the call site.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

_ALGO = "sha256"


def canonicalize(payload: Mapping[str, Any]) -> str:
    """Render the payload as canonical JSON (sorted keys, compact, UTF-8 text).

    The same logical content always yields byte-identical output regardless of
    key insertion order — the precondition for a recomputable content hash.
    NaN/Infinity have no canonical JSON form and fail loud (``ValueError``)
    instead of silently hashing non-JSON.
    """
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def compute_attestation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compute the deterministic attestation for a payload (pure).

    Returns the canonical form alongside hash+algo so a verifier can either
    recompute from the raw payload or byte-compare the canonical string.
    """
    canonical = canonicalize(payload)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {"algo": _ALGO, "canonical": canonical, "hash": digest}


def verify_attestation(payload: Mapping[str, Any], attestation: Mapping[str, Any]) -> bool:
    """Recompute the payload's hash and compare against the attestation (fail-closed).

    Returns ``False`` for a missing/non-string hash or any mismatch — never raises
    on malformed attestations, so a corrupt record can only ever read as unverified.
    """
    expected = attestation.get("hash")
    if not isinstance(expected, str) or not expected:
        return False
    actual = compute_attestation(payload)["hash"]
    return hmac.compare_digest(actual, expected)
