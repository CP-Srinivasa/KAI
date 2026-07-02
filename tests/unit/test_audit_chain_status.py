"""Tests for the audit-chain integrity Truth-Layer KPI (#314).

Behaviour, not implementation: a green KPI must mean the decision audit-chain is
genuinely tamper-free, a red KPI must mean real tampering, and a legitimate
journal rotation must NOT raise a false tamper alarm.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.audit.decision_chain import append_chain_entry
from app.observability.audit_chain_status import (
    AuditChainStatus,
    derive_audit_chain_status,
    load_audit_chain_status,
)


def _journal(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _build_valid_chain(chain_path: Path, journal_path: Path, n: int = 3) -> list[dict]:
    """Append ``n`` chained decisions and mirror their payloads into the journal."""
    records = []
    for i in range(n):
        payload = {"decision_id": f"dec-{i}", "action": "hold", "score": i}
        append_chain_entry(
            chain_path=chain_path, decision_id=payload["decision_id"], record_payload=payload
        )
        records.append(payload)
    _journal(journal_path, records)
    return records


# ── load_audit_chain_status (IO wrapper, end-to-end over real files) ──────────


def test_empty_when_no_chain_file(tmp_path: Path) -> None:
    status = load_audit_chain_status(
        chain_path=tmp_path / "chain.jsonl", journal_path=tmp_path / "journal.jsonl"
    )
    assert status.state == "empty"
    assert status.available is True
    assert status.entries == 0
    assert status.errors == 0


def test_ok_for_valid_chain_with_journal_crosscheck(tmp_path: Path) -> None:
    chain = tmp_path / "chain.jsonl"
    journal = tmp_path / "journal.jsonl"
    _build_valid_chain(chain, journal, n=3)

    status = load_audit_chain_status(chain_path=chain, journal_path=journal)
    assert status.state == "ok"
    assert status.available is True
    assert status.entries == 3
    assert status.errors == 0
    assert status.first_error is None
    assert status.cross_checked is True
    assert status.journal_gaps == 0


def test_broken_when_journal_record_tampered(tmp_path: Path) -> None:
    chain = tmp_path / "chain.jsonl"
    journal = tmp_path / "journal.jsonl"
    records = _build_valid_chain(chain, journal, n=3)

    # Tamper a journal record AFTER it was chained → record_hash no longer matches.
    records[1]["score"] = 999
    _journal(journal, records)

    status = load_audit_chain_status(chain_path=chain, journal_path=journal)
    assert status.state == "broken"
    assert status.errors >= 1
    assert status.first_error is not None
    assert "record_hash_mismatch" in status.first_error


def test_broken_when_chain_line_tampered(tmp_path: Path) -> None:
    chain = tmp_path / "chain.jsonl"
    journal = tmp_path / "journal.jsonl"
    _build_valid_chain(chain, journal, n=3)

    # Flip a chained record_hash directly in the chain file → chain_hash mismatch.
    lines = chain.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["record_hash"] = "f" * 64
    lines[1] = json.dumps(row)
    chain.write_text("\n".join(lines) + "\n", encoding="utf-8")

    status = load_audit_chain_status(chain_path=chain, journal_path=journal)
    assert status.state == "broken"
    assert status.errors >= 1


def test_journal_rotation_is_gap_not_tamper(tmp_path: Path) -> None:
    """A truncated journal (rotation) leaves chain entries without payloads — that
    is a ``journal_gaps`` count, NOT a tamper ``broken`` state."""
    chain = tmp_path / "chain.jsonl"
    journal = tmp_path / "journal.jsonl"
    records = _build_valid_chain(chain, journal, n=3)

    # Simulate rotation: journal keeps only the newest record.
    _journal(journal, records[-1:])

    status = load_audit_chain_status(chain_path=chain, journal_path=journal)
    assert status.state == "ok"
    assert status.journal_gaps == 2
    assert status.errors == 0


def test_unavailable_on_read_error(tmp_path: Path) -> None:
    # A directory where a file is expected → read raises → fail-soft unavailable.
    bad = tmp_path / "is_a_dir"
    bad.mkdir()
    status = load_audit_chain_status(chain_path=bad, journal_path=tmp_path / "journal.jsonl")
    assert status.state == "unavailable"
    assert status.available is False
    assert "Chain-Read-Fehler" in status.reason


# ── derive_audit_chain_status (pure classification) ──────────────────────────


def test_derive_empty() -> None:
    s = derive_audit_chain_status(entries=0, errors=[], cross_checked=False)
    assert s.state == "empty" and s.entries == 0


def test_derive_ok_ignores_missing_journal_record() -> None:
    s = derive_audit_chain_status(
        entries=5,
        errors=["missing_journal_record idx=2 decision_id=dec-2"],
        cross_checked=True,
    )
    assert s.state == "ok"
    assert s.errors == 0
    assert s.journal_gaps == 1


def test_derive_broken_on_tamper_prefix() -> None:
    s = derive_audit_chain_status(
        entries=5,
        errors=["chain_break idx=1 decision_id=dec-1 expected_prev=… got=…"],
        cross_checked=False,
    )
    assert s.state == "broken"
    assert s.errors == 1
    assert isinstance(s, AuditChainStatus)
