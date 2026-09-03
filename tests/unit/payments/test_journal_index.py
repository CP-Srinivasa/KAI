"""Was der Index ueber das Journal WEISS (ADR 0018 §5/§8).

Der Reconciler stellt drei Fragen, die vorher niemand beantworten konnte:

* *Unter welchem Schluessel dedupliziert der Rail diesen Intent?* — ohne ihn
  gibt es keinen ``lookup``, und ohne ``lookup`` keine Evidenz.
* *Welche Forderung habe ich ausgestellt und noch nicht als beglichen gesehen?*
* *Wann laeuft dieser Intent ab?*

Der Index ist abgeleitet, nie Wahrheit: alles hier muss aus einem Neuaufbau
ueber dieselben Records identisch herauskommen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.payments.journal import PaymentJournal

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _journal(tmp_path: Path) -> PaymentJournal:
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    return journal


def test_dedup_key_kommt_aus_dem_submitted_record(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.append(
        "pi_1",
        "submitted",
        {"status": "SUBMITTED", "rail_dedup_key": "a" * 64, "amount_sent_minor_units": 100},
        ts=NOW,
    )
    assert journal.index.dedup_key("pi_1") == "a" * 64
    assert journal.index.dedup_key("pi_unknown") is None


def test_ein_intent_ohne_send_hat_keinen_dedup_key(tmp_path: Path) -> None:
    """Ohne ``submitted`` ist nichts draussen — und es gibt nichts nachzufragen."""
    journal = _journal(tmp_path)
    journal.append("pi_1", "intent_created", {"status": "REQUESTED"}, ts=NOW)
    assert journal.index.dedup_key("pi_1") is None


def test_ablaufzeit_wird_gefuehrt(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    expiry = int((NOW + timedelta(hours=1)).timestamp())
    journal.append(
        "pi_1", "intent_created", {"status": "REQUESTED", "expires_at_unix": expiry}, ts=NOW
    )
    assert journal.index.expires_at("pi_1") == expiry
    assert journal.index.expires_at("pi_2") is None


def test_offene_forderungen_und_ihre_begleichung(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    ref = "b" * 64
    journal.append(
        "rcv_1",
        "intent_created",
        {"status": "REQUESTED", "invoice_ref_hash": ref, "order_ref": "order-7"},
        ts=NOW,
    )
    open_now = journal.index.open_receivables()
    assert [r.ref_hash for r in open_now] == [ref]
    assert open_now[0].order_ref == "order-7"
    assert open_now[0].intent_id == "rcv_1"

    journal.append("rcv_1", "receivable_settled", {"invoice_ref_hash": ref}, ts=NOW)
    assert journal.index.open_receivables() == []


def test_bereits_gemeldete_waisen_werden_nicht_zweimal_gemeldet(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    key = "c" * 64
    assert journal.index.orphan_keys() == frozenset()
    journal.append("orphan_x", "orphan_settlement", {"rail_dedup_key": key}, ts=NOW)
    assert journal.index.orphan_keys() == frozenset({key})


def test_neuaufbau_ergibt_denselben_index(tmp_path: Path) -> None:
    """Der Index ist abgeleitet — ein Neustart muss ihn exakt herstellen."""
    path = tmp_path / "payment_journal.jsonl"
    journal = PaymentJournal(path)
    journal.open()
    journal.append(
        "pi_1",
        "submitted",
        {"status": "SUBMITTED", "rail_dedup_key": "d" * 64, "amount_sent_minor_units": 10},
        ts=NOW,
    )
    journal.append(
        "rcv_1", "intent_created", {"status": "REQUESTED", "invoice_ref_hash": "e" * 64}, ts=NOW
    )
    journal.append("orphan_y", "orphan_settlement", {"rail_dedup_key": "f" * 64}, ts=NOW)
    live = journal.index.snapshot()

    rebuilt = PaymentJournal(path)
    rebuilt.open()
    assert rebuilt.index.snapshot() == live
    assert rebuilt.index.dedup_key("pi_1") == "d" * 64
    assert [r.ref_hash for r in rebuilt.index.open_receivables()] == ["e" * 64]
    assert rebuilt.index.orphan_keys() == frozenset({"f" * 64})
