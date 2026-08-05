"""Unit tests for the self-healing truth-anchor runner (scripts/truth_anchor_run.py).

The runner attests new preregs/verdicts (covered by test_truth_ledger.py) and then
ensures the ledger TIP is on-chain anchored. These tests pin the SELF-HEALING gate:
anchoring keys on "is the current tip proof present", NOT on "were new records chained
this run" — so a pre-existing backlog gets anchored and a failed OTS attempt is retried.
"""

from __future__ import annotations

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
        patch(
            "app.lightning.ops_ledger.attest_ln_ops_tip",
            return_value={"attested": 0, "total": 0},
        ),
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
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5]:
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
