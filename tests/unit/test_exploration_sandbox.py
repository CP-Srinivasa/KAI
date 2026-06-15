"""EXPLORE-S0 — framework durchstich: runner -> capture -> report.

Exercises the whole sandbox end-to-end with the DummyProbe (no network, no keys).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.exploration.base import ExplorationProbe, ProbeMeta
from app.exploration.report import build_report, write_report
from app.exploration.runner import run_probes
from app.exploration.settings import ExplorationSettings
from app.exploration.sources import build_registry


def _settings(tmp_path: Path, **overrides: object) -> ExplorationSettings:
    base: dict[str, object] = {
        "enabled": False,
        "dummy_enabled": True,
        "artifacts_dir": str(tmp_path),
        "min_request_interval_seconds": 0.0,
    }
    base.update(overrides)
    # _env_file=None keeps these tests hermetic — never read the developer's .env.
    return ExplorationSettings(_env_file=None, **base)  # type: ignore[call-arg]


async def test_dummy_probe_runs_and_succeeds(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    results = await run_probes(settings=settings)

    assert len(results) == 1
    res = results[0]
    assert res.probe_id == "dummy:api"
    assert res.success is True
    assert res.record_count == 3
    assert res.meta.latency_ms is not None


async def test_capture_writes_raw_and_normalized(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await run_probes(settings=settings)

    raw_files = list((tmp_path / "raw" / "dummy__api").glob("*.json"))
    assert len(raw_files) == 1
    raw_payload = json.loads(raw_files[0].read_text(encoding="utf-8"))
    assert raw_payload["envelope"]["probe_id"] == "dummy:api"
    assert raw_payload["raw"]["count"] == 3

    normalized = tmp_path / "normalized" / "dummy__api.jsonl"
    assert normalized.exists()
    lines = [json.loads(line) for line in normalized.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3  # one line per record
    assert all(line["record"] is not None for line in lines)


async def test_report_aggregates_coverage(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await run_probes(settings=settings)

    report = build_report(artifacts_dir=str(tmp_path))
    assert report["probe_count"] == 1
    dummy = report["sources"]["dummy:api"]
    assert dummy["verdict"] == "GO"
    assert dummy["record_count"] == 3
    # "unit" is present on 2 of 3 records -> coverage math reflects the gap
    assert dummy["fields"]["unit"]["non_null"] == 2
    assert dummy["fields"]["symbol"]["non_null"] == 3

    json_path, md_path = write_report(artifacts_dir=str(tmp_path))
    assert json_path.exists()
    assert "GO" in md_path.read_text(encoding="utf-8")


async def test_failed_run_writes_envelope_line_and_no_go(tmp_path: Path) -> None:
    class FailingProbe(ExplorationProbe):
        source_name = "failing"
        access_mode = "api"

        async def probe(self):  # type: ignore[override]
            return self.fail("boom", meta=ProbeMeta(http_status=500))

    # Run the probe through the capture/report path manually (artifacts dir = tmp).
    from app.exploration.capture import append_normalized, write_raw

    result = await FailingProbe().probe()
    write_raw(result, artifacts_dir=str(tmp_path))
    append_normalized(result, artifacts_dir=str(tmp_path))

    report = build_report(artifacts_dir=str(tmp_path))
    failing = report["sources"]["failing:api"]
    assert failing["verdict"] == "NO-GO"
    assert failing["success_runs"] == 0
    assert "boom" in next(iter(failing["errors"]))


async def test_raising_probe_is_contained(tmp_path: Path) -> None:
    class RaisingProbe(ExplorationProbe):
        source_name = "rude"
        access_mode = "api"

        async def probe(self):  # type: ignore[override]
            raise RuntimeError("contract violation")

    # The runner must catch the violation and return a failed result, not raise.
    from app.exploration.runner import _run_one

    res = await _run_one(RaisingProbe(), timeout=5)
    assert res.success is False
    assert res.error is not None
    assert "probe_raised" in res.error


def test_registry_default_off_only_dummy(tmp_path: Path) -> None:
    # Global gate off -> only the (independent) dummy probe is eligible.
    settings = _settings(tmp_path, enabled=False)
    registry = build_registry(settings)
    assert set(registry) == {"dummy:api"}


def test_registry_gate_on_without_source_flags_still_only_dummy(tmp_path: Path) -> None:
    # Global gate on but no source flags -> still just dummy (sources opt-in).
    settings = _settings(tmp_path, enabled=True)
    registry = build_registry(settings)
    assert set(registry) == {"dummy:api"}
