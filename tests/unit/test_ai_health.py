"""Tests for the telemetry-derived provider health snapshot (NEO-P-005)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.health import ai_health_snapshot
from app.api.routers.health import router as health_router


def _settings(
    *,
    openai: str = "sk-x",
    gemini: str = "gk-x",
    anthropic: str = "ak-x",
    xai: str = "",
    xai_enabled: bool = False,
) -> Any:
    return SimpleNamespace(
        providers=SimpleNamespace(
            openai_api_key=openai,
            gemini_api_key=gemini,
            anthropic_api_key=anthropic,
            xai_api_key=xai,
            xai_fallback_enabled=xai_enabled,
        )
    )


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8"
    )


def _row(
    provider: str,
    ok: bool,
    *,
    minutes_ago: float = 1.0,
    latency_ms: float = 100.0,
    chain_position: int = 0,
    correlation_id: str | None = "req_1",
    error_class: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "v2",
        "ts": (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(),
        "provider": provider,
        "model": f"{provider}-model",
        "role": "primary",
        "ok": ok,
        "latency_ms": latency_ms,
        "error_type": None if ok else "X",
        "error_class": error_class,
        "http_status": None,
        "correlation_id": correlation_id,
        "call_id": f"llmc_{provider}_{minutes_ago}",
        "purpose": "analysis",
        "chain_position": chain_position,
        "attempt": 1,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "outcome": "success" if ok else "fallthrough",
    }


def _providers(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in snapshot["ai"]["providers"]}


# ── chain description ────────────────────────────────────────────────────────


def test_chain_comes_from_the_factory_not_a_hardcoded_list(tmp_path: Path) -> None:
    snap = ai_health_snapshot(path=tmp_path / "none.jsonl", settings=_settings())
    assert snap["ai"]["chain"]["primary"] == ["openai", "gemini"]
    assert snap["ai"]["chain"]["shadow"] == ["anthropic"]
    assert snap["ai"]["chain"]["source"] == "app/analysis/factory.py"


def test_chain_includes_grok_only_when_flag_and_key_are_set(tmp_path: Path) -> None:
    with_flag = ai_health_snapshot(
        path=tmp_path / "none.jsonl", settings=_settings(xai="xk", xai_enabled=True)
    )
    assert with_flag["ai"]["chain"]["primary"] == ["openai", "gemini", "grok"]

    key_only = ai_health_snapshot(
        path=tmp_path / "none.jsonl", settings=_settings(xai="xk", xai_enabled=False)
    )
    assert key_only["ai"]["chain"]["primary"] == ["openai", "gemini"]


def test_shadow_falls_back_to_gemini_without_anthropic(tmp_path: Path) -> None:
    snap = ai_health_snapshot(path=tmp_path / "none.jsonl", settings=_settings(anthropic=""))
    assert snap["ai"]["chain"]["shadow"] == ["gemini"]


# ── state classification ─────────────────────────────────────────────────────


def test_empty_stream_is_unknown_never_ok(tmp_path: Path) -> None:
    snap = ai_health_snapshot(path=tmp_path / "missing.jsonl", settings=_settings())
    blocks = _providers(snap)
    assert blocks["openai"]["state"] == "unknown"
    assert blocks["openai"]["calls"] == 0
    assert blocks["openai"]["failure_rate_pct"] is None
    assert blocks["openai"]["latency_p50_ms"] is None
    assert blocks["openai"]["configured"] is True


def test_state_ok_below_ten_percent(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    rows = [_row("openai", True, minutes_ago=20 - i) for i in range(19)]
    rows.insert(0, _row("openai", False, minutes_ago=30, error_class="server"))
    _write(path, rows)

    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["calls"] == 20 and block["failures"] == 1
    assert block["failure_rate_pct"] == 5.0
    assert block["state"] == "ok"
    assert block["consecutive_failures"] == 0
    assert block["last_error_class"] == "server"
    assert block["last_ok_ts"] is not None


def test_state_degraded_between_ten_and_fifty_percent(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    rows = [_row("openai", False, minutes_ago=10, error_class="rate_limit")]
    rows += [_row("openai", True, minutes_ago=9 - i) for i in range(9)]
    _write(path, rows)

    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["failure_rate_pct"] == 10.0
    assert block["state"] == "degraded"


def test_state_down_above_fifty_percent(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write(
        path,
        [
            _row("openai", False, minutes_ago=5, error_class="server"),
            _row("openai", False, minutes_ago=4, error_class="server"),
            _row("openai", True, minutes_ago=3),
        ],
    )
    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["state"] == "down"


def test_state_down_on_three_consecutive_failures_even_at_low_rate(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    rows = [_row("openai", True, minutes_ago=60 - i) for i in range(40)]
    rows += [_row("openai", False, minutes_ago=3 - i * 0.5, error_class="auth") for i in range(3)]
    _write(path, rows)

    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["consecutive_failures"] == 3
    assert block["failure_rate_pct"] < 10.0
    assert block["state"] == "down"
    assert block["last_error_class"] == "auth"


# ── windowing + de-duplication ───────────────────────────────────────────────


def test_rows_outside_the_window_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write(
        path,
        [
            _row("openai", False, minutes_ago=60 * 48, error_class="server"),
            _row("openai", True, minutes_ago=5),
        ],
    )
    block = _providers(ai_health_snapshot(window_hours=24.0, path=path, settings=_settings()))[
        "openai"
    ]
    assert block["calls"] == 1 and block["failures"] == 0


def test_outer_wrapper_row_does_not_double_count_the_ensemble(tmp_path: Path) -> None:
    """chain_position=-1 spans the whole chain; counting it too would double."""
    path = tmp_path / "t.jsonl"
    _write(
        path,
        [
            _row("openai", False, minutes_ago=3, chain_position=0, error_class="rate_limit"),
            _row("gemini", True, minutes_ago=2, chain_position=1),
            _row("gemini", True, minutes_ago=1, chain_position=-1),
        ],
    )
    blocks = _providers(ai_health_snapshot(path=path, settings=_settings()))
    assert blocks["openai"]["calls"] == 1
    assert blocks["gemini"]["calls"] == 1


def test_v1_rows_without_correlation_id_are_still_counted(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "ts": datetime.now(UTC).isoformat(),
                "provider": "openai",
                "model": "gpt-4o",
                "role": "primary",
                "ok": True,
                "latency_ms": 120.0,
                "error_type": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["calls"] == 1 and block["state"] == "ok"


def test_unconfigured_provider_seen_in_traffic_is_reported_as_not_configured(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    _write(path, [_row("grok", True, minutes_ago=2)])
    blocks = _providers(ai_health_snapshot(path=path, settings=_settings()))
    assert blocks["grok"]["configured"] is False
    assert blocks["grok"]["calls"] == 1


def test_latency_percentiles(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write(
        path,
        [
            _row("openai", True, minutes_ago=4, latency_ms=100.0),
            _row("openai", True, minutes_ago=3, latency_ms=200.0),
            _row("openai", True, minutes_ago=2, latency_ms=300.0),
            _row("openai", True, minutes_ago=1, latency_ms=400.0),
        ],
    )
    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["latency_p50_ms"] == 200.0
    assert block["latency_p95_ms"] == 400.0


def test_corrupt_lines_do_not_break_the_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        "not json\n" + json.dumps(_row("openai", True, minutes_ago=1)) + "\n", encoding="utf-8"
    )
    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["calls"] == 1


# ── endpoint ─────────────────────────────────────────────────────────────────


@pytest.fixture
def health_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    sink = tmp_path / "llm_telemetry.jsonl"
    _write(
        sink,
        [
            _row("openai", False, minutes_ago=3, chain_position=0, error_class="rate_limit"),
            _row("gemini", True, minutes_ago=2, chain_position=1),
        ],
    )
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    monkeypatch.setattr("app.ai.health.DEFAULT_TELEMETRY_PATH", sink)

    app = FastAPI()
    app.include_router(health_router)
    app.dependency_overrides = {}
    return TestClient(app)


def test_health_ai_endpoint_returns_chain_and_providers(
    health_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.settings.get_settings", lambda: _settings())
    response = health_client.get("/health/ai")
    assert response.status_code == 200
    body = response.json()
    assert body["chain"]["source"] == "app/analysis/factory.py"
    assert body["window_hours"] == 24.0
    names = [p["name"] for p in body["providers"]]
    assert "openai" in names and "gemini" in names
    assert response.headers["Cache-Control"].startswith("no-store")


def test_plain_health_endpoint_is_unchanged(health_client: TestClient) -> None:
    """HealthResponse must not grow — liveness consumers depend on it."""
    body = health_client.get("/health").json()
    assert body["status"] == "ok"
    assert "providers" not in body and "chain" not in body
