"""Unit tests for audit-integrity anchoring (KAI L3).

Covers: deterministic + order-independent digest, missing-file recording,
default-off no-op, NullStamper record, and anchoring via an injected stamper.
"""

from __future__ import annotations

import json

from app.core.integrity_settings import IntegritySettings
from app.integrity import anchor as anchor_mod
from app.integrity import anchor_audit_digest, compute_audit_digest


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_digest_deterministic_and_order_independent(tmp_path) -> None:
    a = _write(tmp_path, "a.jsonl", "alpha")
    b = _write(tmp_path, "b.jsonl", "beta")
    d1 = compute_audit_digest([a, b])
    d2 = compute_audit_digest([b, a])  # reversed order
    assert d1.digest == d2.digest
    assert len(d1.digest) == 64
    assert set(d1.files) == {a, b}


def test_digest_changes_with_content(tmp_path) -> None:
    a = _write(tmp_path, "a.jsonl", "alpha")
    before = compute_audit_digest([a]).digest
    (tmp_path / "a.jsonl").write_text("ALPHA-changed", encoding="utf-8")
    assert compute_audit_digest([a]).digest != before


def test_digest_records_missing(tmp_path) -> None:
    a = _write(tmp_path, "a.jsonl", "alpha")
    d = compute_audit_digest([a, str(tmp_path / "gone.jsonl")])
    assert d.missing == [str(tmp_path / "gone.jsonl")]


def test_anchor_disabled_is_noop() -> None:
    r = anchor_audit_digest(IntegritySettings(enabled=False))
    assert r.state == "disabled" and r.digest == ""


def test_anchor_null_records(tmp_path) -> None:
    a = _write(tmp_path, "a.jsonl", "alpha")
    cfg = IntegritySettings(
        enabled=True, stamper="null", audit_paths=[a], proofs_dir=str(tmp_path / "out")
    )
    r = anchor_audit_digest(cfg)
    assert r.state == "recorded" and len(r.digest) == 64 and r.proof_path == ""
    rec = json.loads((tmp_path / "out" / f"audit-{r.digest[:16]}.json").read_text())
    assert rec["digest"] == r.digest and rec["stamper"] == "null"


def test_anchor_with_injected_stamper(tmp_path, monkeypatch) -> None:
    a = _write(tmp_path, "a.jsonl", "alpha")

    class FakeStamper:
        name = "fake"

        def stamp(self, digest_hex, out_dir):
            p = out_dir / "proof.ots"
            p.write_bytes(b"proof")
            return str(p)

    monkeypatch.setattr(anchor_mod, "_make_stamper", lambda name: FakeStamper())
    cfg = IntegritySettings(
        enabled=True, stamper="opentimestamps", audit_paths=[a], proofs_dir=str(tmp_path / "o")
    )
    r = anchor_audit_digest(cfg)
    assert r.state == "anchored" and r.proof_path.endswith("proof.ots")


def test_anchor_captures_stamper_error(tmp_path, monkeypatch) -> None:
    a = _write(tmp_path, "a.jsonl", "alpha")

    class BoomStamper:
        name = "boom"

        def stamp(self, digest_hex, out_dir):
            raise anchor_mod.AnchorUnavailableError("no ots lib")

    monkeypatch.setattr(anchor_mod, "_make_stamper", lambda name: BoomStamper())
    cfg = IntegritySettings(
        enabled=True, stamper="opentimestamps", audit_paths=[a], proofs_dir=str(tmp_path / "o")
    )
    r = anchor_audit_digest(cfg)
    assert r.state == "error" and "no ots lib" in r.reason


# --- read-only status surface (get_integrity_status) ---------------------------

from app.integrity import get_integrity_status  # noqa: E402


def test_status_disabled_no_fs_touch() -> None:
    s = get_integrity_status(IntegritySettings(enabled=False, proofs_dir="/does/not/exist"))
    assert s.state == "disabled" and s.enabled is False


def test_status_no_anchor_when_empty(tmp_path) -> None:
    s = get_integrity_status(IntegritySettings(enabled=True, proofs_dir=str(tmp_path / "empty")))
    assert s.state == "no_anchor" and s.enabled is True and s.anchor_count == 0


def test_status_ok_reflects_latest_anchor_record(tmp_path) -> None:
    # Write a real record via the anchor action (null stamper → no .ots proof),
    # then the read-only status must reflect it without re-computing anything.
    a = _write(tmp_path, "a.jsonl", "alpha")
    out = str(tmp_path / "out")
    cfg = IntegritySettings(enabled=True, stamper="null", audit_paths=[a], proofs_dir=out)
    r = anchor_audit_digest(cfg)
    s = get_integrity_status(cfg)
    assert s.state == "ok"
    assert s.anchor_count == 1
    assert s.last_digest == r.digest
    assert s.last_anchored_at != ""
    assert s.proof_available is False  # null stamper writes no .ots


def test_status_proof_available_and_latest_by_ts(tmp_path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    older = "a" * 64
    newer = "b" * 64
    (out / f"audit-{older[:16]}.json").write_text(
        json.dumps({"ts": "2026-06-01T00:00:00+00:00", "digest": older}), encoding="utf-8"
    )
    (out / f"audit-{newer[:16]}.json").write_text(
        json.dumps({"ts": "2026-06-17T00:00:00+00:00", "digest": newer}), encoding="utf-8"
    )
    (out / f"audit-{newer[:16]}.ots").write_bytes(b"\x00ots-proof")  # OTS proof for newer
    s = get_integrity_status(IntegritySettings(enabled=True, proofs_dir=str(out)))
    assert s.state == "ok"
    assert s.anchor_count == 2
    assert s.last_digest == newer  # picked by latest ts
    assert s.proof_available is True


def test_status_skips_corrupt_record(tmp_path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    good = "c" * 64
    (out / f"audit-{good[:16]}.json").write_text(
        json.dumps({"ts": "2026-06-10T00:00:00+00:00", "digest": good}), encoding="utf-8"
    )
    (out / "audit-deadbeefdeadbeef.json").write_text("{not json", encoding="utf-8")
    s = get_integrity_status(IntegritySettings(enabled=True, proofs_dir=str(out)))
    assert s.state == "ok"
    assert s.anchor_count == 1  # corrupt record skipped, not crashed
    assert s.last_digest == good
