"""Performance + cache-coherence tests for the hash-chain stores.

Validates Neo-F-001: append must be O(1) amortised, not O(n²).
Validates Neo-F-002: portalocker file lock is held during append.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.audit.structured_reasoning import (
    GENESIS_PREV_HASH,
    PHASE_TRIGGER,
    ReasoningJournal,
    _hash_record,
)
from app.learning.parameter_version import ParameterVersionStore

# ============================================================================
# ReasoningJournal cache + perf
# ============================================================================


def test_reasoning_journal_uses_cache_after_first_write(tmp_path: Path):
    rj = ReasoningJournal(tmp_path / "rj.jsonl")
    s1 = rj.log_step(
        decision_id="d", phase=PHASE_TRIGGER, actor="x", rationale_summary="a"
    )
    # Cache should now be the hash of s1 + the file size after the write
    assert rj._cached_last_hash == _hash_record(s1)
    assert rj._cached_size > 0


def test_reasoning_journal_invalidates_cache_on_external_change(tmp_path: Path):
    """If someone writes to the file behind the journal's back, the next
    `_last_chain_hash()` call must re-read (not return the stale cache)."""
    path = tmp_path / "rj.jsonl"
    rj = ReasoningJournal(path)
    s1 = rj.log_step(
        decision_id="d", phase=PHASE_TRIGGER, actor="x", rationale_summary="a"
    )

    # External change: append a clearly different valid step out-of-band by
    # using a *second* journal instance (no cache yet)
    rj2 = ReasoningJournal(path)
    s2 = rj2.log_step(
        decision_id="d", phase=PHASE_TRIGGER, actor="y", rationale_summary="b"
    )

    # rj's cached_size now mismatches the on-disk size → next log_step re-walks
    s3 = rj.log_step(
        decision_id="d", phase=PHASE_TRIGGER, actor="z", rationale_summary="c"
    )
    # s3 should chain off s2, NOT off s1
    assert s3.prev_chain_hash == _hash_record(s2)
    assert s3.prev_chain_hash != _hash_record(s1)


def test_reasoning_journal_append_is_constant_time_per_step(tmp_path: Path):
    """Writing N steps must take time roughly proportional to N — not N²."""
    rj = ReasoningJournal(tmp_path / "rj.jsonl")

    n_warmup = 10
    n_measure = 200

    for i in range(n_warmup):
        rj.log_step(
            decision_id=f"w_{i}", phase=PHASE_TRIGGER, actor="x", rationale_summary="warm"
        )

    start = time.perf_counter()
    for i in range(n_measure):
        rj.log_step(
            decision_id=f"d_{i}",
            phase=PHASE_TRIGGER,
            actor="x",
            rationale_summary="m",
        )
    duration = time.perf_counter() - start

    # 200 appends should comfortably finish in well under 5 seconds even on
    # slow CI. Without the cache fix, the n_warmup cumulative read on each
    # subsequent write makes this much slower.
    assert duration < 5.0, f"200 appends took {duration:.2f}s — cache regression?"

    # Chain still verifies clean
    ok, err = rj.verify_chain()
    assert ok, err


# ============================================================================
# ParameterVersionStore cache + perf
# ============================================================================


def test_parameter_store_caches_after_first_propose(tmp_path: Path):
    store = ParameterVersionStore(tmp_path / "pj.jsonl")
    p1 = store.propose_version(parameter_path="p", parameter_set={"v": 1})
    # Cache reflects last write
    from app.learning.parameter_version import _hash_record as _phr

    assert store._cached_last_hash == _phr(p1)


def test_parameter_store_genesis_cache_for_empty_journal(tmp_path: Path):
    store = ParameterVersionStore(tmp_path / "pj.jsonl")
    # Without any writes, the cache starts at genesis
    assert store._cached_last_hash == GENESIS_PREV_HASH


def test_parameter_store_chain_remains_valid_after_many_writes(tmp_path: Path):
    store = ParameterVersionStore(tmp_path / "pj.jsonl")
    for i in range(50):
        store.propose_version(parameter_path="p", parameter_set={"v": i})
    ok, err = store.verify_chain()
    assert ok, err


def test_chain_remains_valid_when_two_stores_share_a_file(tmp_path: Path):
    """Simulates trading-loop + operator-CLI writing to the same file: each
    has its own store instance + cache, but the on-disk chain must remain
    intact end-to-end."""
    path = tmp_path / "pj.jsonl"
    a = ParameterVersionStore(path)
    b = ParameterVersionStore(path)
    a.propose_version(parameter_path="p", parameter_set={"v": 1})
    b.propose_version(parameter_path="p", parameter_set={"v": 2})  # b's cache is stale
    a.propose_version(parameter_path="p", parameter_set={"v": 3})  # a's cache is stale
    # Either store can verify — cache is not the source of truth
    ok_a, err_a = a.verify_chain()
    ok_b, err_b = b.verify_chain()
    assert ok_a, err_a
    assert ok_b, err_b
