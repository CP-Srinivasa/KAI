from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "freshness_check.py"
    spec = importlib.util.spec_from_file_location("freshness_check", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


freshness = _load_module()


def _client(response: httpx.Response) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    return httpx.Client(
        base_url="https://kai-trader.org",
        transport=httpx.MockTransport(handler),
    )


def test_health_probe_detects_cloudflare_access_login_even_with_http_200() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text="<html><head><title>Sign in ・ Cloudflare Access</title></head></html>",
    )
    result = freshness.probe_one(
        _client(response),
        freshness.Probe("health", "/health", None, 0, 0),
        datetime.now(UTC),
    )

    assert result.state == "down"
    assert result.note == "cloudflare_access_login"


def test_json_probe_reports_cloudflare_access_login_as_non_json_root_cause() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        text="<html><body>Cloudflare Access sign in</body></html>",
    )
    result = freshness.probe_one(
        _client(response),
        freshness.Probe("dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600),
        datetime.now(UTC),
    )

    assert result.state == "down"
    assert result.note == "cloudflare_access_login"


def test_json_probe_reports_unexpected_content_type_for_other_html() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text="<html><body>not the API</body></html>",
    )
    result = freshness.probe_one(
        _client(response),
        freshness.Probe("dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600),
        datetime.now(UTC),
    )

    assert result.state == "down"
    assert result.note == "non-json body (text/html)"


def test_cf_access_headers_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("KAI_FRESHNESS_CF_ACCESS_CLIENT_ID", "id.example")
    monkeypatch.setenv("KAI_FRESHNESS_CF_ACCESS_CLIENT_SECRET", "secret")

    assert freshness._read_cf_access_headers() == {
        "CF-Access-Client-Id": "id.example",
        "CF-Access-Client-Secret": "secret",
    }
