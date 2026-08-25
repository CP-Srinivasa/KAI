from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import metrics


class FakeLagSampler:
    def snapshot(self) -> dict[str, dict[str, float | int | None]]:
        return {
            "60s": {
                "n": 2,
                "p50_s": 0.01,
                "p95_s": 0.02,
                "max_s": 0.02,
                "window_s": 60,
                "last_sample_at_monotonic": 10.0,
            },
            "3600s": {
                "n": 3,
                "p50_s": 0.01,
                "p95_s": 0.50,
                "max_s": 0.50,
                "window_s": 3600,
                "last_sample_at_monotonic": 12.0,
            },
        }


class FakeHTTPRecorder:
    def snapshot(self) -> dict[str, dict[str, float | int]]:
        return {
            "/items/{item_id}": {
                "count": 2,
                "p50_s": 0.03,
                "p95_s": 0.05,
                "max_s": 0.05,
            },
            "/quoted\\route\n{x}": {
                "count": 1,
                "p50_s": 0.07,
                "p95_s": 0.07,
                "max_s": 0.07,
            },
        }


def _client() -> TestClient:
    app = FastAPI()
    app.state.event_loop_lag_sampler = FakeLagSampler()
    app.state.http_latency_recorder = FakeHTTPRecorder()
    app.include_router(metrics.router)
    return TestClient(app)


def test_metrics_endpoint_serves_prometheus_text_format() -> None:
    response = _client().get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert "kai_event_loop_lag_seconds" in response.text
    assert "kai_event_loop_lag_samples_total" in response.text
    assert "kai_http_request_duration_seconds" in response.text
    assert "kai_http_requests_window_total" in response.text
    assert "kai_process_uptime_seconds" in response.text
    assert "kai_process_start_time_seconds" in response.text


def test_metrics_lines_match_prometheus_sample_shape() -> None:
    response = _client().get("/metrics")
    sample_re = re.compile(
        r"^kai_[a-z_]+(?:\{[a-z_]+=\"(?:[^\"\\]|\\[\\\"n])*\""
        r"(?:,[a-z_]+=\"(?:[^\"\\]|\\[\\\"n])*\")*\})? -?\d+(?:\.\d+)?$"
    )

    for line in response.text.splitlines():
        if not line or line.startswith("#"):
            continue
        assert sample_re.match(line), line


def test_metrics_escape_label_values_and_do_not_emit_path_parameters() -> None:
    response = _client().get("/metrics")

    assert 'route="/items/{item_id}"' in response.text
    assert "/items/123" not in response.text
    assert 'route="/quoted\\\\route\\n{x}"' in response.text


def test_render_metrics_can_escape_arbitrary_label_values() -> None:
    rendered = metrics._sample(
        "kai_http_requests_window_total",
        {"route": 'a\\b"c\nz'},
        1,
    )
    assert rendered == 'kai_http_requests_window_total{route="a\\\\b\\"c\\nz"} 1'


def test_metrics_endpoint_uses_app_state_recorders() -> None:
    app = FastAPI()
    app.state.event_loop_lag_sampler = FakeLagSampler()
    app.state.http_latency_recorder = FakeHTTPRecorder()
    app.include_router(metrics.router)

    response = TestClient(app).get("/metrics")

    assert 'window="60s",quantile="0.95"} 0.02' in response.text
    assert 'route="/items/{item_id}",quantile="0.95"} 0.05' in response.text
