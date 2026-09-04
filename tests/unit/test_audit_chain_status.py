"""Tests für die Audit-Chain-Integritäts-KPI (#314, Truth-Layer).

Quelle ist seit 2026-09-04 der **Attestation-Ledger**
(``artifacts/truth/attestation_ledger.jsonl``) — die einzige Hash-Kette, die in
Produktion tatsächlich befüllt wird. Die vorherige Quelle
(``decision_journal_chain.jsonl``) hatte keinen Schreiber; das KPI konnte
strukturell nur ``empty`` liefern und war damit ein Integritätsindikator, der
nie fehlschlagen kann.

Verhalten, nicht Implementierung: grün heißt nachgerechnet tamper-frei, rot
heißt echte Manipulation, unlesbar heißt ``unavailable`` — nie ein 500er.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.audit_chain_status import (
    AuditChainStatus,
    derive_audit_chain_status,
    load_audit_chain_status,
)
from app.truth.ledger import append_attestation

NL = "\n"


def _build_ledger(path: Path, n: int = 3) -> None:
    """``n`` echte, verkettete Attestierungen (ohne Audit-Spiegelung)."""
    for i in range(n):
        append_attestation(
            "test_claim",
            f"subject-{i}",
            {"i": i, "claim": "hold"},
            path=path,
            mirror_audit=False,
        )


# ── load_audit_chain_status (IO-Wrapper, end-to-end über echte Dateien) ───────


def test_empty_when_no_ledger_file(tmp_path: Path) -> None:
    status = load_audit_chain_status(ledger_path=tmp_path / "attestation_ledger.jsonl")
    assert status.state == "empty"
    assert status.available is True
    assert status.entries == 0
    assert status.errors == 0


def test_ok_for_valid_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "attestation_ledger.jsonl"
    _build_ledger(ledger, n=3)

    status = load_audit_chain_status(ledger_path=ledger)
    assert status.state == "ok"
    assert status.available is True
    assert status.entries == 3
    assert status.errors == 0
    assert status.first_error is None


def test_broken_when_payload_tampered(tmp_path: Path) -> None:
    ledger = tmp_path / "attestation_ledger.jsonl"
    _build_ledger(ledger, n=3)

    lines = ledger.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["payload"]["claim"] = "sell"
    lines[1] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    ledger.write_text(NL.join(lines) + NL, encoding="utf-8")

    status = load_audit_chain_status(ledger_path=ledger)
    assert status.state == "broken"
    assert status.errors >= 1
    assert status.first_error is not None
    assert "payload" in status.first_error


def test_broken_when_record_removed(tmp_path: Path) -> None:
    """Eine entfernte Zeile bricht die Verkettung — genau das soll rot werden."""
    ledger = tmp_path / "attestation_ledger.jsonl"
    _build_ledger(ledger, n=3)

    lines = ledger.read_text(encoding="utf-8").splitlines()
    del lines[1]
    ledger.write_text(NL.join(lines) + NL, encoding="utf-8")

    status = load_audit_chain_status(ledger_path=ledger)
    assert status.state == "broken"
    assert status.errors >= 1


def test_unavailable_on_read_error(tmp_path: Path) -> None:
    # Verzeichnis statt Datei → Read scheitert → fail-soft ``unavailable``.
    bad = tmp_path / "is_a_dir"
    bad.mkdir()
    status = load_audit_chain_status(ledger_path=bad)
    assert status.state == "unavailable"
    assert status.available is False
    assert status.reason


# ── derive_audit_chain_status (reine Klassifikation) ──────────────────────────


def test_derive_empty() -> None:
    s = derive_audit_chain_status(entries=0, errors=[])
    assert s.state == "empty"
    assert s.entries == 0
    assert isinstance(s, AuditChainStatus)


def test_derive_ok() -> None:
    s = derive_audit_chain_status(entries=118, errors=[])
    assert s.state == "ok"
    assert s.entries == 118
    assert s.errors == 0
    assert s.first_error is None


def test_derive_broken_keeps_first_error() -> None:
    s = derive_audit_chain_status(
        entries=5,
        errors=["seq 2: chain broken (prev_hash mismatch)", "seq 3: record_hash mismatch"],
    )
    assert s.state == "broken"
    assert s.errors == 2
    assert s.first_error == "seq 2: chain broken (prev_hash mismatch)"
    assert s.reason
