"""Verifiable truth attestation (ADR 0013, Tier-1 frontier primitive).

A deterministic content hash over a signal + its provenance so a third party can
recompute it and verify the claim was not altered after the fact — verifiable
truth instead of trust. Pure, no I/O.
"""

from __future__ import annotations

from app.truth.attestation import compute_attestation, verify_attestation


def test_deterministic_regardless_of_key_order() -> None:
    a = compute_attestation({"signal": "long", "asset": "BTC", "p": 0.61})
    b = compute_attestation({"p": 0.61, "asset": "BTC", "signal": "long"})
    assert a["hash"] == b["hash"]
    assert a["algo"] == "sha256"


def test_nested_key_order_also_canonicalised() -> None:
    a = compute_attestation({"x": {"b": 1, "a": 2}})
    b = compute_attestation({"x": {"a": 2, "b": 1}})
    assert a["hash"] == b["hash"]


def test_hash_changes_when_payload_changes() -> None:
    a = compute_attestation({"signal": "long", "asset": "BTC"})
    b = compute_attestation({"signal": "short", "asset": "BTC"})
    assert a["hash"] != b["hash"]


def test_canonical_has_no_incidental_whitespace() -> None:
    att = compute_attestation({"a": 1, "b": 2})
    assert " " not in att["canonical"]


def test_verify_true_for_matching_and_false_for_tampered() -> None:
    payload = {"signal": "long", "asset": "BTC", "p": 0.61}
    att = compute_attestation(payload)
    assert verify_attestation(payload, att) is True
    tampered = {**payload, "p": 0.99}
    assert verify_attestation(tampered, att) is False


def test_verify_rejects_attestation_without_hash() -> None:
    assert verify_attestation({"a": 1}, {"algo": "sha256"}) is False


def test_nan_payload_fails_loud_not_silent() -> None:
    # NaN hat keine kanonische JSON-Form — ein Wahrheits-Primitiv muss laut
    # scheitern statt stillschweigend Nicht-JSON zu hashen.
    import pytest

    with pytest.raises(ValueError):
        compute_attestation({"p": float("nan")})
