"""Tests for the falsification-verdict record + tamper-evident anchoring (Component 2).

Behaviour (kai-testing-regeln):
  * A verdict record binds the EXACT trial inputs + net_bps it was computed over,
    so the on-chain proof attests a specific, reproducible verdict.
  * The record is ALWAYS written (append-only), even when anchoring is off.
  * ``anchor_record_digest`` respects the default-off integrity settings, never
    raises, and threads the ``prefix`` into the proof filename so verdict proofs
    (``verdict-*.ots``) coexist with the daily audit proofs in one proofs_dir.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.integrity_settings import IntegritySettings
from app.integrity import anchor as anchor_mod
from app.observability.edge_validation_gate import evaluate_edge_validation, resolve_trial_count
from app.observability.falsification_verdict import (
    build_verdict_record,
    record_and_anchor_verdict,
    verdict_record_digest,
)

_TS = "2026-06-29T00:00:00+00:00"


def _verdict_and_resolved():
    net = [12.0, 8.0, 15.0, -3.0, 20.0] * 25  # n=125
    v = evaluate_edge_validation(net, trials=50, min_n=100)
    r = resolve_trial_count(ledger_count=50, override=None)
    return v, r, net


def _record():
    v, r, net = _verdict_and_resolved()
    return (
        build_verdict_record(
            v,
            resolved=r,
            exec_audit_path="x.jsonl",
            venue="paper",
            net_bps=net,
            ledger_path=Path("l.jsonl"),
            recorded_at_utc=_TS,
        ),
        v,
        r,
    )


def test_build_verdict_record_binds_trials_inputs_and_criteria() -> None:
    rec, v, r = _record()
    assert rec["schema"].startswith("falsification_verdict/")
    assert rec["trials_used"] == r.trials
    assert rec["trials_source"] == "ledger"
    assert rec["ledger_count"] == 50
    assert rec["n"] == v.trade_count
    assert len(rec["net_bps_sha256"]) == 64
    assert rec["ready"] == v.ready
    assert {c["name"] for c in rec["criteria"]} == {c.name for c in v.criteria}


def test_verdict_digest_is_deterministic_64hex() -> None:
    rec, _v, _r = _record()
    d1 = verdict_record_digest(rec)
    d2 = verdict_record_digest(dict(rec))
    assert d1 == d2
    assert len(d1) == 64


def test_record_written_even_when_anchoring_disabled(tmp_path) -> None:
    rec, v, _r = _record()
    out = tmp_path / "verdicts.jsonl"
    digest, result = record_and_anchor_verdict(
        rec, verdicts_path=out, settings=IntegritySettings(enabled=False)
    )
    assert result.state == "disabled"
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["trials_used"] == v.trials
    # The written line re-canonicalises to the SAME digest that was anchored —
    # so a third party can verify the proof against the ledger line.
    assert verdict_record_digest(parsed) == digest


def test_anchor_record_digest_null_stamper_records_no_proof(tmp_path) -> None:
    digest = "ab" * 32
    cfg = IntegritySettings(enabled=True, stamper="null", proofs_dir=str(tmp_path / "p"))
    res = anchor_mod.anchor_record_digest(digest, settings=cfg, prefix="verdict")
    assert res.state == "recorded"
    assert res.proof_path == ""
    rec = json.loads((tmp_path / "p" / f"verdict-{digest[:16]}.json").read_text(encoding="utf-8"))
    assert rec["digest"] == digest
    assert rec["prefix"] == "verdict"


def test_anchor_record_digest_disabled_writes_nothing(tmp_path) -> None:
    cfg = IntegritySettings(enabled=False, proofs_dir=str(tmp_path / "p"))
    res = anchor_mod.anchor_record_digest("cd" * 32, settings=cfg)
    assert res.state == "disabled"
    assert not (tmp_path / "p").exists()


def test_anchor_record_digest_prefix_flows_to_proof_filename(tmp_path, monkeypatch) -> None:
    digest = "ef" * 32

    class FakeStamper:
        name = "fake"

        def stamp(self, digest_hex, out_dir, *, prefix="audit"):
            p = out_dir / f"{prefix}-{digest_hex[:16]}.ots"
            p.write_bytes(b"proof")
            return str(p)

    monkeypatch.setattr(anchor_mod, "_make_stamper", lambda name: FakeStamper())
    cfg = IntegritySettings(enabled=True, stamper="opentimestamps", proofs_dir=str(tmp_path / "p"))
    res = anchor_mod.anchor_record_digest(digest, settings=cfg, prefix="verdict")
    assert res.state == "anchored"
    assert res.proof_path.endswith(f"verdict-{digest[:16]}.ots")
