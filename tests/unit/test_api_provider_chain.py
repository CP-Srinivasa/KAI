"""NEO-P-003: the server path uses the same provider chain as the CLI path.

Before this, ``app/api/main.py`` built a SINGLE provider from
``settings.pipeline_provider`` (code default "openai") while the CLI/cron path
built ``EnsembleProvider([openai, gemini(, grok)])``. Two entry points, two
chains — an analysis result was not reproducible without knowing which door it
came through.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_main
from app.core.settings import AppSettings


class _FakeRSSScheduler:
    def __init__(self, session_factory: Any, **kwargs: Any) -> None:
        self.provider = kwargs.get("provider")

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


def _harness(monkeypatch: pytest.MonkeyPatch, settings: AppSettings) -> None:
    settings.operator.telegram_polling_enabled = False
    settings.operator.telegram_bot_token = ""
    settings.operator.admin_chat_ids = ""
    settings.providers.openai_api_key = ""
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "configure_logging", lambda _level: None)
    monkeypatch.setattr(api_main, "validate_secrets", lambda _settings: None)
    monkeypatch.setattr(api_main, "setup_auth", lambda *_a, **_kw: None)
    monkeypatch.setattr(api_main, "build_session_factory", lambda _db: "session-factory")
    monkeypatch.setattr(api_main, "RSSScheduler", _FakeRSSScheduler)
    monkeypatch.setattr(
        api_main,
        "KeywordEngine",
        type("FakeKE", (), {"from_monitor_dir": staticmethod(lambda _path: "fake-ke")}),
    )


def test_server_builds_the_primary_chain_when_no_override_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    settings = AppSettings(_env_file=None)
    settings.model_fields_set.discard("pipeline_provider")
    _harness(monkeypatch, settings)
    monkeypatch.setattr(
        api_main, "create_provider", lambda _p, _s: calls.append("single") or "single-provider"
    )
    monkeypatch.setattr(
        api_main, "create_primary_provider", lambda: calls.append("chain") or "chain-provider"
    )

    test_app = api_main.create_app()
    with TestClient(test_app) as client:
        assert client.get("/health").status_code == 200
        assert test_app.state.rss_scheduler.provider == "chain-provider"

    assert calls == ["chain"]


def test_explicit_pipeline_provider_still_overrides_the_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    settings = AppSettings(_env_file=None)
    settings.pipeline_provider = "anthropic"
    settings.model_fields_set.add("pipeline_provider")
    _harness(monkeypatch, settings)
    monkeypatch.setattr(
        api_main,
        "create_provider",
        lambda name, _s: calls.append(f"single:{name}") or "single-provider",
    )
    monkeypatch.setattr(
        api_main, "create_primary_provider", lambda: calls.append("chain") or "chain-provider"
    )

    test_app = api_main.create_app()
    with TestClient(test_app) as client:
        assert client.get("/health").status_code == 200
        assert test_app.state.rss_scheduler.provider == "single-provider"

    assert calls == ["single:anthropic"]


def test_explicit_empty_override_disables_the_llm_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    settings = AppSettings(_env_file=None)
    settings.pipeline_provider = ""
    settings.model_fields_set.add("pipeline_provider")
    _harness(monkeypatch, settings)
    monkeypatch.setattr(
        api_main, "create_provider", lambda _p, _s: calls.append("single") or "single-provider"
    )
    monkeypatch.setattr(
        api_main, "create_primary_provider", lambda: calls.append("chain") or "chain-provider"
    )

    test_app = api_main.create_app()
    with TestClient(test_app) as client:
        assert client.get("/health").status_code == 200
        assert test_app.state.rss_scheduler.provider is None

    assert calls == []


def test_no_shadow_provider_is_wired_into_the_server_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cost guard: a shadow call per document would double Anthropic spend."""
    settings = AppSettings(_env_file=None)
    settings.model_fields_set.discard("pipeline_provider")
    _harness(monkeypatch, settings)
    monkeypatch.setattr(api_main, "create_provider", lambda _p, _s: None)
    monkeypatch.setattr(api_main, "create_primary_provider", lambda: "chain-provider")

    captured: dict[str, Any] = {}

    class _Capturing(_FakeRSSScheduler):
        def __init__(self, session_factory: Any, **kwargs: Any) -> None:
            super().__init__(session_factory, **kwargs)
            captured.update(kwargs)

    monkeypatch.setattr(api_main, "RSSScheduler", _Capturing)

    test_app = api_main.create_app()
    with TestClient(test_app) as client:
        assert client.get("/health").status_code == 200

    assert "shadow_provider" not in captured
    assert captured.get("provider") == "chain-provider"


def test_alias_still_resolves_to_the_renamed_factory_function() -> None:
    from app.analysis.factory import create_cli_primary_provider, create_primary_provider

    assert create_cli_primary_provider is create_primary_provider
