"""Tests for B-002 LLM telemetry (Audit F-5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability.llm_telemetry import llm_telemetry_summary, record_llm_call


def test_record_and_summary_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    for ok, lat in [(True, 100.0), (True, 200.0), (False, 300.0), (True, 400.0)]:
        record_llm_call(provider="openai", model="gpt-4o", ok=ok, latency_ms=lat, path=p)
    s = llm_telemetry_summary(path=p)
    assert s["implemented"] is True
    assert s["n"] == 4 and s["failures"] == 1
    assert s["failure_rate_pct"] == 25.0
    assert s["latency_p50_ms"] == 200.0
    assert s["latency_p95_ms"] == 400.0


def test_summary_empty_is_honest(tmp_path: Path) -> None:
    s = llm_telemetry_summary(path=tmp_path / "missing.jsonl")
    assert s["n"] == 0 and s["failure_rate_pct"] is None
    assert s["latency_p50_ms"] is None and s["latency_p95_ms"] is None


def test_summary_window_filters_old_rows(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    old_ts = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    p.write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "ts": old_ts,
                "provider": "openai",
                "model": "m",
                "role": "primary",
                "ok": False,
                "latency_ms": 999.0,
                "error_type": "x",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record_llm_call(provider="openai", model="m", ok=True, latency_ms=50.0, path=p)
    s = llm_telemetry_summary(window_hours=24.0, path=p)
    assert s["n"] == 1 and s["failures"] == 0


def test_record_never_raises_on_bad_path() -> None:
    record_llm_call(
        provider="x",
        model="m",
        ok=True,
        latency_ms=1.0,
        path=Path("Z:/nonexistent/dir/t.jsonl"),
    )


def test_error_type_recorded(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    record_llm_call(
        provider="gemini",
        model="m",
        ok=False,
        latency_ms=10.0,
        role="shadow",
        error_type="TimeoutError",
        path=p,
    )
    row = json.loads(p.read_text("utf-8").splitlines()[0])
    assert row["error_type"] == "TimeoutError" and row["role"] == "shadow"
