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
        def __init__(self, session_factory, *, interval_minutes=15,
                     keyword_engine=None, provider=None, **kwargs) -> None:
            self.session_factory = session_factory
            self.keyword_engine = keyword_engine
            self.provider = provider
            events.append(("init", session_factory))
            events.append(("keyword_engine", keyword_engine is not None))

        def start(self) -> None:
            events.append(("start", None))

        def stop(self) -> None:
            events.append(("stop", None))

    settings = AppSettings(_env_file=None)
    settings.operator.telegram_polling_enabled = False
    settings.operator.telegram_dry_run = True
    settings.operator.telegram_bot_token = ""
    settings.operator.admin_chat_ids = ""
    settings.providers.openai_api_key = ""
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "configure_logging", lambda _level: None)
    monkeypatch.setattr(api_main, "validate_secrets", lambda _settings: None)
    monkeypatch.setattr(api_main, "setup_auth", lambda _app, _api_key, _env="development": None)
    monkeypatch.setattr(api_main, "build_session_factory", lambda _db: "session-factory")
    monkeypatch.setattr(api_main, "RSSScheduler", FakeRSSScheduler)
    monkeypatch.setattr(api_main, "KeywordEngine", type("FakeKE", (), {
        "from_monitor_dir": staticmethod(lambda _path: "fake-keyword-engine"),
    }))
    monkeypatch.setattr(api_main, "create_provider", lambda _p, _s: None)

    test_app = api_main.create_app()

    with TestClient(test_app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert test_app.state.session_factory == "session-factory"
        assert test_app.state.rss_scheduler.session_factory == "session-factory"
        assert test_app.state.rss_scheduler.keyword_engine is not None

    assert events == [
        ("init", "session-factory"),
        ("keyword_engine", True),
        ("start", None),
        ("stop", None),
    ]


def test_app_with_api_key_starts_and_auth_middleware_is_active(monkeypatch) -> None:
    class FakeRSSScheduler:
        def __init__(self, session_factory, **kwargs) -> None:
            self.session_factory = session_factory

        def start(self) -> None:
            return

        def stop(self) -> None:
            return

    settings = AppSettings(_env_file=None)
    settings.api_key = "s46d-live-check-key"
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "configure_logging", lambda _level: None)
    monkeypatch.setattr(api_main, "validate_secrets", lambda _settings: None)
    monkeypatch.setattr(api_main, "build_session_factory", lambda _db: "session-factory")
    monkeypatch.setattr(api_main, "RSSScheduler", FakeRSSScheduler)
    monkeypatch.setattr(api_main, "KeywordEngine", type("FakeKE", (), {
        "from_monitor_dir": staticmethod(lambda _path: "fake-keyword-engine"),
    }))
    monkeypatch.setattr(api_main, "create_provider", lambda _p, _s: None)

    test_app = api_main.create_app()

    with TestClient(test_app) as client:
        unauth_response = client.get("/docs")
        assert unauth_response.status_code == 401

        auth_response = client.get(
            "/docs",
            headers={"Authorization": "Bearer s46d-live-check-key"},
        )
        assert auth_response.status_code == 200


