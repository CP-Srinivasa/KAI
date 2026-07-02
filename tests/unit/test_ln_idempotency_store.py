"""A1 FIX-3 — persistent, bounded idempotency-key ledger (replay guard).

The B-005 confirm gate rejects a replayed idempotency key. A process-local ``set``
forgot every consumed key on restart, re-opening the replay window. PersistentSeenKeys
persists keys across a (simulated) restart and stays bounded, while dropping into the
existing ``set[str]`` seam unchanged.
"""

from __future__ import annotations

from pathlib import Path

from app.lightning.control_gate import verify_capital_confirm
from app.lightning.idempotency_store import PersistentSeenKeys


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


def test_persist_failure_is_fail_soft(tmp_path: Path) -> None:
    # A persist error must never crash the control surface: the key is still marked
    # seen in memory (replay guard holds for the process), the error is swallowed.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    store = PersistentSeenKeys(blocker / "seen.jsonl")  # parent is a file → persist fails
    store.add("k1")  # must not raise
    assert "k1" in store


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
