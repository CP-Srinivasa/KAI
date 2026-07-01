"""Provenance / ownership ledger + compliance export (ADR 0013).

Nachweis-Hygiene (belegen, nicht verschleiern): records wallet-ownership proofs
(TFR Satoshi-test / signature), the withdrawal whitelist and the transfer log, and
derives a consolidated SoF/TFR/tax export. Crucially it flags GAPS — wallets that
moved value but have no recorded ownership proof — so missing evidence is surfaced,
not hidden.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.compliance.provenance import (
    ProvenanceRecord,
    append_provenance_record,
    compute_compliance_export,
    read_provenance_records,
)


def test_record_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        ProvenanceRecord(
            kind="laundering", timestamp="2026-07-01T00:00:00Z", wallet_address="bc1qxyz"
        )


def test_record_rejects_empty_wallet() -> None:
    with pytest.raises(ValidationError):
        ProvenanceRecord(kind="whitelist", timestamp="2026-07-01T00:00:00Z", wallet_address="")


def test_append_and_read_roundtrip(tmp_path) -> None:
    path = tmp_path / "provenance.jsonl"
    rec = ProvenanceRecord(
        kind="ownership_proof",
        timestamp="2026-07-01T00:00:00Z",
        wallet_address="bc1qself",
        method="satoshi_test",
        tx_hash="abc123",
    )
    append_provenance_record(rec, path=path)
    rows = read_provenance_records(path)
    assert len(rows) == 1
    assert rows[0]["kind"] == "ownership_proof"
    assert rows[0]["wallet_address"] == "bc1qself"


def test_export_aggregates_and_flags_missing_proof() -> None:
    records = [
        {
            "kind": "ownership_proof",
            "timestamp": "t1",
            "wallet_address": "W_OK",
            "method": "signature",
        },
        {"kind": "whitelist", "timestamp": "t2", "wallet_address": "W_OK"},
        {
            "kind": "transfer",
            "timestamp": "t3",
            "wallet_address": "W_OK",
            "amount": 100.0,
            "currency": "eur",
        },
        # W_GAP moved value but has NO ownership proof -> must be flagged
        {
            "kind": "transfer",
            "timestamp": "t4",
            "wallet_address": "W_GAP",
            "amount": 2000.0,
            "currency": "eur",
        },
    ]
    export = compute_compliance_export(records)
    assert export["totals"]["transfer_count"] == 2
    assert export["totals"]["ownership_proofs"] == 1
    assert export["wallets"]["W_OK"]["has_ownership_proof"] is True
    assert export["wallets"]["W_GAP"]["has_ownership_proof"] is False
    # the gap (transfer without proof) is surfaced, not hidden
    assert "W_GAP" in export["gaps"]
    assert "W_OK" not in export["gaps"]


def test_export_empty_is_wellformed() -> None:
    export = compute_compliance_export([])
    assert export["totals"]["transfer_count"] == 0
    assert export["gaps"] == []
