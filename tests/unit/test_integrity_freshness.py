"""Unit tests for the L3 freshness/replay probe (app.integrity.freshness).

Covers the operator's seven cases plus the crucial append-only invariant: a
GROWING audit log is NOT a mismatch (only a changed/truncated prefix is).
stamper=null / proof_available=false must never count as an error.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


# ---------------------------------------------------------------------------
# G6: die Sonde sah 75 von 150 Ankern (A7-051/072)
# ---------------------------------------------------------------------------


def _write_anchor(out_dir: Path, name: str, ts: datetime) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(
        json.dumps({"ts": ts.isoformat(), "digest": "a" * 64, "files": {}, "sizes": {}}),
        encoding="utf-8",
    )


def test_family_of_strips_the_hash_not_the_name() -> None:
    from app.integrity.freshness import _family_of

    assert _family_of("truthledger-b8b256f733fa942a.json") == "truthledger"
    assert _family_of("audit-fc77794d843fe984.json") == "audit"
    assert _family_of("analystprobe-rule-5560581be47bd71e.json") == "analystprobe-rule"


def test_all_families_are_counted_not_only_audit(tmp_path: Path) -> None:
    from app.integrity.freshness import anchor_families

    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    _write_anchor(tmp_path, "audit-" + "1" * 16 + ".json", now - timedelta(hours=8))
    _write_anchor(tmp_path, "truthledger-" + "2" * 16 + ".json", now - timedelta(hours=8))
    _write_anchor(tmp_path, "verdict-" + "3" * 16 + ".json", now - timedelta(hours=1490))

    families = anchor_families(tmp_path, now=now)
    assert set(families) == {"audit", "truthledger", "verdict"}
    assert families["truthledger"]["count"] == 1
    assert families["verdict"]["age_hours"] == 1490.0


def test_dead_truthledger_family_is_a_finding(tmp_path: Path) -> None:
    """Der Kern des Befunds: ein toter truthledger-Anker sah aus wie ein lebender."""
    from app.integrity.freshness import anchor_families, stale_anchor_families

    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    _write_anchor(tmp_path, "audit-" + "1" * 16 + ".json", now - timedelta(hours=8))
    _write_anchor(tmp_path, "truthledger-" + "2" * 16 + ".json", now - timedelta(days=10))
    families = anchor_families(tmp_path, now=now)
    assert stale_anchor_families(families) == ("truthledger",)


def test_event_driven_families_are_never_stale(tmp_path: Path) -> None:
    """Negativkontrolle: verdict ist 1.490 h alt und voellig in Ordnung.

    Eine Altersschwelle waere hier ein Dauer-Fehlalarm — dieselbe Falle wie
    beim H2-Zombie (Praereg ohne erreichbare Population).
    """
    from app.integrity.freshness import anchor_families, stale_anchor_families

    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    for family in ("newsverdict", "verdict", "analystprobe-rule"):
        _write_anchor(tmp_path, f"{family}-" + "4" * 16 + ".json", now - timedelta(days=62))
    assert stale_anchor_families(anchor_families(tmp_path, now=now)) == ()


def test_measured_max_gap_does_not_trip_the_threshold(tmp_path: Path) -> None:
    """Groesster je gemessener truthledger-Abstand: 24,01 h. Die Schwelle ist 48 h."""
    from app.integrity.freshness import anchor_families, stale_anchor_families

    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    _write_anchor(tmp_path, "truthledger-" + "5" * 16 + ".json", now - timedelta(hours=24.01))
    assert stale_anchor_families(anchor_families(tmp_path, now=now)) == ()


def test_unreadable_anchor_is_skipped_not_guessed(tmp_path: Path) -> None:
    from app.integrity.freshness import anchor_families

    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    _write_anchor(tmp_path, "audit-" + "1" * 16 + ".json", now)
    (tmp_path / ("audit-" + "9" * 16 + ".json")).write_text("{kaputt", encoding="utf-8")
    families = anchor_families(tmp_path, now=now)
    assert families["audit"]["count"] == 1
