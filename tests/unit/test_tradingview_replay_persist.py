"""D-189 / NEO-F-META-20260424-026 — PersistentReplayCache tests.

Covers the SQLite-backed replay cache that survives process restarts:
  (a) fresh cache starts empty and persists an accepted key,
  (b) a new instance pointing at the same DB rejects the replay,
  (c) within-window rows hydrate back into the in-memory LRU on restart,
  (d) out-of-window rows are pruned at hydrate time,
  (e) fail-open: a persistence error does not break in-memory semantics,
  (f) max_size enforced across hydrate + runtime.

No router wiring — the class is tested in isolation via its public contract.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from app.api.routers.tradingview import PersistentReplayCache, ReplayCache


def _cache(tmp_path: Path, **kwargs: object) -> PersistentReplayCache:
    """Factory with sensible test defaults."""
    return PersistentReplayCache(
        max_size=int(kwargs.pop("max_size", 64)),
        window_seconds=float(kwargs.pop("window_seconds", 300.0)),
        db_path=tmp_path / "replay.db",
        table_name=str(kwargs.pop("table_name", "payload_seen")),
    )


def test_rejects_unsafe_table_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe table name"):
        PersistentReplayCache(
            max_size=10,
            window_seconds=60.0,
            db_path=tmp_path / "replay.db",
            table_name="payload; DROP TABLE foo",
        )


def test_first_accept_persists(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    assert cache.check_and_record("hash_a") is True

    with sqlite3.connect(tmp_path / "replay.db") as conn:
        rows = conn.execute("SELECT key FROM payload_seen").fetchall()
    assert rows == [("hash_a",)]


def test_replay_survives_reload(tmp_path: Path) -> None:
    """Simulate a process restart: new instance on same DB rejects replay."""
    cache_a = _cache(tmp_path)
    assert cache_a.check_and_record("hash_x") is True

    # Drop the first instance entirely — imagine uvicorn restarted.
    del cache_a
    cache_b = _cache(tmp_path)

    # Same key now within the window → REPLAY (guard holds across restart).
    assert cache_b.check_and_record("hash_x") is False
    # Fresh key still accepted.
    assert cache_b.check_and_record("hash_y") is True


def test_hydrate_prunes_expired_rows(tmp_path: Path) -> None:
    """Rows outside the window are deleted at hydrate time."""
    db_path = tmp_path / "replay.db"
    # Seed the DB directly with one expired row and one fresh row.
    now = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE payload_seen (key TEXT PRIMARY KEY, inserted_at REAL NOT NULL)")
        conn.execute(
            "INSERT INTO payload_seen (key, inserted_at) VALUES (?, ?)",
            ("stale", now - 10_000.0),  # 10_000 s ago, well outside 300 s window
        )
        conn.execute(
            "INSERT INTO payload_seen (key, inserted_at) VALUES (?, ?)",
            ("fresh", now - 10.0),
        )
        conn.commit()

    cache = _cache(tmp_path, window_seconds=300.0)

    # Stale entry: pruned, so a re-submit is treated as new.
    assert cache.check_and_record("stale") is True
    # Fresh entry: still remembered, so a re-submit is rejected as replay.
    assert cache.check_and_record("fresh") is False


def test_max_size_enforced_on_hydrate(tmp_path: Path) -> None:
    """Too many within-window rows get evicted to respect max_size."""
    db_path = tmp_path / "replay.db"
    now = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE payload_seen (key TEXT PRIMARY KEY, inserted_at REAL NOT NULL)")
        for i in range(10):
            conn.execute(
                "INSERT INTO payload_seen (key, inserted_at) VALUES (?, ?)",
                (f"k{i}", now - float(10 - i)),  # oldest first
            )
        conn.commit()

    cache = _cache(tmp_path, max_size=3, window_seconds=300.0)

    # Expect only the 3 most recent keys (k7, k8, k9) survive in memory.
    # Older ones are evicted → re-submit counts as a fresh accept.
    assert cache.check_and_record("k0") is True  # evicted, treated as new
    assert cache.check_and_record("k9") is False  # kept, replay rejected


def test_persistence_failure_is_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken sqlite layer should not crash the request path.

    Uses monkey-patch on the module-level ``sqlite3.connect`` used by the
    cache so we simulate a DB outage without depending on filesystem quirks
    (Windows holds file handles differently than Linux). The in-memory guard
    must still accept the first request and reject the replay.
    """
    cache = _cache(tmp_path)

    # Poison sqlite3.connect *in the tradingview module* after the initial
    # connection during __init__ already succeeded — any subsequent write
    # from check_and_record will blow up and must be swallowed.
    from app.api.routers import tradingview as tv_router

    def _boom(*args: object, **kwargs: object) -> object:
        raise sqlite3.OperationalError("simulated db outage")

    monkeypatch.setattr(tv_router.sqlite3, "connect", _boom)

    # In-memory path still works despite the poisoned backend.
    assert cache.check_and_record("hash_im") is True
    assert cache.check_and_record("hash_im") is False  # replay rejected


def test_clear_wipes_sqlite_and_memory(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.check_and_record("hash_c")
    cache.clear()

    with sqlite3.connect(tmp_path / "replay.db") as conn:
        rows = conn.execute("SELECT key FROM payload_seen").fetchall()
    assert rows == []
    # Same key after clear → treated as new.
    assert cache.check_and_record("hash_c") is True


def test_in_memory_backward_compat(tmp_path: Path) -> None:
    """Legacy ReplayCache (in-memory only) is unchanged — sanity check."""
    legacy = ReplayCache(max_size=8, window_seconds=60.0)
    assert legacy.check_and_record("a") is True
    assert legacy.check_and_record("a") is False