def test_app_lifespan_telegram_poller_is_fail_closed_by_default(monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    class FakeRSSScheduler:
        def __init__(self, session_factory, **kwargs) -> None:
            self.session_factory = session_factory

        def start(self) -> None:
            events.append(("rss_start", None))

        def stop(self) -> None:
            events.append(("rss_stop", None))

    class FakeTelegramOperatorBot:
        def __init__(self, *, bot_token: str, admin_chat_ids: list[int],
                     audit_log_path: str, dry_run: bool,
                     text_processor: object | None,
                     voice_transcriber: object | None = None,
                     signal_handoff_log_path: str = "",
                     signal_exchange_outbox_log_path: str = "",
                     signal_append_decision_enabled: bool = False,
                     signal_auto_run_enabled: bool = False,
                     signal_auto_run_mode: str = "paper",
                     signal_auto_run_provider: str = "coingecko",
                     signal_forward_to_exchange_enabled: bool = False,
                     signal_exchange_sent_log_path: str = "",
                     signal_exchange_dead_letter_log_path: str = "",
                     dashboard_url: str = "",
                     context_provider: object | None = None) -> None:
            self.is_configured = bool(bot_token) and bool(admin_chat_ids)
            events.append(("bot_dry_run", dry_run))
            events.append(("bot_audit_log", audit_log_path))
            events.append(("bot_has_text_processor", text_processor is not None))
            events.append(("bot_has_voice_transcriber", voice_transcriber is not None))
            events.append(("signal_handoff_log_path", signal_handoff_log_path))
            events.append(("signal_exchange_outbox_log_path", signal_exchange_outbox_log_path))
            events.append(("signal_append_decision_enabled", signal_append_decision_enabled))
            events.append(("signal_auto_run_enabled", signal_auto_run_enabled))
            events.append(("signal_auto_run_mode", signal_auto_run_mode))
            events.append(("signal_auto_run_provider", signal_auto_run_provider))
            events.append(
                ("signal_forward_to_exchange_enabled", signal_forward_to_exchange_enabled)
            )
            events.append(("signal_exchange_sent_log_path", signal_exchange_sent_log_path))
            events.append(
                ("signal_exchange_dead_letter_log_path", signal_exchange_dead_letter_log_path)
            )

    class FakeTelegramPoller:
        def __init__(self, bot: object, poll_interval: float, long_poll_timeout: int) -> None:
            events.append(("poller_init", (poll_interval, long_poll_timeout)))

        def start(self) -> None:
            events.append(("poller_start", None))

        def stop(self) -> None:
            events.append(("poller_stop", None))

    settings = AppSettings(_env_file=None)
    settings.operator.telegram_polling_enabled = False
    settings.operator.telegram_dry_run = True
    settings.operator.telegram_bot_token = ""
    settings.operator.admin_chat_ids = ""
    settings.operator.signal_append_decision_enabled = False
    settings.operator.signal_auto_run_enabled = False
    settings.operator.signal_forward_to_exchange_enabled = False
    settings.providers.openai_api_key = ""
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "configure_logging", lambda _level: None)
    monkeypatch.setattr(api_main, "validate_secrets", lambda _settings: None)
    monkeypatch.setattr(api_main, "setup_auth", lambda _app, _api_key, _env="development": None)
    monkeypatch.setattr(api_main, "build_session_factory", lambda _db: "session-factory")
    monkeypatch.setattr(api_main, "RSSScheduler", FakeRSSScheduler)
    monkeypatch.setattr(api_main, "TelegramOperatorBot", FakeTelegramOperatorBot)
    monkeypatch.setattr(api_main, "TelegramPoller", FakeTelegramPoller)
    monkeypatch.setattr(
        api_main,
        "KeywordEngine",
        type("FakeKE", (), {"from_monitor_dir": staticmethod(lambda _path: "fake-keyword-engine")}),
    )
    monkeypatch.setattr(api_main, "create_provider", lambda _p, _s: None)
    monkeypatch.setattr(api_main, "VoiceTranscriber", lambda **kw: "fake-voice-transcriber")
    monkeypatch.setattr(api_main, "make_context_provider", lambda _sf: None)

    test_app = api_main.create_app()
    with TestClient(test_app) as client:
        assert client.get("/health").status_code == 200

    assert ("bot_dry_run", True) in events
    assert ("poller_init", (1.0, 20)) in events
    assert ("poller_start", None) not in events
    assert ("poller_stop", None) in events
    assert ("signal_append_decision_enabled", False) in events
    assert ("signal_auto_run_enabled", False) in events
    assert ("signal_forward_to_exchange_enabled", False) in events


