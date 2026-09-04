"""Der Abgleich der beiden Geldjournale waehrend des Dual-Read (ADR 0018 §8/§12).

**Die bekannte Grenze, die diese Datei schliesst.** Bis zum Rueckbau des
Altpfads fuehren zwei Dateien Buch ueber Wertbewegungen:
``artifacts/ln_ops_ledger_v2.jsonl`` (Bestand) und
``artifacts/payments/payment_journal.jsonl`` (Control Plane). Der Reconciler
hielt bisher jede EINZELN gegen den Node — aber nie gegeneinander. Ein Vorgang,
den beide fuehren und den der Altpfad fuer offen oder unbewiesen (``error``)
haelt, war damit unsichtbar: zwei Buchfuehrungen ueber dasselbe Geld, von denen
eine sagt "vielleicht unterwegs" und die andere "erledigt".

Der Vergleich laeuft ueber den ``payment_hash``. Das ist der einzige
Schluessel, den beide Seiten kennen und den der Rail selbst zur Dedup benutzt —
Intent-IDs sind je Journal eigene Vokabulare.

**Nur lesen.** Kein Pfad hier schreibt jemals in das v2-Journal. Der Befund
landet als ``dual_journal_conflict`` im Payment-Journal, wo er in die
bestehende ``attention``-Kette und damit in den Telegram-Alarm faellt.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.payments.journal import PaymentJournal
from app.payments.reconcile_dual import dual_journal_pass

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
HASH_A = "aa" * 32
HASH_B = "bb" * 32


def a_journal(tmp_path: Path) -> PaymentJournal:
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    return journal


def with_send(journal: PaymentJournal, intent_id: str, payment_hash: str) -> None:
    """Ein Vorgang im Control Plane, der bereits abgeschickt wurde."""
    journal.append(intent_id, "intent_created", {"status": "REQUESTED"}, ts=NOW)
    journal.append(
        intent_id,
        "submitted",
        {"status": "SUBMITTED", "rail_dedup_key": payment_hash, "attempt_no": 1},
        ts=NOW,
    )
    journal.append(intent_id, "settled", {"status": "SETTLED"}, ts=NOW)


def legacy(tmp_path: Path, *rows: dict[str, object]) -> Path:
    path = tmp_path / "ln_ops_ledger_v2.jsonl"
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def row(intent_id: str, state: str, payment_hash: str = "") -> dict[str, object]:
    return {
        "ts": NOW.isoformat(),
        "intent_id": intent_id,
        "action": "pay_invoice",
        "state": state,
        "plan": {"payment_hash": payment_hash, "amount_sat": 1000},
        "response": {},
    }


def conflicts(journal: PaymentJournal) -> list[dict[str, object]]:
    return [
        dict(event.payload)
        for event in journal.events()
        if event.event_type == "dual_journal_conflict"
    ]


# --------------------------------------------------------------------------- #
# Der Befund
# --------------------------------------------------------------------------- #


def test_an_open_legacy_intent_for_a_known_payment_is_reported(tmp_path: Path) -> None:
    """Beide Buecher fuehren dieselbe Zahlung, und das alte haelt sie fuer offen."""
    journal = a_journal(tmp_path)
    with_send(journal, "pi_dual0000000001", HASH_A)
    path = legacy(tmp_path, row("legacy-1", "intent", HASH_A))

    counts: dict[str, int] = {}
    found = dual_journal_pass(journal, counts=counts, now=NOW, legacy_path=path)

    assert found == (HASH_A,)
    assert counts["DUAL_JOURNAL_CONFLICT"] == 1
    payload = conflicts(journal)[0]
    assert payload["rail_dedup_key"] == HASH_A
    assert payload["observed_status"] == "intent"
    assert payload["evidence_source"] == "ln_ops_ledger_v2"
    assert payload["status"] == "attention"


def test_a_legacy_error_outcome_counts_as_unresolved(tmp_path: Path) -> None:
    """``error`` heisst im Altpfad ausdruecklich *wir wissen es nicht* (m-14).

    Es ist dort zwar terminal, aber terminal ohne Beweis — und damit genau der
    Fall, in dem zwei Buchfuehrungen ueber dasselbe Geld gefaehrlich werden.
    """
    journal = a_journal(tmp_path)
    with_send(journal, "pi_dual0000000002", HASH_A)
    path = legacy(tmp_path, row("legacy-1", "intent", HASH_A), row("legacy-1", "error", HASH_A))

    assert dual_journal_pass(journal, counts={}, now=NOW, legacy_path=path) == (HASH_A,)


def test_a_proven_legacy_outcome_is_no_conflict(tmp_path: Path) -> None:
    """``executed`` ist bewiesen — der Altpfad hat seine Frage beantwortet."""
    journal = a_journal(tmp_path)
    with_send(journal, "pi_dual0000000003", HASH_A)
    path = legacy(tmp_path, row("legacy-1", "intent", HASH_A), row("legacy-1", "executed", HASH_A))

    assert dual_journal_pass(journal, counts={}, now=NOW, legacy_path=path) == ()
    assert conflicts(journal) == []


def test_a_legacy_intent_the_control_plane_never_saw_is_no_conflict(tmp_path: Path) -> None:
    """Ein offener Alt-Vorgang allein ist kein Doppelbefund.

    Er gehoert dem alten Reconciler. Ihn hier zu melden hiesse, waehrend der
    Uebergangsphase jede offene Alt-Zeile in den Payment-Alarm zu heben — und
    eine Wache, die immer schlaegt, wird abgeschaltet.
    """
    journal = a_journal(tmp_path)
    with_send(journal, "pi_dual0000000004", HASH_A)
    path = legacy(tmp_path, row("legacy-1", "intent", HASH_B))

    assert dual_journal_pass(journal, counts={}, now=NOW, legacy_path=path) == ()


# --------------------------------------------------------------------------- #
# Laerm-Vermeidung und Robustheit
# --------------------------------------------------------------------------- #


def test_a_conflict_is_reported_exactly_once(tmp_path: Path) -> None:
    """Ein Alarm im Fuenf-Minuten-Takt ist ein stummgeschalteter Alarm."""
    journal = a_journal(tmp_path)
    with_send(journal, "pi_dual0000000005", HASH_A)
    path = legacy(tmp_path, row("legacy-1", "intent", HASH_A))

    assert dual_journal_pass(journal, counts={}, now=NOW, legacy_path=path) == (HASH_A,)
    assert dual_journal_pass(journal, counts={}, now=NOW, legacy_path=path) == ()
    assert len(conflicts(journal)) == 1


def test_the_pass_never_writes_to_the_legacy_journal(tmp_path: Path) -> None:
    """Das alte Journal ist waehrend des Dual-Read read-only. Ohne Ausnahme."""
    journal = a_journal(tmp_path)
    with_send(journal, "pi_dual0000000006", HASH_A)
    path = legacy(tmp_path, row("legacy-1", "intent", HASH_A))
    before = path.read_bytes()

    dual_journal_pass(journal, counts={}, now=NOW, legacy_path=path)

    assert path.read_bytes() == before


def test_a_missing_legacy_journal_is_not_a_finding(tmp_path: Path) -> None:
    """Auf einer Anlage ohne Altpfad gibt es nichts abzugleichen."""
    journal = a_journal(tmp_path)
    with_send(journal, "pi_dual0000000007", HASH_A)
    assert (
        dual_journal_pass(
            journal, counts={}, now=NOW, legacy_path=tmp_path / "does-not-exist.jsonl"
        )
        == ()
    )


def test_an_unreadable_legacy_line_does_not_stop_the_pass(tmp_path: Path) -> None:
    """Der Payment-Reconciler darf nicht an der Form des ALTEN Journals haengen.

    Dessen Kette prueft der alte Pfad; hier zaehlt nur, was lesbar ist. Eine
    kaputte Zeile darf den Abgleich der uebrigen nicht verhindern.
    """
    journal = a_journal(tmp_path)
    with_send(journal, "pi_dual0000000008", HASH_A)
    path = legacy(tmp_path, row("legacy-1", "intent", HASH_A))
    path.write_text("{kaputt\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    assert dual_journal_pass(journal, counts={}, now=NOW, legacy_path=path) == (HASH_A,)


def test_a_legacy_row_without_a_payment_hash_is_skipped(tmp_path: Path) -> None:
    """Ohne gemeinsamen Schluessel gibt es keine Aussage — keysend, on-chain, Channel."""
    journal = a_journal(tmp_path)
    with_send(journal, "pi_dual0000000009", HASH_A)
    path = legacy(tmp_path, row("legacy-1", "intent", ""))

    assert dual_journal_pass(journal, counts={}, now=NOW, legacy_path=path) == ()


def test_the_conflict_record_carries_no_raw_rail_material(tmp_path: Path) -> None:
    """Die Redaktionsgrenze gilt auch fuer den neuen Record."""
    journal = a_journal(tmp_path)
    with_send(journal, "pi_dual0000000010", HASH_A)
    path = legacy(tmp_path, row("legacy-1", "intent", HASH_A))
    dual_journal_pass(journal, counts={}, now=NOW, legacy_path=path)

    raw = journal.path.read_text(encoding="utf-8")
    assert "lnbc" not in raw
    assert "legacy-1" not in raw, "die Alt-Intent-ID ist Fremdvokabular, nicht Evidenz"


# --------------------------------------------------------------------------- #
# Verdrahtung
# --------------------------------------------------------------------------- #


def test_the_event_type_is_part_of_the_journal_vocabulary() -> None:
    from app.payments.enums import AUDIT_EVENT_TYPES

    assert "dual_journal_conflict" in AUDIT_EVENT_TYPES


@pytest.mark.parametrize("name", ["dual_journal_pass", "legacy_journal_path"])
def test_the_module_exposes_what_the_reconciler_needs(name: str) -> None:
    import app.payments.reconcile_dual as module

    assert hasattr(module, name)
