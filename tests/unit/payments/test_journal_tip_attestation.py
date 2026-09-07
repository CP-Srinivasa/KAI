"""Der Truth-Anker des Geld-Journals (ADR 0018 §12, PR 1).

Bis zum Rueckbau band ``scripts/truth_anchor_run.py`` den verifizierten Tip des
ALTEN v2-Journals als ``lightning_ops_tip`` in die Truth-Kette; deren Tip geht
per OTS on-chain. Faellt der Altpfad ersatzlos, ist **keine Geldbewegung mehr
on-chain verankert** — das waere der Verlust einer bestehenden
Beweiseigenschaft, nicht bloss weniger Code.

:func:`attest_payment_journal_tip` uebernimmt die Zusage woertlich, inklusive
der unbequemen Haelfte: eine **ungueltige Kette wird verweigert**, nicht
attestiert. Ein gebrochenes Geldjournal in die Truth-Kette zu schreiben hiesse,
es dort zu waschen.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.payments.journal import PaymentJournal
from app.payments.journal_chain import JournalIntegrityError, attest_payment_journal_tip

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _journal_with(records: int, tmp_path: Path) -> Path:
    path = tmp_path / "payments" / "payment_journal.jsonl"
    journal = PaymentJournal(path)
    journal.open()
    for index in range(records):
        with journal.transaction() as tx:
            tx.append(f"pi_{index}", "intent_created", {"actor": "operator"}, ts=NOW)
    return path


def _truth(tmp_path: Path) -> Path:
    return tmp_path / "truth" / "attestation_ledger.jsonl"


def _kinds(path: Path) -> list[str]:
    return [json.loads(line)["kind"] for line in path.read_text(encoding="utf-8").splitlines()]


def test_empty_journal_is_a_quiet_noop(tmp_path: Path) -> None:
    """Kein Record, keine Attestierung — und ausdruecklich keine Warnung."""
    result = attest_payment_journal_tip(
        journal_path=tmp_path / "absent.jsonl",
        truth_path=_truth(tmp_path),
        mirror_audit=False,
    )
    assert result == {"total": 0, "attested": 0, "skipped": 0}
    assert not _truth(tmp_path).exists()


def test_valid_tip_is_attested_once_and_then_skipped(tmp_path: Path) -> None:
    journal_path = _journal_with(2, tmp_path)
    truth_path = _truth(tmp_path)

    first = attest_payment_journal_tip(
        journal_path=journal_path, truth_path=truth_path, mirror_audit=False
    )
    assert first == {"total": 1, "attested": 1, "skipped": 0}
    assert _kinds(truth_path) == ["payment_journal_tip"]

    second = attest_payment_journal_tip(
        journal_path=journal_path, truth_path=truth_path, mirror_audit=False
    )
    assert second == {"total": 1, "attested": 0, "skipped": 1}
    assert _kinds(truth_path) == ["payment_journal_tip"]  # idempotent, kein zweiter Record


def test_the_attested_subject_is_the_verified_tip_hash(tmp_path: Path) -> None:
    journal_path = _journal_with(3, tmp_path)
    truth_path = _truth(tmp_path)
    tip = PaymentJournal(journal_path).verify_chain()

    attest_payment_journal_tip(journal_path=journal_path, truth_path=truth_path, mirror_audit=False)
    record = json.loads(truth_path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["subject_id"] == f"payment-tip:{tip.tip_hash}"
    assert record["payload"]["record_hash"] == tip.tip_hash
    assert record["payload"]["seq"] == 3
    assert record["payload"]["schema"] == "payment-journal-tip/v1"


def test_a_broken_chain_is_refused_not_laundered(tmp_path: Path) -> None:
    """Die unbequeme Haelfte der Zusage — woertlich wie ``attest_ln_ops_tip``."""
    journal_path = _journal_with(2, tmp_path)
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[-1])
    tampered["payload"] = {"actor": "someone_else"}
    lines[-1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(JournalIntegrityError, match="refusing to attest"):
        attest_payment_journal_tip(
            journal_path=journal_path, truth_path=_truth(tmp_path), mirror_audit=False
        )
    assert not _truth(tmp_path).exists()


def test_open_intents_travel_with_the_tip(tmp_path: Path) -> None:
    """Ein Anker ohne die offenen Vorgaenge waere eine halbe Aussage."""
    journal_path = _journal_with(1, tmp_path)
    attest_payment_journal_tip(
        journal_path=journal_path, truth_path=_truth(tmp_path), mirror_audit=False
    )
    record = json.loads(_truth(tmp_path).read_text(encoding="utf-8").splitlines()[-1])
    assert record["payload"]["open_intents"] == ["pi_0"]
