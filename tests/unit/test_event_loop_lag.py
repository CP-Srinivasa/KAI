from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from app.observability.event_loop_lag import EventLoopLagSampler


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def fake_sleep_factory(
    clock: FakeClock,
    elapsed: list[float],
) -> Callable[[float], Awaitable[None]]:
    async def _sleep(_interval_s: float) -> None:
        if not elapsed:
            raise asyncio.CancelledError
        clock.advance(elapsed.pop(0))

    return _sleep


async def _run_until_fake_sleep_cancelled(sampler: EventLoopLagSampler) -> None:
    with pytest.raises(asyncio.CancelledError):
        await sampler.run()


@pytest.mark.asyncio
async def test_sampler_records_lag_percentiles_for_60s_and_3600s_windows(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    sampler = EventLoopLagSampler(
        interval_s=1.0,
        artifact_path=tmp_path / "event_loop_lag.jsonl",
        clock=clock,
        sleep=fake_sleep_factory(clock, [1.10, 1.20, 1.30]),
    )

    await _run_until_fake_sleep_cancelled(sampler)

    snapshot_60 = sampler.snapshot(window_s=60)
    snapshot_3600 = sampler.snapshot(window_s=3600)
    assert snapshot_60 == {
        "n": 3,
        "p50_s": pytest.approx(0.20),
        "p95_s": pytest.approx(0.30),
        "max_s": pytest.approx(0.30),
        "window_s": 60,
        "last_sample_at_monotonic": pytest.approx(3.60),
    }
    assert snapshot_3600["n"] == 3
    assert snapshot_3600["p95_s"] == pytest.approx(0.30)

    clock.advance(70.0)
    assert sampler.snapshot(window_s=60)["n"] == 0
    assert sampler.snapshot(window_s=3600)["n"] == 3


@pytest.mark.asyncio
async def test_sampler_flushes_jsonl_every_60_seconds(tmp_path: Path) -> None:
    clock = FakeClock()
    artifact_path = tmp_path / "observability" / "event_loop_lag.jsonl"
    sampler = EventLoopLagSampler(
        interval_s=1.0,
        artifact_path=artifact_path,
        clock=clock,
        sleep=fake_sleep_factory(clock, [1.0] * 60),
    )

    await _run_until_fake_sleep_cancelled(sampler)

    lines = artifact_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["window_s"] == 60
    assert payload["n"] == 60
    assert payload["p50_s"] == 0.0
    assert payload["p95_s"] == 0.0
    assert payload["max_s"] == 0.0
    assert payload["ts_utc"].endswith("+00:00")


@pytest.mark.asyncio
async def test_sampler_rotates_jsonl_to_last_1440_lines(tmp_path: Path) -> None:
    clock = FakeClock()
    artifact_path = tmp_path / "observability" / "event_loop_lag.jsonl"
    artifact_path.parent.mkdir(parents=True)
    old_lines = [
        json.dumps({"ts_utc": f"old-{idx}", "window_s": 60, "n": 1}) for idx in range(2000)
    ]
    artifact_path.write_text("\n".join(old_lines) + "\n", encoding="utf-8")
    sampler = EventLoopLagSampler(
        interval_s=1.0,
        artifact_path=artifact_path,
        clock=clock,
        sleep=fake_sleep_factory(clock, [60.0]),
    )

    await _run_until_fake_sleep_cancelled(sampler)

    lines = artifact_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1440
    assert json.loads(lines[0])["ts_utc"] == "old-561"
    assert json.loads(lines[-1])["window_s"] == 60
    assert json.loads(lines[-1])["n"] == 1


@pytest.mark.asyncio
async def test_sampler_logs_write_errors_without_stopping(caplog, tmp_path: Path) -> None:
    clock = FakeClock()
    sampler = EventLoopLagSampler(
        interval_s=1.0,
        artifact_path=tmp_path,
        clock=clock,
        sleep=fake_sleep_factory(clock, [60.0, 1.25]),
    )

    await _run_until_fake_sleep_cancelled(sampler)

    assert sampler.snapshot(window_s=60)["n"] == 2
    assert "event_loop_lag_write_failed" in caplog.text