def test_app_lifespan_telegram_poller_starts_when_enabled(monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    class FakeRSSScheduler:
        def __init__(self, session_factory, **kwargs) -> None:
            self.session_factory = session_factory

        def start(self) -> None:
            events.append(("rss_start", None))

        def stop(self) -> None:
            events.append(("rss_stop", None))

    class FakeTelegramOperatorBot:
        def __init__(self, *, bot_token: str, admin_chat_ids: list[int],
                     audit_log_path: str, dry_run: bool,
                     text_processor: object | None,
                     voice_transcriber: object | None = None,
                     signal_handoff_log_path: str = "",
                     signal_exchange_outbox_log_path: str = "",
                     signal_append_decision_enabled: bool = False,
                     signal_auto_run_enabled: bool = False,
                     signal_auto_run_mode: str = "paper",
                     signal_auto_run_provider: str = "coingecko",
                     signal_forward_to_exchange_enabled: bool = False,
                     signal_exchange_sent_log_path: str = "",
                     signal_exchange_dead_letter_log_path: str = "",
                     dashboard_url: str = "",
                     context_provider: object | None = None) -> None:
            self.is_configured = bool(bot_token) and bool(admin_chat_ids)
            events.append(("bot_token", bot_token))
            events.append(("bot_admin_ids", admin_chat_ids))
            events.append(("bot_dry_run", dry_run))
            events.append(("bot_audit_log", audit_log_path))
            events.append(("bot_has_text_processor", text_processor is not None))
            events.append(("bot_has_voice_transcriber", voice_transcriber is not None))
            events.append(("signal_handoff_log_path", signal_handoff_log_path))
            events.append(("signal_exchange_outbox_log_path", signal_exchange_outbox_log_path))
            events.append(("signal_append_decision_enabled", signal_append_decision_enabled))
            events.append(("signal_auto_run_enabled", signal_auto_run_enabled))
            events.append(("signal_auto_run_mode", signal_auto_run_mode))
            events.append(("signal_auto_run_provider", signal_auto_run_provider))
            events.append(
                ("signal_forward_to_exchange_enabled", signal_forward_to_exchange_enabled)
            )
            events.append(("signal_exchange_sent_log_path", signal_exchange_sent_log_path))
            events.append(
                ("signal_exchange_dead_letter_log_path", signal_exchange_dead_letter_log_path)
            )

    class FakeTelegramPoller:
        def __init__(self, bot: object, poll_interval: float, long_poll_timeout: int) -> None:
            events.append(("poller_init", (poll_interval, long_poll_timeout)))

        def start(self) -> None:
            events.append(("poller_start", None))

        def stop(self) -> None:
            events.append(("poller_stop", None))

    settings = AppSettings()
    settings.operator.telegram_polling_enabled = True
    settings.operator.telegram_dry_run = False
    settings.operator.telegram_poll_interval_seconds = 2.5
    settings.operator.telegram_long_poll_timeout_seconds = 30
    settings.operator.telegram_bot_token = "test-token"
    settings.operator.admin_chat_ids = "111,222"
    settings.operator.command_audit_log = "artifacts/custom_operator_commands.jsonl"
    settings.operator.signal_handoff_log = "artifacts/custom_signal_handoff.jsonl"
    settings.operator.signal_exchange_outbox_log = "artifacts/custom_exchange_outbox.jsonl"
    settings.operator.signal_append_decision_enabled = True
    settings.operator.signal_auto_run_enabled = True
    settings.operator.signal_auto_run_mode = "shadow"
    settings.operator.signal_auto_run_provider = "mock"
    settings.operator.signal_forward_to_exchange_enabled = True
    settings.operator.signal_exchange_sent_log = "artifacts/custom_exchange_sent.jsonl"
    settings.operator.signal_exchange_dead_letter_log = "artifacts/custom_exchange_dead.jsonl"

    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "configure_logging", lambda _level: None)
    monkeypatch.setattr(api_main, "validate_secrets", lambda _settings: None)
    monkeypatch.setattr(api_main, "setup_auth", lambda _app, _api_key, _env="development": None)
    monkeypatch.setattr(api_main, "build_session_factory", lambda _db: "session-factory")
    monkeypatch.setattr(api_main, "RSSScheduler", FakeRSSScheduler)
    monkeypatch.setattr(api_main, "TelegramOperatorBot", FakeTelegramOperatorBot)
    monkeypatch.setattr(api_main, "TelegramPoller", FakeTelegramPoller)
    monkeypatch.setattr(
        api_main,
        "KeywordEngine",
        type("FakeKE", (), {"from_monitor_dir": staticmethod(lambda _path: "fake-keyword-engine")}),
    )
    monkeypatch.setattr(api_main, "create_provider", lambda _p, _s: None)
    monkeypatch.setattr(api_main, "VoiceTranscriber", lambda **kw: "fake-voice-transcriber")
    monkeypatch.setattr(api_main, "make_context_provider", lambda _sf: None)

    test_app = api_main.create_app()
    with TestClient(test_app) as client:
        assert client.get("/health").status_code == 200

    assert ("bot_token", "test-token") in events
    assert ("bot_admin_ids", [111, 222]) in events
    assert ("bot_dry_run", False) in events
    assert ("bot_audit_log", "artifacts/custom_operator_commands.jsonl") in events
    assert ("signal_handoff_log_path", "artifacts/custom_signal_handoff.jsonl") in events
    assert ("signal_exchange_outbox_log_path", "artifacts/custom_exchange_outbox.jsonl") in events
    assert ("signal_append_decision_enabled", True) in events
    assert ("signal_auto_run_enabled", True) in events
    assert ("signal_auto_run_mode", "shadow") in events
    assert ("signal_auto_run_provider", "mock") in events
    assert ("signal_forward_to_exchange_enabled", True) in events
    assert ("signal_exchange_sent_log_path", "artifacts/custom_exchange_sent.jsonl") in events
    assert (
        "signal_exchange_dead_letter_log_path",
        "artifacts/custom_exchange_dead.jsonl",
    ) in events
    assert ("poller_init", (2.5, 30)) in events
    assert ("poller_start", None) in events
    assert ("poller_stop", None) in events
