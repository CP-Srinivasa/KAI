"""Ein Fehlschlag darf nicht sofort wieder in denselben teuren Pfad laufen.

``_load_source_by_doc`` parst das komplette Alert-Audit und fragt danach die DB
ab; das Ergebnis ist 300 s lang gueltig. Der except-Zweig liess ``at`` aber
unberuehrt — nach einem Fehler war der Cache also nie frisch, und der teure Block
lief bei JEDEM Aufruf erneut. Ein Retry-Sturm genau dann, wenn die DB ohnehin
klemmt, ausgeloest von jedem Dashboard-Refresh.

Dazu holte der Pfad seine Session-Fabrik ueber ``build_session_factory``, das
``create_async_engine`` ruft: eine neue Engine mit eigenem Pool pro Aufruf, nie
disposed.
"""

from __future__ import annotations

import pytest

from app.api.routers import dashboard as dash


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    dash._source_map_cache["map"] = None
    dash._source_map_cache["at"] = 0.0
    yield
    dash._source_map_cache["map"] = None
    dash._source_map_cache["at"] = 0.0


def _fail_stream(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("DB weg")


@pytest.mark.asyncio
async def test_a_failure_is_not_retried_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def counting(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        _fail_stream()

    monkeypatch.setattr(dash, "_validate_dashboard_stream", counting)

    clock = {"t": 1000.0}
    monkeypatch.setattr(dash.time, "monotonic", lambda: clock["t"])

    assert await dash._load_source_by_doc() == {}
    assert calls["n"] == 1

    # Direkt danach und kurz darauf: KEIN erneuter teurer Durchlauf.
    clock["t"] += 1.0
    assert await dash._load_source_by_doc() == {}
    clock["t"] += 20.0
    assert await dash._load_source_by_doc() == {}
    assert calls["n"] == 1, f"Retry-Sturm: {calls['n']} teure Durchlaeufe statt 1"


@pytest.mark.asyncio
async def test_after_the_backoff_it_tries_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Backoff darf nicht zur Dauersperre werden."""
    calls = {"n": 0}

    def counting(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        _fail_stream()

    monkeypatch.setattr(dash, "_validate_dashboard_stream", counting)

    clock = {"t": 1000.0}
    monkeypatch.setattr(dash.time, "monotonic", lambda: clock["t"])

    await dash._load_source_by_doc()
    assert calls["n"] == 1

    clock["t"] += dash._SOURCE_MAP_ERROR_BACKOFF_S + 1.0
    await dash._load_source_by_doc()
    assert calls["n"] == 2, "nach dem Backoff muss ein neuer Versuch erfolgen"


@pytest.mark.asyncio
async def test_a_previous_good_map_survives_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Fehlschlag darf einen bereits geholten Stand nicht loeschen."""
    clock = {"t": 5000.0}
    monkeypatch.setattr(dash.time, "monotonic", lambda: clock["t"])

    dash._source_map_cache["map"] = {"doc-1": "cointelegraph"}
    dash._source_map_cache["at"] = clock["t"] - dash._SOURCE_MAP_TTL_S - 1.0  # abgelaufen

    monkeypatch.setattr(dash, "_validate_dashboard_stream", _fail_stream)

    assert await dash._load_source_by_doc() == {"doc-1": "cointelegraph"}
    # und der Stand bleibt fuer den Backoff erhalten, statt erneut zu scheitern
    clock["t"] += 1.0
    assert await dash._load_source_by_doc() == {"doc-1": "cointelegraph"}


def test_the_loader_uses_the_shared_session_factory() -> None:
    """Kein ``build_session_factory`` mehr in diesem Pfad — sonst eine Engine je Aufruf."""
    import inspect

    # Kommentare ausblenden: der Code ERKLAERT die Umstellung und nennt dabei den
    # alten Namen. Geprueft wird, was ausgefuehrt wird, nicht was danebensteht.
    code = "\n".join(
        line.split("#", 1)[0] for line in inspect.getsource(dash._load_source_by_doc).splitlines()
    )
    assert "get_shared_session_factory" in code
    assert "build_session_factory" not in code
