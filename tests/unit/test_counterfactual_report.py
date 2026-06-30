"""Counterfactual Live/Replay roll-up report tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.counterfactual_report import (
    build_counterfactual_report,
    render_counterfactual_report,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _rows() -> list[dict[str, object]]:
    return [
        {  # in-range, no drift, gate ok
            "symbol": "BTCUSDT",
            "source": "technical_paper",
            "in_settled_range": True,
            "drift_to_range_bps": 0.0,
            "drift_exceeded": False,
            "data_quality_suspect": False,
            "gate_would_reject": False,
        },
        {  # real drift, gate would reject
            "symbol": "BTCUSDT",
            "source": "technical_paper",
            "in_settled_range": False,
            "drift_to_range_bps": 120.0,
            "drift_exceeded": True,
            "data_quality_suspect": False,
            "gate_would_reject": True,
        },
        {  # glitch: suspect, excluded from drift + percentiles, gate unknown
            "symbol": "ETHUSDT",
            "source": "technical_paper",
            "in_settled_range": False,
            "drift_to_range_bps": 5000.0,
            "drift_exceeded": False,
            "data_quality_suspect": True,
            "gate_would_reject": None,
        },
        {  # real drift, gate unknown
            "symbol": "ETHUSDT",
            "source": "momentum",
            "in_settled_range": False,
            "drift_to_range_bps": -60.0,
            "drift_exceeded": True,
            "data_quality_suspect": False,
            "gate_would_reject": None,
        },
    ]


def test_counts_drift_suspect_and_tri_state_gate(tmp_path: Path) -> None:
    path = tmp_path / "counterfactual_comparison.jsonl"
    _write_jsonl(path, _rows())

    report = build_counterfactual_report(path)

    assert report.total == 4
    assert report.in_settled_range == 1
    assert report.drift_exceeded == 2
    assert report.data_quality_suspect == 1
    assert report.gate_would_reject == 1  # only the explicit True, not the Nones
    assert report.gate_unknown == 2
    assert report.available is True


def test_percentiles_exclude_suspect_glitch(tmp_path: Path) -> None:
    path = tmp_path / "cf.jsonl"
    _write_jsonl(path, _rows())

    report = build_counterfactual_report(path)

    # non-suspect |drift| = [0, 60, 120]; the 5000 glitch must NOT leak in.
    assert report.drift_abs_bps["p50"] == 60.0
    assert report.drift_abs_bps["p90"] == 120.0
    assert report.drift_abs_bps["max"] == 120.0


def _v1_backlog_rows() -> list[dict[str, object]]:
    """Pre-v2 records: stored ``drift_exceeded`` WITHOUT a ``data_quality_suspect``
    field (the suspect gate did not exist yet). The physically-impossible
    ~100-index glitch was therefore stored as a real drift."""
    return [
        {  # v1 real drift (moderate, plausible) — must stay counted
            "symbol": "ENA/USDT",
            "source": "technical_screener",
            "in_settled_range": False,
            "drift_to_range_bps": 80.0,
            "drift_exceeded": True,
            "schema_version": "v1",
            "gate_would_reject": None,
        },
        {  # v1 glitch: live=~100 index vs sub-$1 settled → 10.7M bps, no suspect field
            "symbol": "ENA/USDT",
            "source": "technical_screener",
            "in_settled_range": False,
            "drift_to_range_bps": 10781534.4,
            "drift_exceeded": True,
            "schema_version": "v1",
            "gate_would_reject": None,
        },
    ]


def test_v1_glitch_reclassified_suspect_at_read_time(tmp_path: Path) -> None:
    # Read-time plausibility: a record beyond the suspect range is a glitch even
    # if the stored fields (v1 backlog) never flagged it. It must not inflate
    # drift_exceeded, must count as suspect, and must stay out of the percentiles.
    path = tmp_path / "cf.jsonl"
    _write_jsonl(path, _v1_backlog_rows())

    report = build_counterfactual_report(path)

    assert report.drift_exceeded == 1  # only the 80 bps; the 10.7M glitch excluded
    assert report.data_quality_suspect == 1  # the glitch, via read-time rule
    assert report.drift_abs_bps["max"] == 80.0  # glitch kept out of the distribution

    by_source = {row["source"]: row for row in report.by_source}
    assert by_source["technical_screener"] == {
        "source": "technical_screener",
        "total": 2,
        "drift_exceeded": 1,
        "data_quality_suspect": 1,
    }


def test_by_symbol_and_by_source_breakdown(tmp_path: Path) -> None:
    path = tmp_path / "cf.jsonl"
    _write_jsonl(path, _rows())

    report = build_counterfactual_report(path)

    by_symbol = {row["symbol"]: row for row in report.by_symbol}
    assert by_symbol["BTCUSDT"] == {
        "symbol": "BTCUSDT",
        "total": 2,
        "drift_exceeded": 1,
        "data_quality_suspect": 0,
    }
    assert by_symbol["ETHUSDT"]["data_quality_suspect"] == 1

    by_source = {row["source"]: row for row in report.by_source}
    assert by_source["technical_paper"]["total"] == 3
    assert by_source["momentum"]["drift_exceeded"] == 1

    rendered = render_counterfactual_report(report)
    assert "total_comparisons: 4" in rendered
    assert "BY SYMBOL" in rendered


def test_empty_stream_is_unavailable(tmp_path: Path) -> None:
    report = build_counterfactual_report(tmp_path / "missing.jsonl")
    assert report.total == 0
    assert report.available is False
    assert report.drift_abs_bps["max"] == 0.0
    assert report.by_symbol == []


def test_default_path_is_logger_output() -> None:
    from app.observability.counterfactual_replay_logger import OUTPUT_PATH

    assert OUTPUT_PATH.as_posix() == "artifacts/counterfactual_comparison.jsonl"
