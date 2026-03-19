from fastapi.testclient import TestClient

from app.api import main as api_main
from app.api.main import app
from app.core.settings import AppSettings


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_app_lifespan_starts_and_stops_rss_scheduler(monkeypatch) -> None:
    events: list[tuple[str, object | bool | None]] = []

    class FakeRSSScheduler:
        def __init__(self, session_factory, persist_result=None) -> None:
            self.session_factory = session_factory
            self.persist_result = persist_result
            events.append(("init", session_factory))
            events.append(("persist_callback", callable(persist_result)))

        def start(self) -> None:
            events.append(("start", None))

        def stop(self) -> None:
            events.append(("stop", None))

    monkeypatch.setattr(api_main, "get_settings", lambda: AppSettings())
    monkeypatch.setattr(api_main, "configure_logging", lambda _level: None)
    monkeypatch.setattr(api_main, "validate_secrets", lambda _settings: None)
    monkeypatch.setattr(api_main, "setup_auth", lambda _app, _api_key: None)
    monkeypatch.setattr(api_main, "build_session_factory", lambda _db: "session-factory")
    monkeypatch.setattr(api_main, "RSSScheduler", FakeRSSScheduler)

    test_app = api_main.create_app()

    with TestClient(test_app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert test_app.state.session_factory == "session-factory"
        assert test_app.state.rss_scheduler.session_factory == "session-factory"
        assert callable(test_app.state.rss_scheduler.persist_result)

    assert events == [
        ("init", "session-factory"),
        ("persist_callback", True),
        ("start", None),
        ("stop", None),
    ]
