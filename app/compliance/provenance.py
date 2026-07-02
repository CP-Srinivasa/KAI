"""Provenance / ownership ledger + compliance export (ADR 0013).

Nachweis-Hygiene: an append-only record of wallet-ownership proofs (TFR Satoshi-test
/ signature), the withdrawal whitelist and the transfer log, plus a pure aggregation
into a consolidated SoF/TFR/tax export. The export deliberately flags **gaps** —
wallets that moved value without a recorded ownership proof — so missing evidence is
surfaced, never hidden. Audit-to-prove, not to obscure.

This is **not** legal or tax advice; it is a documentation aid.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.storage.jsonl_io import read_jsonl_tolerant

ProvenanceKind = Literal["ownership_proof", "whitelist", "transfer"]

DEFAULT_PROVENANCE_LEDGER_PATH = Path("artifacts/compliance/provenance_ledger.jsonl")

_CAVEAT = (
    "nachweis-hygiene — records evidence to PROVE provenance (SoF/TFR/tax), never to "
    "obscure it; 'gaps' surface wallets that moved value without a recorded ownership "
    "proof. Not legal/tax advice."
)


class ProvenanceRecord(BaseModel):
    """One provenance/ownership/transfer entry. Validated before it is written."""

    kind: ProvenanceKind
    timestamp: str = Field(min_length=1)
    wallet_address: str = Field(min_length=1)
    method: str | None = None  # e.g. satoshi_test / signature (ownership_proof)
    tx_hash: str | None = None
    counterparty: str | None = None  # e.g. the exchange for a transfer
    amount: float | None = None
    currency: str | None = None
    note: str | None = None


def append_provenance_record(record: ProvenanceRecord, *, path: Path) -> None:
    """Append one validated record to the append-only provenance JSONL ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.model_dump(), ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_provenance_records(path: Path) -> list[dict[str, Any]]:
    """Read all provenance records (canonical tolerant JSONL reader)."""
    return read_jsonl_tolerant(path)


def compute_compliance_export(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate provenance records into a SoF/TFR/tax export (pure, read-only).

    Groups per wallet (ownership proof present?, methods, whitelisted, transfer
    count), lists transfers, and flags ``gaps`` — wallets with at least one transfer
    but no recorded ownership proof. Surfacing gaps is the point: missing evidence is
    made visible, not swept away.
    """
    wallets: dict[str, dict[str, Any]] = {}
    transfers: list[dict[str, Any]] = []
    ownership_proofs = 0

    for r in records:
        addr = r.get("wallet_address")
        if not addr:
            continue
        w = wallets.setdefault(
            addr,
            {
                "has_ownership_proof": False,
                "proof_methods": [],
                "whitelisted": False,
                "transfer_count": 0,
            },
        )
        kind = r.get("kind")
        if kind == "ownership_proof":
            w["has_ownership_proof"] = True
            method = r.get("method")
            if method:
                w["proof_methods"].append(method)
            ownership_proofs += 1
        elif kind == "whitelist":
            w["whitelisted"] = True
        elif kind == "transfer":
            w["transfer_count"] += 1
            transfers.append(dict(r))

    gaps = sorted(
        addr
        for addr, w in wallets.items()
        if w["transfer_count"] > 0 and not w["has_ownership_proof"]
    )
    totals = {
        "transfer_count": len(transfers),
        "ownership_proofs": ownership_proofs,
        "whitelisted_wallets": sum(1 for w in wallets.values() if w["whitelisted"]),
    }

    return {
        "wallets": wallets,
        "transfers": transfers,
        "totals": totals,
        "gaps": gaps,
        "caveat": _CAVEAT,
    }


__all__ = [
    "ProvenanceRecord",
    "append_provenance_record",
    "read_provenance_records",
    "compute_compliance_export",
]
