"""Unit tests for the self-healing truth-anchor runner (scripts/truth_anchor_run.py).

The runner attests new preregs/verdicts (covered by test_truth_ledger.py), binds the
Lightning money-journal tip into the same chain, and then ensures the ledger TIP is
on-chain anchored. These tests pin two things:

  * the SELF-HEALING gate: anchoring keys on "is the current tip proof present", NOT on
    "were new records chained this run" — so a pre-existing backlog gets anchored and a
    failed OTS attempt is retried;
  * BL-1: the LN-ops step is BEST EFFORT. The tests below deliberately do NOT patch
    ``attest_ln_ops_tip`` — they run the real function against a real broken ledger,
    because patching exactly the function under suspicion is what made CI blind to the
    deploy blocker in the first place.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from scripts.truth_anchor_run import main

from app.integrity.anchor import AnchorResult

_TIP_HASH = "a" * 64
_TIP16 = _TIP_HASH[:16]


def _patches(*, enabled: bool, proofs_dir: str, anchor_result: AnchorResult, calls: list):
    """Patch the lazily-imported collaborators of ``main`` (patched at their source)."""

    def _anchor(digest_hex, *, settings, prefix):  # noqa: ANN001, ANN202
        calls.append((digest_hex, prefix))
        return anchor_result

    return (
        patch("app.truth.ledger.attest_prereg_ledger", return_value={"attested": 0, "total": 8}),
        patch("app.truth.ledger.attest_verdict_reports", return_value={"attested": 0, "total": 5}),
        patch("app.truth.ledger.chain_tip", return_value={"record_hash": _TIP_HASH, "seq": 20}),
        patch(
            "app.core.integrity_settings.IntegritySettings",
            return_value=SimpleNamespace(
                enabled=enabled, proofs_dir=proofs_dir, stamper="opentimestamps"
            ),
        ),
        patch("app.integrity.anchor.anchor_record_digest", side_effect=_anchor),
    )


def _run(**kw):  # noqa: ANN003
    calls: list = []
    ctxs = _patches(calls=calls, **kw)
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        rc = main()
    return rc, calls


def test_backlog_unanchored_tip_gets_anchored(tmp_path) -> None:
    # No proof file present → the runner must anchor the current tip even though 0 new
    # records were chained this run (the pre-existing backlog is the whole point).
    rc, calls = _run(
        enabled=True,
        proofs_dir=str(tmp_path),
        anchor_result=AnchorResult(state="anchored", proof_path=str(tmp_path / "p.ots")),
    )
    assert rc == 0
    assert calls == [(_TIP_HASH, "truthledger")]  # anchored the tip hash exactly once


def test_already_anchored_tip_is_skipped(tmp_path) -> None:
    # Proof for the current tip already exists → idempotent no-op, anchor NOT called.
    (tmp_path / f"truthledger-{_TIP16}.ots").write_bytes(b"proof")
    rc, calls = _run(
        enabled=True,
        proofs_dir=str(tmp_path),
        anchor_result=AnchorResult(state="anchored"),
    )
    assert rc == 0
    assert calls == []  # tip already anchored → no re-anchor


def test_disabled_is_noop(tmp_path) -> None:
    rc, calls = _run(
        enabled=False,
        proofs_dir=str(tmp_path),
        anchor_result=AnchorResult(state="disabled"),
    )
    assert rc == 0
    assert calls == []  # disabled → never touches the anchor


def test_anchor_error_returns_nonzero(tmp_path) -> None:
    # A failed OTS attempt (no proof written) → rc 1 so the timer surfaces it; the next
    # run retries because the proof still doesn't exist (self-healing).
    rc, calls = _run(
        enabled=True,
        proofs_dir=str(tmp_path),
        anchor_result=AnchorResult(state="error", reason="calendar outage"),
    )
    assert rc == 1
    assert calls == [(_TIP_HASH, "truthledger")]


# --------------------------------------------------------------------------- #
# BL-1 — a broken money journal must never take the whole truth chain offline.
# --------------------------------------------------------------------------- #


def test_legacy_ln_ops_ledger_warns_but_does_not_block_the_anchor(
    tmp_path, monkeypatch, capsys
) -> None:
    # The real deploy situation: the box still has an unmigrated, unchained v1-style
    # journal. verify_ln_ops_ledger() → ok:False → attest_ln_ops_tip() raises. Before
    # the guard this aborted main() BEFORE chain_tip(), so the OTS anchoring of the
    # ENTIRE truth chain silently stopped on the first timer run after deploy.
    ledger = tmp_path / "ledger" / "ln_ops_ledger_v2.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "ts": "2026-07-02T05:46:20+00:00",
                "action": "pay_invoice",
                "state": "error",
                "plan": {"payment_request": "lnbc250u1legacy"},
                "response": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_LN_OPS_LEDGER_V2_PATH", str(ledger))

    rc, calls = _run(
        enabled=True,
        proofs_dir=str(tmp_path),
        anchor_result=AnchorResult(state="anchored", proof_path=str(tmp_path / "p.ots")),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [(_TIP_HASH, "truthledger")]  # the OTS step was still reached
    assert "WARNING ln-ops-tip attestation skipped" in out
    assert "LightningOpsLedgerError" in out
    assert "ln-ops-tip attested=0/0" in out


def test_torn_ln_ops_ledger_warns_but_does_not_block_the_anchor(
    tmp_path, monkeypatch, capsys
) -> None:
    # Same guarantee for a power-cut torn tail (M-5) — unparseable, not merely legacy.
    ledger = tmp_path / "ledger" / "ln_ops_ledger_v2.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"ts": "2026-08-05T00:00:00+00:0', encoding="utf-8")
    monkeypatch.setenv("APP_LN_OPS_LEDGER_V2_PATH", str(ledger))

    rc, calls = _run(
        enabled=True,
        proofs_dir=str(tmp_path),
        anchor_result=AnchorResult(state="anchored", proof_path=str(tmp_path / "p.ots")),
    )
    assert rc == 0
    assert calls == [(_TIP_HASH, "truthledger")]
    assert "WARNING ln-ops-tip attestation skipped" in capsys.readouterr().out


def test_missing_ln_ops_ledger_is_a_quiet_noop(tmp_path, monkeypatch, capsys) -> None:
    # PR-B ships the v2 machinery unwired: on a box that has no v2 journal yet the
    # step must be a silent 0/0, not a daily WARNING that trains the operator to
    # ignore the line.
    monkeypatch.setenv("APP_LN_OPS_LEDGER_V2_PATH", str(tmp_path / "ledger" / "absent.jsonl"))
    rc, calls = _run(
        enabled=True,
        proofs_dir=str(tmp_path),
        anchor_result=AnchorResult(state="anchored", proof_path=str(tmp_path / "p.ots")),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [(_TIP_HASH, "truthledger")]
    assert "WARNING" not in out
    assert "ln-ops-tip attested=0/0" in out


def test_valid_ln_ops_tip_is_attested_into_the_run(tmp_path, monkeypatch, capsys) -> None:
    # The happy path PR-C will rely on: a verified v2 journal contributes its tip.
    from app.lightning.ops_ledger import append_ln_outcome, prepare_ln_intent

    ledger = tmp_path / "ledger" / "ln_ops_ledger_v2.jsonl"
    monkeypatch.setenv("APP_LN_OPS_LEDGER_V2_PATH", str(ledger))
    prepare_ln_intent("create_invoice", plan={"value_sat": 10}, intent_id="i1")
    append_ln_outcome("create_invoice", "executed", plan={"value_sat": 10}, intent_id="i1")

    truth = tmp_path / "truth.jsonl"
    with (
        patch("app.truth.ledger.DEFAULT_TRUTH_LEDGER_PATH", truth),
        patch("app.truth.ledger.get_default_kai_audit_service"),  # no real audit stream
    ):
        rc, calls = _run(
            enabled=True,
            proofs_dir=str(tmp_path),
            anchor_result=AnchorResult(state="anchored", proof_path=str(tmp_path / "p.ots")),
        )
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [(_TIP_HASH, "truthledger")]
    assert "WARNING" not in out
    assert "ln-ops-tip attested=1/1" in out
    assert truth.exists()
