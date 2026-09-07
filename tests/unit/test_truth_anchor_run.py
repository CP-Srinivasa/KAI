"""Unit tests for the self-healing truth-anchor runner (scripts/truth_anchor_run.py).

The runner attests new preregs/verdicts (covered by test_truth_ledger.py), binds the
money-journal tip into the same chain, and then ensures the ledger TIP is on-chain
anchored. These tests pin two things:

  * the SELF-HEALING gate: anchoring keys on "is the current tip proof present", NOT on
    "were new records chained this run" — so a pre-existing backlog gets anchored and a
    failed OTS attempt is retried;
  * BL-1: the money-tip step is BEST EFFORT. The tests below deliberately do NOT patch
    ``attest_payment_journal_tip`` — they run the real function against a real broken
    journal, because patching exactly the function under suspicion is what made CI blind
    to the deploy blocker in the first place.

ADR 0018 §12: the subject is the Payment Control Plane journal, not the retired
``ln_ops_ledger_v2.jsonl``. The guarantee is unchanged — SOME money movement stays
bound into the OTS-anchored chain — but it now points at the journal that is
actually written.
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


def _redirect_journal(monkeypatch, path) -> None:  # noqa: ANN001, ANN202
    """Zeige die prozessweit gecachten Settings auf ein tmp-Journal.

    ``cache_clear`` gehoert dazu: ein reines ``setenv`` bliebe wirkungslos,
    weil ``get_payment_settings`` ``lru_cache`` traegt. Die autouse-Fixture in
    ``tests/conftest.py`` raeumt den Cache nach dem Test wieder auf.
    """
    from app.core.payment_settings import get_payment_settings

    monkeypatch.setenv("APP_PAYMENT_JOURNAL_PATH", str(path))
    get_payment_settings.cache_clear()


def test_torn_payment_journal_warns_but_does_not_block_the_anchor(
    tmp_path, monkeypatch, capsys
) -> None:
    # A power-cut torn tail (the realistic defect): unparseable, so verify_chain()
    # reports not-ok and attest_payment_journal_tip refuses. Before the BL-1 guard
    # this aborted main() BEFORE chain_tip(), and the OTS anchoring of the ENTIRE
    # truth chain stopped silently.
    journal = tmp_path / "payments" / "payment_journal.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text('{"seq": 1, "ts": "2026-09-04T00:00:00+00:0', encoding="utf-8")
    _redirect_journal(monkeypatch, journal)

    rc, calls = _run(
        enabled=True,
        proofs_dir=str(tmp_path),
        anchor_result=AnchorResult(state="anchored", proof_path=str(tmp_path / "p.ots")),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [(_TIP_HASH, "truthledger")]  # the OTS step was still reached
    assert "WARNING payment-journal-tip attestation skipped" in out
    assert "JournalIntegrityError" in out
    assert "payment-journal-tip attested=0/0" in out


def test_missing_payment_journal_is_a_quiet_noop(tmp_path, monkeypatch, capsys) -> None:
    # On a box without a journal yet the step must be a silent 0/0, not a daily
    # WARNING that trains the operator to ignore the line.
    _redirect_journal(monkeypatch, tmp_path / "payments" / "absent.jsonl")
    rc, calls = _run(
        enabled=True,
        proofs_dir=str(tmp_path),
        anchor_result=AnchorResult(state="anchored", proof_path=str(tmp_path / "p.ots")),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [(_TIP_HASH, "truthledger")]
    assert "WARNING" not in out
    assert "payment-journal-tip attested=0/0" in out


def test_valid_payment_journal_tip_is_attested_into_the_run(tmp_path, monkeypatch, capsys) -> None:
    # The happy path the timer relies on: a verified journal contributes its tip.
    from app.payments.journal import PaymentJournal

    journal_path = tmp_path / "payments" / "payment_journal.jsonl"
    _redirect_journal(monkeypatch, journal_path)
    journal = PaymentJournal(journal_path)
    journal.open()
    journal.append("pi_1", "intent_created", {"actor": "operator"})

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
    assert "payment-journal-tip attested=1/1" in out
    assert truth.exists()
