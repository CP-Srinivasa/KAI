"""Issue #1000: ein fehlender oder falscher Key meldet 401, nicht 500/400.

Der CI-Lauf hat kein ``litellm`` (ADR 0019: Transport-Runtime getrennt vom
Kern). Die Tests stellen deshalb die beiden Namen, die der Callback braucht,
als minimale Attrappen bereit und pruefen die Entscheidung selbst. Der
Gegenbeweis gegen den echten Transport 1.99.0 steht in der PR (Wegwerf-Instanz
auf der Pi, Port 4099, vorher 500/400, nachher 401).
"""

from __future__ import annotations

import asyncio
import enum
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import HTTPException

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "config" / "kai_litellm_auth_status.py"
CALLBACK = "kai_litellm_auth_status.auth_status_handler"


class ProxyErrorTypes(str, enum.Enum):  # noqa: UP042 - bewusst wie LiteLLM, nicht StrEnum
    """Wie in LiteLLM: str-Enum -- ``str(member)`` ist NICHT der Wert."""

    no_db_connection = "no_db_connection"
    auth_error = "auth_error"
    budget_exceeded = "budget_exceeded"


class ProxyException(Exception):  # noqa: N818 - Name wie in LiteLLM
    def __init__(self, error_type: str, code: int) -> None:
        super().__init__(error_type)
        self.type = ProxyErrorTypes(error_type)
        self.code = code


@pytest.fixture()
def callback(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:
        pass

    custom_logger.CustomLogger = CustomLogger  # type: ignore[attr-defined]
    proxy_server = types.ModuleType("litellm.proxy.proxy_server")
    proxy_server.prisma_client = None  # type: ignore[attr-defined]
    for name, module in {
        "litellm": types.ModuleType("litellm"),
        "litellm.integrations": types.ModuleType("litellm.integrations"),
        "litellm.integrations.custom_logger": custom_logger,
        "litellm.proxy": types.ModuleType("litellm.proxy"),
        "litellm.proxy.proxy_server": proxy_server,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules["litellm.proxy"].proxy_server = proxy_server  # type: ignore[attr-defined]
    spec = importlib.util.spec_from_file_location("kai_litellm_auth_status", MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _raised_in(module_name: str, exc: BaseException) -> BaseException:
    """Die Ausnahme so werfen, als kaeme sie aus ``module_name``."""
    namespace: dict[str, Any] = {"__name__": module_name, "exc": exc}
    exec("def boom():\n    raise exc\n", namespace)  # noqa: S102 - Testattrappe
    try:
        namespace["boom"]()
    except BaseException as caught:  # noqa: BLE001
        return caught
    raise AssertionError("nicht geworfen")


AUTH = "litellm.proxy.auth.user_api_key_auth"


def _hook(module: types.ModuleType, exc: BaseException) -> Any:
    handler = module.auth_status_handler
    return asyncio.run(
        handler.async_post_call_failure_hook(
            request_data={}, original_exception=exc, user_api_key_dict=object()
        )
    )


def test_missing_key_without_database_becomes_401(callback: types.ModuleType) -> None:
    result = _hook(callback, _raised_in(AUTH, Exception("No api key passed in.")))
    assert isinstance(result, HTTPException)
    assert result.status_code == 401
    assert "Bearer" in str(result.detail)


def test_wrong_key_no_db_lookup_becomes_401(callback: types.ModuleType) -> None:
    exc = _raised_in(AUTH, ProxyException("no_db_connection", 400))
    result = _hook(callback, exc)
    assert isinstance(result, HTTPException) and result.status_code == 401


def test_model_call_failures_are_left_alone(callback: types.ModuleType) -> None:
    exc = _raised_in("litellm.main", Exception("upstream 502"))
    assert _hook(callback, exc) is None


@pytest.mark.parametrize("error_type", ["auth_error", "budget_exceeded"])
def test_correct_proxy_errors_keep_their_status(
    callback: types.ModuleType, error_type: str
) -> None:
    assert _hook(callback, _raised_in(AUTH, ProxyException(error_type, 429))) is None


def test_http_exceptions_are_left_alone(callback: types.ModuleType) -> None:
    exc = _raised_in(AUTH, HTTPException(status_code=403, detail="forbidden"))
    assert _hook(callback, exc) is None


def test_with_a_database_upstream_semantics_stay(callback: types.ModuleType) -> None:
    sys.modules["litellm.proxy.proxy_server"].prisma_client = object()  # type: ignore[attr-defined]
    assert _hook(callback, _raised_in(AUTH, Exception("No api key passed in."))) is None


@pytest.mark.parametrize("name", ["litellm.yaml", "litellm_dev.yaml"])
def test_both_proxies_register_the_callback_next_to_their_config(name: str) -> None:
    config = yaml.safe_load((REPO / "config" / name).read_text(encoding="utf-8"))
    assert CALLBACK in config["litellm_settings"]["callbacks"]
    # LiteLLM loest den Modulnamen relativ zum Verzeichnis der Konfiguration auf.
    assert (REPO / "config" / (CALLBACK.split(".")[0] + ".py")).is_file()
