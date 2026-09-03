"""Der Konsument des Geld-Journals (Stream-Vertrag G4 + ADR 0018 §10).

Ein Strom ohne Leser ist ein Produzent, kein System — und ein Geld-Journal,
dessen Kettenbruch niemandem auffaellt, ist schlimmer als keins: es sieht
weiter aus wie ein Beweis.

Freshness scheidet als Ueberwachungsform aus. Der Strom ist ereignisgetrieben
und im Default-Modus SIMULATION voellig legitim tagelang still; eine
Kadenz-Schwelle waere entweder wirkungslos oder ein Daueralarm. Ueberwacht wird
deshalb, was hier wirklich kaputtgehen kann: die Kette.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.alerts.health_check import _check_payment_journal_chain
from app.payments.journal import PaymentJournal

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _journal_in(adir: Path) -> PaymentJournal:
    journal = PaymentJournal(adir / "payments" / "payment_journal.jsonl")
    journal.open()
    return journal


def test_missing_journal_is_not_an_issue(tmp_path: Path) -> None:
    """Eine Anlage in SIMULATION hat noch nie gezahlt — das ist kein Defekt."""
    assert _check_payment_journal_chain(tmp_path) == []


def test_intact_chain_is_not_an_issue(tmp_path: Path) -> None:
    journal = _journal_in(tmp_path)
    journal.append("pi_1", "intent_created", {"status": "REQUESTED"}, ts=NOW)
    journal.append("pi_1", "policy_decided", {"verdict": "ALLOW"}, ts=NOW)
    assert _check_payment_journal_chain(tmp_path) == []


def test_broken_chain_is_critical(tmp_path: Path) -> None:
    journal = _journal_in(tmp_path)
    journal.append("pi_1", "intent_created", {"status": "REQUESTED"}, ts=NOW)
    journal.append("pi_1", "submitted", {"amount_sent_minor_units": 500}, ts=NOW)

    rows = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    rows[1]["payload"]["amount_sent_minor_units"] = 1
    journal.path.write_text(
        "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )

    issues = _check_payment_journal_chain(tmp_path)
    assert len(issues) == 1
    assert issues[0].severity == "critical"
    assert issues[0].component == "payment_journal"
    assert "seq 2" in issues[0].message


def test_torn_tail_is_critical(tmp_path: Path) -> None:
    journal = _journal_in(tmp_path)
    journal.append("pi_1", "intent_created", {"status": "REQUESTED"}, ts=NOW)
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq":2,"partial')

    issues = _check_payment_journal_chain(tmp_path)
    assert len(issues) == 1
    assert issues[0].severity == "critical"


def test_probe_is_wired_into_the_report() -> None:
    """Ein Waechter, den niemand ruft, ist eine Behauptung (Stream-Ratchet G4)."""
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "app" / "alerts" / "health_check.py").read_text(encoding="utf-8")
    marker = source.index("def run_health_check_report")
    assert "_check_payment_journal_chain(" in source[marker:]


def test_stream_contract_declares_this_probe() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    contracts = json.loads(
        (repo_root / "config" / "stream_contracts.json").read_text(encoding="utf-8")
    )
    entry = contracts["streams"]["payment_journal.jsonl"]
    assert entry["monitoring"] == "alternative_watcher"
    assert entry["watcher"] == "_check_payment_journal_chain"
