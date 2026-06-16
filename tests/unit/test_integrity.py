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
