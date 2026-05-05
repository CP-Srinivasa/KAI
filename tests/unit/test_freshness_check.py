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


def _client_with_request_echo(response: httpx.Response) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        response.request = request
        return response

    return httpx.Client(
        base_url="https://kai-trader.org",
        headers={
            "CF-Access-Client-Id": "id.example",
            "CF-Access-Client-Secret": "super-secret-value",
        },
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


def test_json_probe_reports_cloudflare_access_redirect() -> None:
    response = httpx.Response(
        302,
        headers={
            "location": (
                "https://kai-dashboard.cloudflareaccess.com/cdn-cgi/access/login/"
                "kai-trader.org"
            )
        },
    )
    result = freshness.probe_one(
        _client(response),
        freshness.Probe("dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600),
        datetime.now(UTC),
    )

    assert result.http_status == 302
    assert result.state == "down"
    assert result.note == "cloudflare_access_redirect"


def test_legitimate_redirect_is_not_classified_as_cloudflare_access() -> None:
    response = httpx.Response(
        302,
        headers={"location": "https://kai-trader.org/dashboard/api/quality/"},
    )
    result = freshness.probe_one(
        _client(response),
        freshness.Probe("dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600),
        datetime.now(UTC),
    )

    assert result.state == "down"
    assert result.note == "status 302"


def test_bad_or_expired_service_token_has_distinct_non_secret_note() -> None:
    response = httpx.Response(
        302,
        headers={
            "location": (
                "https://kai-dashboard.cloudflareaccess.com/cdn-cgi/access/login/"
                "kai-trader.org"
            )
        },
    )
    result = freshness.probe_one(
        _client_with_request_echo(response),
        freshness.Probe("dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600),
        datetime.now(UTC),
    )

    assert result.state == "down"
    assert result.note == "cloudflare_access_service_token_rejected"
    assert "super-secret-value" not in result.note


def test_bad_service_token_html_login_does_not_leak_secret() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text="<html><head><title>Sign in ・ Cloudflare Access</title></head></html>",
    )
    result = freshness.probe_one(
        _client_with_request_echo(response),
        freshness.Probe("dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600),
        datetime.now(UTC),
    )

    assert result.state == "down"
    assert result.note == "cloudflare_access_service_token_rejected"
    assert "super-secret-value" not in result.note


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


def test_cloudflare_access_401_with_cf_mitigated_header_classified_as_login() -> None:
    # Cloudflare Access can deny with 401 + cf-mitigated=challenge instead of
    # a 302 redirect. Without a service-token header the note is "login".
    response = httpx.Response(
        401,
        headers={"cf-mitigated": "challenge"},
    )
    result = freshness.probe_one(
        _client(response),
        freshness.Probe("dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600),
        datetime.now(UTC),
    )

    assert result.http_status == 401
    assert result.state == "down"
    assert result.note == "cloudflare_access_login"


def test_cloudflare_access_401_with_token_sent_classified_as_rejected() -> None:
    # Same 401-from-edge case, but our request carried a service token —
    # so the operator sees "rejected", not "login required".
    response = httpx.Response(
        401,
        headers={"cf-mitigated": "challenge"},
    )
    result = freshness.probe_one(
        _client_with_request_echo(response),
        freshness.Probe("dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600),
        datetime.now(UTC),
    )

    assert result.http_status == 401
    assert result.state == "down"
    assert result.note == "cloudflare_access_service_token_rejected"
    assert "super-secret-value" not in result.note


def test_generic_401_without_cf_mitigated_falls_back_to_status_note() -> None:
    # A 401 without the cf-mitigated marker is treated as upstream-auth
    # failure (legacy `status 401` note) — we don't claim CF involvement
    # without evidence.
    response = httpx.Response(401)
    result = freshness.probe_one(
        _client(response),
        freshness.Probe("dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600),
        datetime.now(UTC),
    )

    assert result.http_status == 401
    assert result.state == "down"
    assert result.note == "status 401"


def test_cf_access_headers_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("KAI_FRESHNESS_CF_ACCESS_CLIENT_ID", "id.example")
    monkeypatch.setenv("KAI_FRESHNESS_CF_ACCESS_CLIENT_SECRET", "secret")

    assert freshness._read_cf_access_headers() == {
        "CF-Access-Client-Id": "id.example",
        "CF-Access-Client-Secret": "secret",
    }


def test_cf_access_headers_loaded_from_dotenv_file(tmp_path, monkeypatch) -> None:
    # Pi cron loads .env via systemd EnvironmentFile=, but the script also
    # supports falling back to a local .env directly so a developer can probe
    # without injecting env-vars into the shell.
    monkeypatch.delenv("KAI_FRESHNESS_CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("KAI_FRESHNESS_CF_ACCESS_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        'KAI_FRESHNESS_CF_ACCESS_CLIENT_ID="id.from-dotenv"\n'
        "KAI_FRESHNESS_CF_ACCESS_CLIENT_SECRET='secret-from-dotenv'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(freshness, "REPO_ROOT", tmp_path)

    assert freshness._read_cf_access_headers() == {
        "CF-Access-Client-Id": "id.from-dotenv",
        "CF-Access-Client-Secret": "secret-from-dotenv",
    }


def test_cf_access_headers_env_takes_precedence_over_dotenv(tmp_path, monkeypatch) -> None:
    # Operator-override semantics: an explicit env-var must win over .env so a
    # one-off probe (e.g. shell-injected token) is never silently shadowed by
    # a stale on-disk value.
    monkeypatch.setenv("KAI_FRESHNESS_CF_ACCESS_CLIENT_ID", "id.from-env")
    monkeypatch.setenv("KAI_FRESHNESS_CF_ACCESS_CLIENT_SECRET", "secret-from-env")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KAI_FRESHNESS_CF_ACCESS_CLIENT_ID=id.from-dotenv\n"
        "KAI_FRESHNESS_CF_ACCESS_CLIENT_SECRET=secret-from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(freshness, "REPO_ROOT", tmp_path)

    assert freshness._read_cf_access_headers() == {
        "CF-Access-Client-Id": "id.from-env",
        "CF-Access-Client-Secret": "secret-from-env",
    }


def test_service_token_secret_never_leaks_to_outputs(tmp_path, monkeypatch) -> None:
    """Across every failure path probe_one knows about, the CF Access service
    token and Bearer token must not bleed into Result.note, Result.repr, the
    freshness_status.json artifact, or the freshness_check.log line. Guards
    against future regressions where someone formats request headers or
    exception detail into the user-visible note.
    """
    secret = "super-secret-value-that-must-never-leak-987654321"

    monkeypatch.setattr(freshness, "ARTIFACTS", tmp_path / "artifacts")
    monkeypatch.setattr(freshness, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(
        freshness, "STATUS_FILE", tmp_path / "artifacts" / "freshness_status.json"
    )
    monkeypatch.setattr(freshness, "LOG_FILE", tmp_path / "logs" / "freshness_check.log")

    def _client_with_secret(
        response: httpx.Response | None, *, fail: bool = False
    ) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            if fail:
                raise httpx.ConnectError("upstream refused", request=request)
            assert response is not None
            response.request = request
            return response

        return httpx.Client(
            base_url="https://kai-trader.org",
            headers={
                "Authorization": f"Bearer {secret}",
                "CF-Access-Client-Id": "id.example",
                "CF-Access-Client-Secret": secret,
            },
            transport=httpx.MockTransport(handler),
        )

    probe = freshness.Probe(
        "dashboard_quality", "/dashboard/api/quality", "generated_at", 120, 600
    )
    now = datetime.now(UTC)

    pathological = [
        # Cloudflare Access redirect (302 → cloudflareaccess.com)
        httpx.Response(
            302,
            headers={
                "location": "https://x.cloudflareaccess.com/cdn-cgi/access/login/foo"
            },
        ),
        # Cloudflare Access HTML login page (200 + HTML body)
        httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html>Cloudflare Access sign in</html>",
        ),
        # Generic HTML body (non-CF)
        httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html>not the api</html>",
        ),
        # Non-200 upstream status
        httpx.Response(503, text="upstream gone"),
        # 200 + JSON missing the timestamp field
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            text='{"ok": true}',
        ),
        # 200 + JSON with unparseable timestamp
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            text='{"generated_at": "not-a-date"}',
        ),
    ]

    results: list[freshness.Result] = []
    for resp in pathological:
        results.append(
            freshness.probe_one(_client_with_secret(resp), probe, now, scope="external")
        )
    # Exception path (httpx.HTTPError branch)
    results.append(
        freshness.probe_one(
            _client_with_secret(None, fail=True), probe, now, scope="external"
        )
    )

    for r in results:
        assert secret not in (r.note or ""), f"secret leaked into note: {r.note!r}"
        assert secret not in repr(r), f"secret leaked into repr: {repr(r)!r}"

    overall, _ = freshness.overall_from_results(results)
    freshness.write_outputs(results, overall)

    status_text = (tmp_path / "artifacts" / "freshness_status.json").read_text(
        encoding="utf-8"
    )
    log_text = (tmp_path / "logs" / "freshness_check.log").read_text(encoding="utf-8")

    assert secret not in status_text, "secret leaked into freshness_status.json"
    assert secret not in log_text, "secret leaked into freshness_check.log"


def test_external_skip_empty_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("KAI_FRESHNESS_EXTERNAL_SKIP", raising=False)
    monkeypatch.setattr(freshness, "REPO_ROOT", tmp_path)
    assert freshness._read_external_skip() == set()


def test_external_skip_parsed_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "KAI_FRESHNESS_EXTERNAL_SKIP", "dashboard_quality, trading_loop_status"
    )
    monkeypatch.setattr(freshness, "REPO_ROOT", tmp_path)
    assert freshness._read_external_skip() == {
        "dashboard_quality",
        "trading_loop_status",
    }


def test_external_skip_falls_back_to_dotenv(monkeypatch, tmp_path) -> None:
    # Cron / systemd setups that don't pre-export the var can keep the skip
    # list in .env next to the other KAI_FRESHNESS_* keys — same pattern
    # the CF Access headers already follow.
    monkeypatch.delenv("KAI_FRESHNESS_EXTERNAL_SKIP", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KAI_FRESHNESS_EXTERNAL_SKIP=dashboard_quality\n", encoding="utf-8"
    )
    monkeypatch.setattr(freshness, "REPO_ROOT", tmp_path)
    assert freshness._read_external_skip() == {"dashboard_quality"}


def test_overall_goes_crit_when_external_probe_fails_but_internal_is_ok() -> None:
    internal = freshness.Result(
        "dashboard_quality",
        "/dashboard/api/quality",
        200,
        "2026-05-04T00:00:00+00:00",
        1.0,
        "ok",
        scope="internal",
    )
    external = freshness.Result(
        "dashboard_quality",
        "/dashboard/api/quality",
        302,
        None,
        None,
        "down",
        "cloudflare_access_redirect",
        "external",
    )

    assert freshness.overall_from_results([internal, external]) == ("crit", 1)
