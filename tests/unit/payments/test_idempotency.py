"""Idempotenz im Journal, nicht daneben (ADR 0017 §5).

Der Bestand hat dafuer ein zweites Artefakt (``lightning/idempotency_store.py``)
mit drei Defekten, die hier alle strukturell entfallen:

* ``threading.Lock`` statt Interprozess-Lock → zwei Prozesse verlieren Keys
  (Lost Update beim Full-Rewrite). Hier liegt der Konsum unter demselben
  ``portalocker``-Lock wie der Append.
* ``_DEFAULT_MAX_KEYS = 1000`` mit ``popitem`` → ein verdraengter Key ist
  wieder replaybar. Hier gibt es KEIN Evict: append-only heisst append-only.
* Zwei Substrate, die auseinanderlaufen koennen. Hier ist der Key Teil
  desselben Records, der den Intent begruendet.

Der Key steht als HASH im Journal. Er ist ein vom Aufrufer gewaehlter Wert und
kann alles enthalten; fuer die Dedup genuegt sein Hash vollstaendig.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.payments.enums import PaymentMode
from app.payments.idempotency import consume, hash_idempotency_key
from app.payments.journal import PaymentJournal
from app.payments.models import Money, PaymentIntent

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def an_intent(intent_id: str, key: str, *, destination: str = "lnbc10u1pexample") -> PaymentIntent:
    return PaymentIntent(
        intent_id=intent_id,
        idempotency_key=key,
        correlation_id="corr-1",
        actor="operator",
        purpose="self_test",
        rail="lightning",
        destination=destination,
        amount_requested=sat(1000),
        fee_limit=sat(10),
        created_at=NOW,
        expires_at=datetime(2026, 9, 3, 13, 0, tzinfo=UTC),
        mode=PaymentMode.SIMULATION,
    )


def rows(path: Path) -> list[dict[str, object]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def make_journal(tmp_path: Path) -> PaymentJournal:
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    return journal


# --------------------------------------------------------------------------- #
# Seriell
# --------------------------------------------------------------------------- #


def test_first_consume_creates_the_intent(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    outcome = consume(journal, "idem-0123456789abcdef", an_intent("pi_1", "idem-0123456789abcdef"))
    assert outcome.replayed is False
    assert outcome.intent_id == "pi_1"
    assert len(rows(journal.path)) == 1


def test_second_consume_replays_and_writes_nothing(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    key = "idem-0123456789abcdef"
    consume(journal, key, an_intent("pi_1", key))
    outcome = consume(journal, key, an_intent("pi_2", key))
    assert outcome.replayed is True
    assert outcome.intent_id == "pi_1", "der zweite Aufruf darf keinen neuen Intent erzeugen"
    assert len(rows(journal.path)) == 1


def test_replay_survives_a_restart(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    key = "idem-0123456789abcdef"
    consume(journal, key, an_intent("pi_1", key))

    restarted = PaymentJournal(journal.path)
    restarted.open()
    outcome = consume(restarted, key, an_intent("pi_2", key))
    assert outcome.replayed is True
    assert outcome.intent_id == "pi_1"


def test_a_different_key_with_identical_content_is_a_new_intent(tmp_path: Path) -> None:
    """Der Key ist die Absicht des Aufrufers, nicht ein Fingerabdruck des Inhalts.

    Zweimal denselben Betrag an dasselbe Ziel zu zahlen ist ein legitimer
    Vorgang; ihn am Inhalt zu deduplizieren wuerde eine echte zweite Zahlung
    verschlucken.
    """
    journal = make_journal(tmp_path)
    first = consume(journal, "idem-aaaaaaaaaaaaaaaa", an_intent("pi_1", "idem-aaaaaaaaaaaaaaaa"))
    second = consume(journal, "idem-bbbbbbbbbbbbbbbb", an_intent("pi_2", "idem-bbbbbbbbbbbbbbbb"))
    assert (first.replayed, second.replayed) == (False, False)
    assert {first.intent_id, second.intent_id} == {"pi_1", "pi_2"}
    assert len(rows(journal.path)) == 2


def test_the_raw_key_never_reaches_the_journal(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    key = "idem-secret-value-0123"
    consume(journal, key, an_intent("pi_1", key))
    text = journal.path.read_text(encoding="utf-8")
    assert key not in text
    assert hash_idempotency_key(key) in text


def test_destination_is_bound_as_a_hash(tmp_path: Path) -> None:
    """ADR §11: Destination-Bindung im Journal — aber nie im Klartext."""
    journal = make_journal(tmp_path)
    key = "idem-0123456789abcdef"
    consume(journal, key, an_intent("pi_1", key, destination="lnbc10u1pexamplerawvalue"))
    record = rows(journal.path)[0]
    payload = record["payload"]
    assert isinstance(payload, dict)
    assert len(str(payload["destination_hash"])) == 64
    assert "lnbc" not in journal.path.read_text(encoding="utf-8")


def test_no_eviction_after_many_keys(tmp_path: Path) -> None:
    """Eine Verdraengungsgrenze waere ein Replay-Fenster mit Countdown."""
    journal = make_journal(tmp_path)
    first_key = "idem-key-000000000000"
    consume(journal, first_key, an_intent("pi_first", first_key))
    for i in range(1, 60):
        key = f"idem-key-{i:012d}"
        consume(journal, key, an_intent(f"pi_{i}", key))
    outcome = consume(journal, first_key, an_intent("pi_late", first_key))
    assert outcome.replayed is True
    assert outcome.intent_id == "pi_first"


# --------------------------------------------------------------------------- #
# Nebenlaeufig
# --------------------------------------------------------------------------- #


def test_two_threads_with_the_same_key_produce_one_intent(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    key = "idem-0123456789abcdef"
    results: list[bool] = []
    barrier = threading.Barrier(2)

    def worker(intent_id: str) -> None:
        barrier.wait()
        results.append(consume(journal, key, an_intent(intent_id, key)).replayed)

    threads = [threading.Thread(target=worker, args=(f"pi_{i}",)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(results) == [False, True]
    assert len(rows(journal.path)) == 1


def _child_consume(path_str: str, key: str, intent_id: str, out_path: str) -> None:
    journal = PaymentJournal(Path(path_str))
    journal.open()
    outcome = consume(journal, key, an_intent(intent_id, key))
    Path(out_path).write_text(
        json.dumps({"replayed": outcome.replayed, "intent_id": outcome.intent_id}),
        encoding="utf-8",
    )


def test_two_processes_with_the_same_key_produce_one_intent(tmp_path: Path) -> None:
    path = tmp_path / "payment_journal.jsonl"
    PaymentJournal(path).open()
    key = "idem-0123456789abcdef"

    ctx = mp.get_context("spawn")
    outs = [str(tmp_path / "a.json"), str(tmp_path / "b.json")]
    children = [
        ctx.Process(target=_child_consume, args=(str(path), key, "pi_a", outs[0])),
        ctx.Process(target=_child_consume, args=(str(path), key, "pi_b", outs[1])),
    ]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=180)
        assert child.exitcode == 0

    results = [json.loads(Path(out).read_text(encoding="utf-8")) for out in outs]
    assert sorted(r["replayed"] for r in results) == [False, True]
    assert len({r["intent_id"] for r in results}) == 1, "beide muessen denselben Intent sehen"
    assert len(rows(path)) == 1, "zwei Prozesse, ein Record — sonst ist der Key doppelt konsumiert"
