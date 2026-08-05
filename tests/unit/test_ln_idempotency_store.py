"""A1 FIX-3 — persistent, bounded idempotency-key ledger (replay guard).

The B-005 confirm gate rejects a replayed idempotency key. A process-local ``set``
forgot every consumed key on restart, re-opening the replay window. PersistentSeenKeys
persists keys across a (simulated) restart and stays bounded, while dropping into the
existing ``set[str]`` seam unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.lightning.control_gate import verify_capital_confirm
from app.lightning.idempotency_store import IdempotencyPersistenceError, PersistentSeenKeys


class _FakeHotp:
    def verify(self, code: str) -> bool:
        if code != "good":
            raise RuntimeError("hotp verification failed")
        return True


def test_replay_detected_across_simulated_restart(tmp_path: Path) -> None:
    p = tmp_path / "seen.jsonl"
    store = PersistentSeenKeys(p)
    store.add("k1")
    assert "k1" in store

    # Simulate a process restart: a fresh instance reloads from disk.
    reloaded = PersistentSeenKeys(p)
    assert "k1" in reloaded  # the replay guard survived the "restart"


def test_add_is_idempotent_and_persists_once(tmp_path: Path) -> None:
    p = tmp_path / "seen.jsonl"
    store = PersistentSeenKeys(p)
    store.add("k1")
    store.add("k1")  # re-add must not duplicate
    assert len(PersistentSeenKeys(p)) == 1


def test_bounding_evicts_oldest_keys(tmp_path: Path) -> None:
    p = tmp_path / "seen.jsonl"
    store = PersistentSeenKeys(p, max_keys=3)
    for i in range(5):
        store.add(f"k{i}")
    assert len(store) == 3
    assert "k0" not in store and "k1" not in store
    assert {"k2", "k3", "k4"} <= store

    # The on-disk file is bounded too: a reload keeps only the last max_keys.
    reloaded = PersistentSeenKeys(p, max_keys=3)
    assert len(reloaded) == 3
    assert "k4" in reloaded and "k0" not in reloaded


def test_missing_file_loads_empty(tmp_path: Path) -> None:
    store = PersistentSeenKeys(tmp_path / "nope.jsonl")
    assert len(store) == 0
    assert "anything" not in store


def test_corrupt_line_is_tolerated(tmp_path: Path) -> None:
    p = tmp_path / "seen.jsonl"
    p.write_text(
        '{"key": "good", "ts": "t1"}\nNOT JSON\n{"key": "good2", "ts": "t2"}\n',
        encoding="utf-8",
    )
    store = PersistentSeenKeys(p)
    assert "good" in store and "good2" in store


def test_clear_truncates_memory_and_disk(tmp_path: Path) -> None:
    p = tmp_path / "seen.jsonl"
    store = PersistentSeenKeys(p)
    store.add("k1")
    store.clear()
    assert "k1" not in store
    assert "k1" not in PersistentSeenKeys(p)  # gone on disk too


def test_persist_failure_is_fail_closed(tmp_path: Path) -> None:
    # A persist error must NOT be swallowed. Swallowing it left the caller believing
    # it holds a restart-safe replay guard while the disk knew nothing: confirmed
    # spend + restart + replayed request = a second spend. Raising here means the
    # value action is denied — the money never moves without a durable guard.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    store = PersistentSeenKeys(blocker / "seen.jsonl")  # parent is a file → persist fails
    with pytest.raises(IdempotencyPersistenceError):
        store.add("k1")
    # And the in-memory view must NOT pretend the key was consumed.
    assert "k1" not in store


def test_consume_is_atomic_check_and_set(tmp_path: Path) -> None:
    # ``consume`` is the honest primitive behind ``add``: True exactly once per key,
    # so a caller cannot pass the membership check twice for the same key.
    store = PersistentSeenKeys(tmp_path / "seen.jsonl")
    assert store.consume("k1") is True
    assert store.consume("k1") is False
    assert "k1" in PersistentSeenKeys(tmp_path / "seen.jsonl")


def test_key_becomes_visible_only_after_a_successful_persist(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "seen.jsonl"
    store = PersistentSeenKeys(p)
    store.add("first")

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", _boom)
    with pytest.raises(IdempotencyPersistenceError):
        store.add("second")
    assert "first" in store and "second" not in store
    monkeypatch.undo()
    assert set(PersistentSeenKeys(p)) == {"first"}


def test_drops_into_confirm_gate_seam(tmp_path: Path) -> None:
    # The store must behave like the set[str] the B-005 gate expects: a fresh confirm
    # succeeds + consumes the key; a replay of the same key is then rejected.
    p = tmp_path / "seen.jsonl"
    store = PersistentSeenKeys(p)
    first = verify_capital_confirm(
        hotp_verifier=_FakeHotp(),
        hotp_code="good",
        submitted_plan_hash="h",
        expected_plan_hash="h",
        idempotency_key="key-1",
        seen_keys=store,
    )
    assert first.ok and "key-1" in store

    # A restart must NOT clear the consumed key → replay stays rejected.
    replay = verify_capital_confirm(
        hotp_verifier=_FakeHotp(),
        hotp_code="good",
        submitted_plan_hash="h",
        expected_plan_hash="h",
        idempotency_key="key-1",
        seen_keys=PersistentSeenKeys(p),
    )
    assert not replay.ok and "replay" in replay.reason
