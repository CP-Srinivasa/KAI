"""Unit tests for the L3 anchor runner (scripts/integrity_anchor_audit.py).

Covers the exit-code mapping and that the runner actually invokes anchoring
(a record file appears) when enabled. The digest/stamper internals are covered
by test_integrity.py — here we only exercise the thin runner wrapper.
"""

from __future__ import annotations

from scripts.integrity_anchor_audit import main

from app.core.integrity_settings import IntegritySettings


def test_runner_disabled_is_noop(tmp_path) -> None:
    rc = main(IntegritySettings(enabled=False, proofs_dir=str(tmp_path / "out")))
    assert rc == 0
    assert not (tmp_path / "out").exists()  # disabled → no filesystem touch


def test_runner_records_when_enabled(tmp_path) -> None:
    audit = tmp_path / "a.jsonl"
    audit.write_text("alpha", encoding="utf-8")
    out = tmp_path / "proofs"

    rc = main(
        IntegritySettings(
            enabled=True,
            stamper="null",
            audit_paths=[str(audit)],
            proofs_dir=str(out),
        )
    )
    assert rc == 0
    records = list(out.glob("audit-*.json"))
    assert len(records) == 1  # runner invoked the anchor action → record written
