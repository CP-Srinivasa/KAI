from __future__ import annotations

import time
from collections.abc import Mapping
from typing import cast

from fastapi import APIRouter, Request, Response

from app.observability.event_loop_lag import DEFAULT_EVENT_LOOP_LAG_SAMPLER
from app.observability.http_latency import HTTP_LATENCY_RECORDER

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
PROCESS_START_TIME_SECONDS = time.time()

router = APIRouter(tags=["metrics"])

LagMetricSnapshot = Mapping[str, Mapping[str, float | int | None]]
HTTPMetricSnapshot = Mapping[str, Mapping[str, float | int]]


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: float | int | None) -> str:
    if value is None:
        return "0"
    if isinstance(value, int):
        return str(value)
    return format(value, ".12g")


def _sample(name: str, labels: Mapping[str, str] | None, value: float | int | None) -> str:
    if not labels:
        return f"{name} {_format_value(value)}"
    rendered_labels = ",".join(
        f'{key}="{_escape_label_value(label_value)}"' for key, label_value in labels.items()
    )
    return f"{name}{{{rendered_labels}}} {_format_value(value)}"


def _snapshot_from(source: object) -> object:
    snapshot = getattr(source, "snapshot", None)
    if not callable(snapshot):
        return {}
    return snapshot()


def _lag_snapshots(request: Request) -> LagMetricSnapshot:
    sampler = getattr(request.app.state, "event_loop_lag_sampler", DEFAULT_EVENT_LOOP_LAG_SAMPLER)
    return cast(LagMetricSnapshot, _snapshot_from(sampler))


def _http_snapshots(request: Request) -> HTTPMetricSnapshot:
    recorder = getattr(request.app.state, "http_latency_recorder", HTTP_LATENCY_RECORDER)
    return cast(HTTPMetricSnapshot, _snapshot_from(recorder))


def render_prometheus_metrics(
    *,
    lag_snapshots: LagMetricSnapshot,
    http_snapshots: HTTPMetricSnapshot,
    now_seconds: float | None = None,
) -> str:
    now = time.time() if now_seconds is None else now_seconds
    lines = [
        "# HELP kai_event_loop_lag_seconds Event loop lag over the in-process sample window.",
        "# TYPE kai_event_loop_lag_seconds gauge",
    ]
    for window_label in ("60s", "3600s"):
        snapshot = lag_snapshots.get(window_label, {})
        for quantile, field in (("0.5", "p50_s"), ("0.95", "p95_s"), ("max", "max_s")):
            lines.append(
                _sample(
                    "kai_event_loop_lag_seconds",
                    {"window": window_label, "quantile": quantile},
                    snapshot.get(field, 0),
                )
            )

    lines.extend(
        [
            "# HELP kai_event_loop_lag_samples_total Event loop lag samples in the window.",
            "# TYPE kai_event_loop_lag_samples_total gauge",
        ]
    )
    for window_label in ("60s", "3600s"):
        snapshot = lag_snapshots.get(window_label, {})
        lines.append(
            _sample(
                "kai_event_loop_lag_samples_total",
                {"window": window_label},
                snapshot.get("n", 0),
            )
        )

    lines.extend(
        [
            "# HELP kai_http_request_duration_seconds HTTP request duration by route template.",
            "# TYPE kai_http_request_duration_seconds gauge",
        ]
    )
    for route in sorted(http_snapshots):
        snapshot = http_snapshots[route]
        for quantile, field in (("0.5", "p50_s"), ("0.95", "p95_s"), ("max", "max_s")):
            lines.append(
                _sample(
                    "kai_http_request_duration_seconds",
                    {"route": route, "quantile": quantile},
                    snapshot.get(field, 0),
                )
            )

    lines.extend(
        [
            "# HELP kai_http_requests_window_total HTTP requests currently retained by route.",
            "# TYPE kai_http_requests_window_total gauge",
        ]
    )
    for route in sorted(http_snapshots):
        lines.append(
            _sample(
                "kai_http_requests_window_total",
                {"route": route},
                http_snapshots[route].get("count", 0),
            )
        )

    uptime_s = max(0.0, now - PROCESS_START_TIME_SECONDS)
    lines.extend(
        [
            "# HELP kai_process_uptime_seconds Process uptime in seconds.",
            "# TYPE kai_process_uptime_seconds gauge",
            _sample("kai_process_uptime_seconds", None, uptime_s),
            "# HELP kai_process_start_time_seconds Process start time as Unix seconds.",
            "# TYPE kai_process_start_time_seconds gauge",
            _sample("kai_process_start_time_seconds", None, PROCESS_START_TIME_SECONDS),
        ]
    )
    return "\n".join(lines) + "\n"


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    body = render_prometheus_metrics(
        lag_snapshots=_lag_snapshots(request),
        http_snapshots=_http_snapshots(request),
    )
    return Response(content=body, media_type=PROMETHEUS_CONTENT_TYPE)
