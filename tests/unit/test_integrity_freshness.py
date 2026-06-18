"""Unit tests for the L3 freshness/replay probe (app.integrity.freshness).

Covers the operator's seven cases plus the crucial append-only invariant: a
GROWING audit log is NOT a mismatch (only a changed/truncated prefix is).
stamper=null / proof_available=false must never count as an error.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.integrity_settings import IntegritySettings
from app.integrity import anchor_audit_digest, check_l3_integrity_freshness


def _cfg(tmp_path, audit_file):
    return IntegritySettings(
        enabled=True,
        stamper="null",
        audit_paths=[str(audit_file)],
        proofs_dir=str(tmp_path / "proofs"),
    )


def test_disabled_is_ok_noop(tmp_path) -> None:
    p = check_l3_integrity_freshness(IntegritySettings(enabled=False, proofs_dir=str(tmp_path)))
    assert p.status == "ok" and p.reason_code == "L3_DISABLED" and p.enabled is False


def test_enabled_but_no_anchor_is_warning_not_critical(tmp_path) -> None:
    p = check_l3_integrity_freshness(
        IntegritySettings(enabled=True, proofs_dir=str(tmp_path / "empty"))
    )
    assert p.status == "warning" and p.reason_code == "L3_ANCHOR_MISSING"


def test_ok_and_growth_is_not_a_mismatch(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("0123456789", encoding="utf-8")
    cfg = _cfg(tmp_path, audit)
    anchor_audit_digest(cfg)

    p = check_l3_integrity_freshness(cfg)
    assert p.status == "ok" and p.reason_code == "L3_ANCHOR_OK"
    assert p.proof_available is False  # stamper=null → never an error
    assert p.anchor_count == 1 and p.last_anchor_age_hours is not None

    # APPEND (append-only growth) — prefix unchanged → still ok, NOT a mismatch.
    with audit.open("a", encoding="utf-8") as fh:
        fh.write("abcdefghij")
    p2 = check_l3_integrity_freshness(cfg)
    assert p2.status == "ok" and p2.reason_code == "L3_ANCHOR_OK"


def test_stale_warning_after_26h(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("x", encoding="utf-8")
    cfg = _cfg(tmp_path, audit)
    anchor_audit_digest(cfg)
    p = check_l3_integrity_freshness(cfg, now=datetime.now(UTC) + timedelta(hours=27))
    assert p.status == "warning" and p.reason_code == "L3_ANCHOR_STALE"


def test_stale_critical_after_48h(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("x", encoding="utf-8")
    cfg = _cfg(tmp_path, audit)
    anchor_audit_digest(cfg)
    p = check_l3_integrity_freshness(cfg, now=datetime.now(UTC) + timedelta(hours=49))
    assert p.status == "critical" and p.reason_code == "L3_ANCHOR_CRITICAL_STALE"


def test_replay_mismatch_when_prefix_changed(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("0123456789", encoding="utf-8")
    cfg = _cfg(tmp_path, audit)
    anchor_audit_digest(cfg)
    # Change a byte WITHIN the anchored prefix (same length) → tamper, not growth.
    audit.write_text("X123456789", encoding="utf-8")
    p = check_l3_integrity_freshness(cfg)
    assert p.status == "critical" and p.reason_code == "L3_DIGEST_REPLAY_MISMATCH"


def test_replay_mismatch_when_truncated(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("0123456789", encoding="utf-8")
    cfg = _cfg(tmp_path, audit)
    anchor_audit_digest(cfg)
    audit.write_text("012", encoding="utf-8")  # file shrank below recorded size
    p = check_l3_integrity_freshness(cfg)
    assert p.status == "critical" and p.reason_code == "L3_DIGEST_REPLAY_MISMATCH"


def test_replay_failed_when_file_missing(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("data", encoding="utf-8")
    cfg = _cfg(tmp_path, audit)
    anchor_audit_digest(cfg)
    audit.unlink()  # source file gone after anchoring
    p = check_l3_integrity_freshness(cfg)
    assert p.status == "critical" and p.reason_code == "L3_DIGEST_REPLAY_FAILED"
