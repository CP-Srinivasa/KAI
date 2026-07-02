"""Truth attestation ledger (ADR 0013 Tier 1 / ADR 0012 realised).

Hash-chained, append-only ledger of attestations over REAL truth artifacts
(pre-registrations, canonical-edge reports). Verifies the plan invariants:
forward-only chain, tamper-evidence, reproducibility (payload hash recomputable
by any third party), idempotent prereg backfill, audit-service mirroring.
"""

from __future__ import annotations

import json

import pytest

from app.audit.kai_audit_service import KaiAuditService
from app.research.prereg_ledger import PreRegistrationLedger, register
from app.truth.ledger import (
    GENESIS_HASH,
    TruthLedgerError,
    append_attestation,
    attest_prereg_ledger,
    attest_verdict_reports,
    chain_tip,
    verify_ledger,
)


def test_first_record_links_to_genesis(tmp_path) -> None:
    path = tmp_path / "truth.jsonl"
    rec = append_attestation("prereg", "abc123", {"claim": "x"}, path=path, mirror_audit=False)
    assert rec["seq"] == 1
    assert rec["prev_hash"] == GENESIS_HASH
    assert rec["algo"] == "sha256"
    assert len(rec["record_hash"]) == 64


def test_chain_links_forward_only(tmp_path) -> None:
    path = tmp_path / "truth.jsonl"
    r1 = append_attestation("prereg", "a", {"n": 1}, path=path, mirror_audit=False)
    r2 = append_attestation("prereg", "b", {"n": 2}, path=path, mirror_audit=False)
    assert r2["seq"] == 2
    assert r2["prev_hash"] == r1["record_hash"]


def test_verify_ok_and_reproducible(tmp_path) -> None:
    path = tmp_path / "truth.jsonl"
    append_attestation("prereg", "a", {"n": 1}, path=path, mirror_audit=False)
    append_attestation("edge", "b", {"n": 2}, path=path, mirror_audit=False)
    report = verify_ledger(path)
    assert report["ok"] is True
    assert report["records"] == 2
    assert report["errors"] == []


def test_verify_flags_tampered_payload(tmp_path) -> None:
    path = tmp_path / "truth.jsonl"
    append_attestation("prereg", "a", {"n": 1}, path=path, mirror_audit=False)
    lines = path.read_text(encoding="utf-8").splitlines()
    doc = json.loads(lines[0])
    doc["payload"]["n"] = 999  # tamper AFTER attestation
    path.write_text(json.dumps(doc, sort_keys=True) + "\n", encoding="utf-8")
    report = verify_ledger(path)
    assert report["ok"] is False
    assert any("payload" in e["reason"] for e in report["errors"])


def test_verify_flags_broken_chain(tmp_path) -> None:
    path = tmp_path / "truth.jsonl"
    append_attestation("prereg", "a", {"n": 1}, path=path, mirror_audit=False)
    append_attestation("prereg", "b", {"n": 2}, path=path, mirror_audit=False)
    lines = path.read_text(encoding="utf-8").splitlines()
    # drop the first record — the second one's prev_hash now dangles
    path.write_text(lines[1] + "\n", encoding="utf-8")
    report = verify_ledger(path)
    assert report["ok"] is False


