"""Unit-Tests für paper_engine_singleton (P1 #7).

Tests verifizieren:
- ``get_paper_engine()`` ist idempotent innerhalb eines Prozesses
- ``reset_paper_engine_cache()`` ermöglicht Test-Isolation
- Konsumenten (Bridge, Reconciler, Premium-Signals) bekommen dieselbe Instance
- Konstruktor-Parameter kommen aus settings.execution (nicht hardcoded)
"""

from __future__ import annotations

import pytest

from app.execution import paper_engine_singleton as pes
from app.execution.paper_engine import PaperExecutionEngine


@pytest.fixture(autouse=True)
def _reset_singleton_cache():
    """Jeder Test startet mit frischem Cache + räumt am Ende auf."""
    pes.reset_paper_engine_cache()
    yield
    pes.reset_paper_engine_cache()


def test_get_paper_engine_returns_paper_execution_engine_instance():
    eng = pes.get_paper_engine()
    assert isinstance(eng, PaperExecutionEngine)


def test_get_paper_engine_is_idempotent_within_process():
    eng_a = pes.get_paper_engine()
    eng_b = pes.get_paper_engine()
    assert eng_a is eng_b, "Singleton muss dieselbe Instance zurückgeben"


def test_reset_paper_engine_cache_yields_new_instance():
    eng_before = pes.get_paper_engine()
    pes.reset_paper_engine_cache()
    eng_after = pes.get_paper_engine()
    assert eng_before is not eng_after


def test_singleton_constructed_from_settings_not_hardcoded(monkeypatch):
    """Verifiziert dass initial_equity aus Settings kommt — nicht 10000.0 hardcoded."""
    from app.core.settings import get_settings

    s = get_settings()
    eng = pes.get_paper_engine()
    # Portfolio initial_equity muss matchen mit settings.execution.paper_initial_equity
    assert eng.portfolio.initial_equity == s.execution.paper_initial_equity


def test_engine_state_persists_across_get_calls():
    """In-Memory-State bleibt zwischen get_paper_engine()-Calls erhalten.

    Das ist die kritische Property: Bridge schreibt Position, Position-Monitor
    sieht sie ohne erneutes rehydrate (innerhalb desselben Ticks). Cross-Tick
    rehydrate bleibt natürlich Pflicht.
    """
    eng = pes.get_paper_engine()
    # Marker direkt am Portfolio setzen (testet object-identity, nicht Persistenz auf disk)
    eng.portfolio.cash = 12345.67
    eng_again = pes.get_paper_engine()
    assert eng_again.portfolio.cash == 12345.67


def test_live_enabled_is_always_false():
    """Singleton MUSS paper-only sein — PaperExecutionEngine selbst raised bei live_enabled=True."""
    eng = pes.get_paper_engine()
    # PaperExecutionEngine speichert live_enabled nicht als public attr; wir prüfen
    # über den Konstruktor-Contract: get_paper_engine() ruft immer live_enabled=False.
    # Wenn das je breaks, schlägt PaperExecutionEngine.__init__ mit ValueError aus.
    assert isinstance(eng, PaperExecutionEngine)
