"""Das eine Geld-Journal (ADR 0018 §5).

Vier Zusagen, jede einzeln pruefbar:

1. **Kette** — jeder Record haengt am Vorgaenger; eine nachtraegliche Aenderung
   ist erkennbar, nicht verhinderbar. Genau das behauptet auch ``ops_ledger``,
   und genau das wird hier auch gebrochen und wieder gefunden.
2. **Torn Tail = Deny** — eine halb geschriebene letzte Zeile beendet das
   Schreiben. Auf die letzte LESBARE Zeile aufzusetzen wuerde das Journal
   still forken.
3. **Ein Lock ueber Prozessgrenzen** — der Reconcile-Timer und der Server
   schreiben in dieselbe Datei. Der Test benutzt echtes ``multiprocessing``,
   keine Threads: ein ``threading.Lock`` haette den Bestandsdefekt
   (``idempotency_store``) nicht gezeigt.
4. **Redaktion am Writer** — ein BOLT11, ein Pubkey oder ein Preimage
   ueberlebt den Append nicht. Nicht "wird maskiert": es steht nicht drin.
"""

from __future__ import annotations

import json
import multiprocessing as mp
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.payments.journal import (
    GENESIS_HASH,
    JournalIntegrityError,
    PaymentJournal,
)

BOLT11 = (
    "lnbc10u1p3pj257pp5yztkwjcz5ftl5laxkav23zmzekaw37zk6kmv80pk4xaev5qhtz7qdpdwd3xger"
    "9wd5kwm36yprx7u3qd36kucmgyp282etnv3shjcqzpgxqyz5vqsp5usyc4lk9chsfp53kvcnvq456ganh"
    "60d89reykdngsmtj6yw3nhvq9qyyssqjcewm5cjwz4a6rfjx77c490yced6pemk0upkxhy89cmm7sct66"
    "k8gneanwykzgdrwrfje69h9u5u0w57rrcsysas7gadwmzxc8c6t0spjazup6"
)
PUBKEY = "03" + "a" * 64
PREIMAGE = "9f" * 32

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def make_journal(tmp_path: Path) -> PaymentJournal:
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    return journal


