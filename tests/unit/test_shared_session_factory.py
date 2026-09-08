"""Wiederkehrende Aufrufer duerfen nicht jedes Mal eine neue AsyncEngine bauen.

Hintergrund (2026-09-08): ``build_trading_loop`` rief ``build_session_factory``
aus dem Position-Monitor-Tick — 60-s-Takt, 1440 Engines pro Tag, jede mit eigenem
Pool, keine je ``dispose()``-t. Der Event-Loop-max-Lag desselben Prozesses stieg
von 6,3 s bei ~600 s Uptime auf 12,4 s bei 1152 s.
"""

from __future__ import annotations

import pytest

from app.core.settings import DBSettings
from app.storage.db import session as session_mod


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    session_mod.reset_shared_session_factories_for_tests()
    yield
    session_mod.reset_shared_session_factories_for_tests()


# Bewusst DATEI-URLs, nicht ":memory:": SQLite-in-memory laeuft auf einem
# StaticPool, der `pool_size`/`max_overflow` nicht annimmt — `build_engine`
# uebergibt beide immer. Der Test wuerde dann an der URL-Wahl scheitern statt
# am Verhalten, das er pruefen soll. Eine Engine oeffnet die Datei erst beim
# ersten Connect, es wird also nichts angelegt.
_URL_A = "sqlite+aiosqlite:///./_shared_factory_test_a.db"
_URL_B = "sqlite+aiosqlite:///./_shared_factory_test_b.db"


def _db(url: str = _URL_A, **kw: object) -> DBSettings:
    return DBSettings(url=url, **kw)  # type: ignore[arg-type]


def test_repeated_calls_share_one_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    real = session_mod.build_engine

    def counting(settings: DBSettings):
        calls["n"] += 1
        return real(settings)

    monkeypatch.setattr(session_mod, "build_engine", counting)

    settings = _db()
    first = session_mod.get_shared_session_factory(settings)
    for _ in range(20):  # entspricht 20 Position-Monitor-Ticks
        assert session_mod.get_shared_session_factory(_db()) is first

    assert calls["n"] == 1, f"erwartet genau eine Engine, gebaut wurden {calls['n']}"


# --- Gegenproben: der Cache darf NICHT ueber Identitaetsgrenzen hinweg teilen ---


def test_different_url_gets_its_own_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    real = session_mod.build_engine

    def counting(settings: DBSettings):
        calls["n"] += 1
        return real(settings)

    monkeypatch.setattr(session_mod, "build_engine", counting)

    a = session_mod.get_shared_session_factory(_db(_URL_A))
    b = session_mod.get_shared_session_factory(_db(_URL_B))

    assert a is not b
    assert calls["n"] == 2


def test_different_pool_size_gets_its_own_engine() -> None:
    """Gleiche URL, andere Pool-Grenzen — sonst erbt einer still die Limits des anderen."""
    a = session_mod.get_shared_session_factory(_db(pool_size=5))
    b = session_mod.get_shared_session_factory(_db(pool_size=9))
    assert a is not b


def test_build_session_factory_stays_unshared() -> None:
    """Die alte Funktion bleibt isolierend — Tests und CLI verlassen sich darauf."""
    a = session_mod.build_session_factory(_db())
    b = session_mod.build_session_factory(_db())
    assert a is not b