def test_append_refuses_to_extend_corrupt_tail(tmp_path) -> None:
    path = tmp_path / "truth.jsonl"
    append_attestation("prereg", "a", {"n": 1}, path=path, mirror_audit=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("NICHT-JSON\n")
    with pytest.raises(TruthLedgerError):
        append_attestation("prereg", "b", {"n": 2}, path=path, mirror_audit=False)


def test_audit_mirror_appends_whitelisted_event(tmp_path) -> None:
    path = tmp_path / "truth.jsonl"
    audit = KaiAuditService(audit_path=tmp_path / "audit.jsonl")
    rec = append_attestation("prereg", "a", {"n": 1}, path=path, audit=audit)
    events = audit.tail()
    assert len(events) == 1
    assert events[0]["type"] == "KAI_TRUTH_ATTESTATION"
    assert events[0]["payload"]["record_hash"] == rec["record_hash"]


def test_prereg_backfill_attests_all_then_idempotent(tmp_path) -> None:
    prereg_path = tmp_path / "prereg.jsonl"
    truth_path = tmp_path / "truth.jsonl"
    ledger = PreRegistrationLedger(prereg_path)
    for i in range(2):
        ledger.record(
            register(
                name=f"hyp_{i}",
                direction="long",
                horizon="24h",
                success_criteria="P>=0.95 net positive",
                sample_size_target=100,
                created_at_utc="2026-07-01T00:00:00+00:00",
            )
        )
    first = attest_prereg_ledger(prereg_path=prereg_path, truth_path=truth_path, mirror_audit=False)
    assert first["attested"] == 2
    assert first["skipped"] == 0
    second = attest_prereg_ledger(
        prereg_path=prereg_path, truth_path=truth_path, mirror_audit=False
    )
    assert second["attested"] == 0
    assert second["skipped"] == 2
    assert verify_ledger(truth_path)["ok"] is True


# --- verdict-report attestation + chain tip (automation building blocks) ----------


def _write_verdict_report(out_dir, *, hypothesis: str, verdict: str, prereg_id=None):
    """Persist a real attested verdict report the way the CLI does."""
    from datetime import UTC, datetime

    from app.research.verdict_report import build_verdict_report, write_verdict_report

    rep = build_verdict_report(
        {"n": 42, "p": 0.1},
        hypothesis=hypothesis,
        prereg_id=prereg_id,
        verdict=verdict,
        params={"gate": "P>=0.95"},
        code_version="deadbee",
        generated_at=datetime.now(UTC),
    )
    return write_verdict_report(rep, out_dir), rep["attestation"]["hash"]


def test_chain_tip_empty_is_genesis(tmp_path) -> None:
    tip = chain_tip(tmp_path / "none.jsonl")
    assert tip["record_hash"] == GENESIS_HASH and tip["seq"] == 0


def test_chain_tip_tracks_head(tmp_path) -> None:
    path = tmp_path / "truth.jsonl"
    append_attestation("prereg", "a", {"n": 1}, path=path, mirror_audit=False)
    r2 = append_attestation("prereg", "b", {"n": 2}, path=path, mirror_audit=False)
    tip = chain_tip(path)
    assert tip["seq"] == 2 and tip["record_hash"] == r2["record_hash"]


def test_attest_verdict_reports_missing_dir_is_noop(tmp_path) -> None:
    out = attest_verdict_reports(
        verdicts_dir=tmp_path / "nope", truth_path=tmp_path / "t.jsonl", mirror_audit=False
    )
    assert out == {"total": 0, "attested": 0, "skipped": 0, "ledger": str(tmp_path / "t.jsonl")}


def test_attest_verdict_reports_is_idempotent(tmp_path) -> None:
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    truth = tmp_path / "truth.jsonl"
    _write_verdict_report(verdicts, hypothesis="momentum_24h", verdict="FAILED")
    _write_verdict_report(verdicts, hypothesis="funding_1h", verdict="FAILED", prereg_id="pid1")

    first = attest_verdict_reports(verdicts_dir=verdicts, truth_path=truth, mirror_audit=False)
    assert first["attested"] == 2 and first["skipped"] == 0
    second = attest_verdict_reports(verdicts_dir=verdicts, truth_path=truth, mirror_audit=False)
    assert second["attested"] == 0 and second["skipped"] == 2
    # chain stays valid and the verdicts sit under kind="verdict"
    assert verify_ledger(truth)["ok"] is True


def test_attest_verdict_reports_skips_corrupt_files(tmp_path) -> None:
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    truth = tmp_path / "truth.jsonl"
    (verdicts / "garbage.json").write_text("{not json", encoding="utf-8")
    (verdicts / "foreign.json").write_text('{"unexpected": true}', encoding="utf-8")
    _write_verdict_report(verdicts, hypothesis="ok_one", verdict="FAILED")

    out = attest_verdict_reports(verdicts_dir=verdicts, truth_path=truth, mirror_audit=False)
    assert out["attested"] == 1 and out["total"] == 1


def test_attest_verdict_then_prereg_share_one_chain(tmp_path) -> None:
    """Verdicts and preregs chain into ONE ledger; the tip commits both."""
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    truth = tmp_path / "truth.jsonl"
    _write_verdict_report(verdicts, hypothesis="h1", verdict="FAILED")
    attest_verdict_reports(verdicts_dir=verdicts, truth_path=truth, mirror_audit=False)
    append_attestation("prereg", "pid1", {"name": "h2"}, path=truth, mirror_audit=False)
    tip = chain_tip(truth)
    assert tip["seq"] == 2 and verify_ledger(truth)["ok"] is True
