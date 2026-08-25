from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.observability.http_latency import (
    HTTPLatencyMiddleware,
    SlidingWindowHTTPRouteLatencyRecorder,
)


class StepClock:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def __call__(self) -> float:
        if not self.values:
            return 0.0
        return self.values.pop(0)


def test_middleware_records_route_template_not_raw_path() -> None:
    recorder = SlidingWindowHTTPRouteLatencyRecorder(clock=StepClock([0.0, 0.050, 1.0, 1.070]))
    app = FastAPI()
    app.add_middleware(HTTPLatencyMiddleware, recorder=recorder)

    @app.get("/items/{item_id}")
    async def item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    client = TestClient(app)
    assert client.get("/items/alpha").status_code == 200
    assert client.get("/items/beta").status_code == 200

    snapshot = recorder.snapshot()
    assert set(snapshot) == {"/items/{item_id}"}
    assert snapshot["/items/{item_id}"]["count"] == 2
    assert snapshot["/items/{item_id}"]["p50_s"] == 0.050
    assert snapshot["/items/{item_id}"]["p95_s"] == 0.070
    assert snapshot["/items/{item_id}"]["max_s"] == 0.070


def test_middleware_bundles_unmatched_paths() -> None:
    recorder = SlidingWindowHTTPRouteLatencyRecorder(clock=StepClock([0.0, 0.010, 1.0, 1.020]))
    app = FastAPI()
    app.add_middleware(HTTPLatencyMiddleware, recorder=recorder)
    client = TestClient(app)

    assert client.get("/missing/one").status_code == 404
    assert client.get("/missing/two").status_code == 404

    snapshot = recorder.snapshot()
    assert set(snapshot) == {"<unmatched>"}
    assert snapshot["<unmatched>"]["count"] == 2


def test_middleware_fail_soft_when_recorder_raises() -> None:
    class RaisingRecorder:
        def record(self, route: str, duration_s: float) -> None:
            raise RuntimeError("recorder failed")

    app = FastAPI()
    app.add_middleware(HTTPLatencyMiddleware, recorder=RaisingRecorder())

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    response = TestClient(app).get("/ok")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_middleware_keeps_streaming_response_intact() -> None:
    recorder = SlidingWindowHTTPRouteLatencyRecorder(clock=StepClock([0.0, 0.025]))
    app = FastAPI()
    app.add_middleware(HTTPLatencyMiddleware, recorder=recorder)

    def chunks() -> Iterator[bytes]:
        yield b"alpha"
        yield b"-"
        yield b"omega"

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(chunks(), media_type="text/plain")

    response = TestClient(app).get("/stream")
    assert response.status_code == 200
    assert response.text == "alpha-omega"
    assert recorder.snapshot()["/stream"]["count"] == 1