def intent_payload(**extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "idempotency_key_hash": "1" * 64,
        "amount_minor_units": 1000,
        "currency": "SAT",
        "scale": 0,
        "destination_hash": "2" * 64,
        "actor": "operator",
        "purpose": "self_test",
        "rail": "lightning",
        "mode": "simulation",
        "status": "REQUESTED",
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------- #
# Kette
# --------------------------------------------------------------------------- #


def test_first_record_links_to_genesis(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    event = journal.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    assert event.seq == 1
    assert event.prev_hash == GENESIS_HASH
    assert len(event.record_hash) == 64


def test_chain_links_every_record(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    events = [
        journal.append("pi_1", "intent_created", intent_payload(), ts=NOW),
        journal.append("pi_1", "policy_decided", {"verdict": "ALLOW"}, ts=NOW),
        journal.append("pi_1", "submitted", {"amount_sent_minor_units": 1000}, ts=NOW),
    ]
    assert [e.seq for e in events] == [1, 2, 3]
    assert events[1].prev_hash == events[0].record_hash
    assert events[2].prev_hash == events[1].record_hash
    assert journal.verify_chain().ok


def test_verify_chain_on_an_empty_journal_is_ok(tmp_path: Path) -> None:
    assert make_journal(tmp_path).verify_chain().ok


def test_tampered_payload_is_detected(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    journal.append("pi_1", "policy_decided", {"verdict": "ALLOW"}, ts=NOW)

    rows = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    rows[0]["payload"]["amount_minor_units"] = 999_999
    journal.path.write_text(
        "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )

    status = PaymentJournal(journal.path).verify_chain()
    assert not status.ok
    assert "record_hash" in status.reason
    assert status.broken_at_seq == 1


def test_tampered_prev_hash_is_detected(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    journal.append("pi_1", "policy_decided", {"verdict": "ALLOW"}, ts=NOW)

    rows = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    rows[1]["prev_hash"] = "0" * 63 + "1"
    journal.path.write_text(
        "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )

    status = PaymentJournal(journal.path).verify_chain()
    assert not status.ok
    assert status.broken_at_seq == 2


def test_deleted_record_breaks_the_chain(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    for _ in range(3):
        journal.append("pi_1", "policy_decided", {"verdict": "ALLOW"}, ts=NOW)
    lines = journal.path.read_text(encoding="utf-8").splitlines()
    journal.path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    assert not PaymentJournal(journal.path).verify_chain().ok


def test_open_refuses_a_broken_chain(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    text = journal.path.read_text(encoding="utf-8")
    journal.path.write_text(
        text.replace('"amount_minor_units":1000', '"amount_minor_units":2'),
        encoding="utf-8",
    )
    with pytest.raises(JournalIntegrityError):
        PaymentJournal(journal.path).open()


# --------------------------------------------------------------------------- #
# Torn Tail
# --------------------------------------------------------------------------- #


def test_torn_tail_refuses_further_writes(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"schema":"payment-journal/v1","seq":2,"ts":"2026-09')

    fresh = PaymentJournal(journal.path)
    with pytest.raises(JournalIntegrityError, match="torn"):
        fresh.open()
    with pytest.raises(JournalIntegrityError):
        fresh.append("pi_1", "policy_decided", {"verdict": "ALLOW"}, ts=NOW)


def test_torn_tail_is_not_silently_dropped(tmp_path: Path) -> None:
    """Der halbe Rest bleibt liegen — Reparatur ist eine Operator-Handlung."""
    journal = make_journal(tmp_path)
    journal.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq":2,"broken')
    before = journal.path.read_bytes()
    with pytest.raises(JournalIntegrityError):
        PaymentJournal(journal.path).append("pi_1", "failed", {}, ts=NOW)
    assert journal.path.read_bytes() == before


def test_interior_corruption_refuses_the_append(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    journal.append("pi_1", "policy_decided", {"verdict": "ALLOW"}, ts=NOW)
    lines = journal.path.read_text(encoding="utf-8").splitlines()
    journal.path.write_text("not json at all\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(JournalIntegrityError):
        PaymentJournal(journal.path).open()


# --------------------------------------------------------------------------- #
# Redaktion
# --------------------------------------------------------------------------- #


def test_raw_bolt11_never_reaches_the_file(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append(
        "pi_1",
        "intent_created",
        intent_payload(destination=BOLT11, payment_request=BOLT11),
        ts=NOW,
    )
    text = journal.path.read_text(encoding="utf-8")
    assert BOLT11 not in text
    assert "lnbc" not in text


def test_raw_pubkey_never_reaches_the_file(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append("pi_1", "rail_requested", {"dest_pubkey_hex": PUBKEY, "peer": PUBKEY}, ts=NOW)
    assert PUBKEY not in journal.path.read_text(encoding="utf-8")


def test_preimage_never_reaches_the_file(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append(
        "pi_1",
        "settled",
        {"preimage": PREIMAGE, "payment_preimage": PREIMAGE, "proof_hash": "5" * 64},
        ts=NOW,
    )
    text = journal.path.read_text(encoding="utf-8")
    assert PREIMAGE not in text
    assert "5" * 64 in text


def test_unknown_payload_keys_are_dropped_not_stored(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    event = journal.append("pi_1", "intent_created", intent_payload(secret_note="hello"), ts=NOW)
    assert "secret_note" not in event.payload
    assert "hello" not in journal.path.read_text(encoding="utf-8")


def test_a_hash_field_must_actually_be_a_hash(tmp_path: Path) -> None:
    """Ein ``*_hash``-Feld mit einem Rohwert ist der bequemste Weg, die
    Redaktion zu umgehen — der Wert fliegt raus, nicht der Name."""
    journal = make_journal(tmp_path)
    event = journal.append(
        "pi_1", "intent_created", intent_payload(destination_hash=BOLT11), ts=NOW
    )
    assert "destination_hash" not in event.payload
    assert BOLT11 not in journal.path.read_text(encoding="utf-8")


def test_long_free_text_is_truncated(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    event = journal.append("pi_1", "failed", {"failure_reason": "x" * 500}, ts=NOW)
    assert len(str(event.payload["failure_reason"])) <= 128


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #


def test_index_maps_idempotency_key_hash_to_intent(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    assert journal.index.intent_for_key("1" * 64) == "pi_1"
    assert journal.index.intent_for_key("f" * 64) is None


def test_index_tracks_open_intents(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    journal.append("pi_2", "intent_created", intent_payload(idempotency_key_hash="3" * 64), ts=NOW)
    assert journal.index.open_intents() == {"pi_1", "pi_2"}

    journal.append("pi_1", "settled", {"status": "SETTLED"}, ts=NOW)
    assert journal.index.open_intents() == {"pi_2"}


def test_index_counts_amounts_per_utc_day(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append("pi_1", "submitted", {"amount_sent_minor_units": 700}, ts=NOW)
    journal.append("pi_1", "settled", {"amount_settled_minor_units": 690}, ts=NOW)
    totals = journal.index.totals_for_day(NOW)
    assert totals.amount_sent == 700
    assert totals.amount_settled == 690


def test_day_boundary_is_utc_not_local(tmp_path: Path) -> None:
    """23:59:59Z und 00:00:00Z sind verschiedene Tage — auch auf einem Pi in CEST."""
    journal = make_journal(tmp_path)
    late = datetime(2026, 9, 3, 23, 59, 59, tzinfo=UTC)
    early = late + timedelta(seconds=1)
    journal.append("pi_1", "submitted", {"amount_sent_minor_units": 100}, ts=late)
    journal.append("pi_2", "submitted", {"amount_sent_minor_units": 200}, ts=early)
    assert journal.index.totals_for_day(late).amount_sent == 100
    assert journal.index.totals_for_day(early).amount_sent == 200


def test_index_rebuild_after_restart_is_identical(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    journal.append("pi_1", "submitted", {"amount_sent_minor_units": 1000}, ts=NOW)
    journal.append("pi_2", "intent_created", intent_payload(idempotency_key_hash="4" * 64), ts=NOW)
    journal.append(
        "pi_1", "settled", {"amount_settled_minor_units": 998, "status": "SETTLED"}, ts=NOW
    )

    restarted = PaymentJournal(journal.path)
    restarted.open()
    assert restarted.index.snapshot() == journal.index.snapshot()
    assert restarted.index.open_intents() == {"pi_2"}
    assert restarted.index.totals_for_day(NOW).amount_settled == 998


def test_refresh_tail_sees_a_foreign_append(tmp_path: Path) -> None:
    """Der Reconcile-Timer haengt an, waehrend der Server laeuft."""
    server = make_journal(tmp_path)
    server.append("pi_1", "intent_created", intent_payload(), ts=NOW)

    timer = PaymentJournal(server.path)
    timer.open()
    timer.append("pi_1", "reconciled", {"status": "RECONCILIATION_REQUIRED"}, ts=NOW)

    assert server.index.intent_status("pi_1") == "REQUESTED"
    server.refresh_tail()
    assert server.index.intent_status("pi_1") == "RECONCILIATION_REQUIRED"


def test_append_after_a_foreign_append_keeps_the_chain(tmp_path: Path) -> None:
    server = make_journal(tmp_path)
    server.append("pi_1", "intent_created", intent_payload(), ts=NOW)
    timer = PaymentJournal(server.path)
    timer.open()
    timer.append("pi_1", "reconciled", {}, ts=NOW)

    event = server.append("pi_1", "final", {"status": "SETTLED"}, ts=NOW)
    assert event.seq == 3
    assert server.verify_chain().ok


# --------------------------------------------------------------------------- #
# Zwei Prozesse
# --------------------------------------------------------------------------- #


def _child_appends(path_str: str, tag: str, count: int) -> None:
    """Kind-Prozess: haengt ``count`` Records an dasselbe Journal."""
    journal = PaymentJournal(Path(path_str))
    journal.open()
    for i in range(count):
        journal.append(f"pi_{tag}_{i}", "policy_decided", {"verdict": "ALLOW"}, ts=NOW)


def test_two_processes_append_without_losing_records(tmp_path: Path) -> None:
    path = tmp_path / "payment_journal.jsonl"
    PaymentJournal(path).open()

    ctx = mp.get_context("spawn")
    children = [
        ctx.Process(target=_child_appends, args=(str(path), "a", 50)),
        ctx.Process(target=_child_appends, args=(str(path), "b", 50)),
    ]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=180)
        assert child.exitcode == 0, f"child failed with exitcode {child.exitcode}"

    verified = PaymentJournal(path)
    status = verified.verify_chain()
    assert status.ok, status.reason

    lines = path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    assert len(rows) == 100, "lost update: ein Append hat einen anderen ueberschrieben"
    assert [r["seq"] for r in rows] == list(range(1, 101))
    assert len({r["intent_id"] for r in rows}) == 100


# --------------------------------------------------------------------------- #
# Pfad-Aufloesung
# --------------------------------------------------------------------------- #


def test_default_path_comes_from_settings_not_from_the_cwd() -> None:
    from app.core.payment_settings import PaymentSettings

    journal = PaymentJournal()
    assert journal.path == PaymentSettings().resolved_journal_path()
    assert journal.path.is_absolute()


def test_constructor_argument_is_the_only_override(tmp_path: Path) -> None:
    """Kein Env-Hintertuerchen: ein Test, der das Journal umbiegt, tut es sichtbar."""
    target = tmp_path / "scratch.jsonl"
    assert PaymentJournal(target).path == target
