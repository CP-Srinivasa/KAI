"""Watchdog collector (#167): deterministic artifact reads, activation gate,
canary exclusion, honest insufficiency, dropbox emission."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.observability.watchdog_collector import (
    collect_agent_metric_inputs,
    collect_source_reputation_inputs,
    emit_watchdog_reports,
)

# ── fixtures ─────────────────────────────────────────────────────────────────


def _write_reliability(tmp_path: Path) -> Path:
    p = tmp_path / "source_reliability.json"
    p.write_text(
        json.dumps(
            {
                "report_type": "source_reliability",
                "scores": {
                    "beincrypto": {
                        "source_name": "beincrypto",
                        "hits": 8,
                        "miss": 2,
                        "n": 10,
                        "wilson_lower_95": 0.49,
                        "tier": "watch",
                    },
                    "YouTube": {
                        "source_name": "YouTube",
                        "hits": 0,
                        "miss": 0,
                        "n": 0,
                        "wilson_lower_95": None,
                        "tier": "insufficient",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return p


def _write_resolved(tmp_path: Path, *, real_rows: int) -> Path:
    p = tmp_path / "resolved.jsonl"
    rows = []
    for i in range(real_rows):
        rows.append(
            {
                "candidate_id": f"c{i}",
                "symbol": "BTC/USDT",
                "regime": "chop_quiet",
                "source": "autonomous_generator",
                "signal_confidence": 0.9,
                "is_canary": False,
                "fwd_3600s_bps": 10.0 if i % 2 == 0 else -10.0,
                "reached_take": False,
                "reached_stop": False,
            }
        )
    # ein Canary-Row darf NIE zählen
    rows.append(
        {
            "candidate_id": "probe",
            "source": "canary_probe",
            "is_canary": True,
            "signal_confidence": 0.85,
            "fwd_3600s_bps": 50.0,
        }
    )
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def _write_paper_audit(tmp_path: Path) -> Path:
    """Zwei Closed-Trades: einer real_analysis, einer canary_probe."""
    p = tmp_path / "paper_audit.jsonl"
    rows = []
    for src, oid in (("real_analysis", "o1"), ("canary_probe", "o2")):
        rows.append(
            {
                "event_type": "order_filled",
                "order_id": oid,
                "symbol": "BTC/USDT",
                "side": "buy",
                "position_side": "long",
                "quantity": 1.0,
                "fill_price": 100.0,
                "timestamp_utc": "2026-06-11T10:00:00+00:00",
                "source": src,
            }
        )
        rows.append(
            {
                "event_type": "position_closed",
                "order_id": oid,
                "symbol": "BTC/USDT",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "quantity": 1.0,
                "trade_pnl_usd": 1.0,
                "timestamp_utc": "2026-06-11T11:00:00+00:00",
                "source": src,
                "signal_source": src,
            }
        )
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


# ── source reputation inputs ─────────────────────────────────────────────────


def test_reputation_inputs_wilson_and_d227_merge(tmp_path: Path) -> None:
    rel = _write_reliability(tmp_path)
    d227 = [
        {"source": "beincrypto", "hit": 3, "miss": 7, "resolved": 10, "precision_pct": 30.0},
        {"source": "newsource", "hit": 4, "miss": 1, "resolved": 5, "precision_pct": 80.0},
        {"source": "None", "hit": 1, "miss": 0, "resolved": 1, "precision_pct": 100.0},
    ]
    inputs = {i.source_id: i for i in collect_source_reputation_inputs(rel, d227_by_source=d227)}
    # Wilson-Bound wird historical_accuracy
    assert inputs["beincrypto"].historical_accuracy == 0.49
    # D-227-Precision wird realized_signal_quality
    assert inputs["beincrypto"].realized_signal_quality == 0.30
    assert inputs["beincrypto"].sample_size == 10
    # n=0-Quelle bleibt ehrlich ohne Accuracy (Insufficiency-Pfad)
    assert inputs["YouTube"].historical_accuracy is None
    assert inputs["YouTube"].sample_size == 0
    # D-227-only-Quelle erscheint; "None"-Bucket nicht
    assert inputs["newsource"].realized_signal_quality == 0.80
    assert "none" not in inputs


def test_reputation_inputs_missing_file_yields_d227_only(tmp_path: Path) -> None:
    inputs = collect_source_reputation_inputs(
        tmp_path / "nope.json",
        d227_by_source=[
            {"source": "solo", "hit": 1, "miss": 1, "resolved": 2, "precision_pct": 50.0}
        ],
    )
    assert len(inputs) == 1 and inputs[0].source_id == "solo"


# ── agent scoreboard inputs ──────────────────────────────────────────────────


def test_agent_inputs_exclude_canary_cohorts(tmp_path: Path) -> None:
    audit = _write_paper_audit(tmp_path)
    resolved = _write_resolved(tmp_path, real_rows=4)
    inputs, meta = collect_agent_metric_inputs(audit_path=audit, resolved_path=resolved)
    ids = {i.agent_id for i in inputs}
    assert "canary_probe" not in ids
    assert "canary_probe" in meta["excluded_cohorts"]
    assert "real_analysis" in ids
    assert "autonomous_generator" in ids  # side-channel-only Kohorte
    assert meta["real_resolved"] == 4
    gen = next(i for i in inputs if i.agent_id == "autonomous_generator")
    assert gen.n_trades == 0  # shadow: keine Closed-Trades
    assert gen.brier is not None  # aber Kalibration aus dem Ledger


# ── activation gate + emission ───────────────────────────────────────────────


def test_gate_holds_without_real_resolutions(tmp_path: Path) -> None:
    audit = _write_paper_audit(tmp_path)
    resolved = _write_resolved(tmp_path, real_rows=0)  # nur der Canary-Row
    result = emit_watchdog_reports(
        reliability_path=_write_reliability(tmp_path),
        audit_path=audit,
        resolved_path=resolved,
        dropbox_dir=tmp_path / "dropbox",
    )
    assert result["emitted"] is False
    assert result["reason"] == "activation_gate_real_resolved_zero"
    assert not (tmp_path / "dropbox").exists()


def test_emission_writes_both_dropbox_streams(tmp_path: Path) -> None:
    result = emit_watchdog_reports(
        reliability_path=_write_reliability(tmp_path),
        audit_path=_write_paper_audit(tmp_path),
        resolved_path=_write_resolved(tmp_path, real_rows=4),
        d227_by_source=[
            {"source": "beincrypto", "hit": 3, "miss": 7, "resolved": 10, "precision_pct": 30.0}
        ],
        dropbox_dir=tmp_path / "dropbox",
        now_utc=datetime(2026, 6, 11, 20, 0, tzinfo=UTC),
    )
    assert result["emitted"] is True
    rep = json.loads(
        (tmp_path / "dropbox" / "source_reputation.jsonl").read_text().splitlines()[-1]
    )
    assert rep["report_type"] == "source_reputation"
    assert rep["invariant"] == "no_source_triggers_execution_alone"
    assert rep["n_sources"] >= 2
    board = json.loads(
        (tmp_path / "dropbox" / "agent_scoreboard.jsonl").read_text().splitlines()[-1]
    )
    assert board["report_type"] == "agent_scoreboard"
    assert board["invariant"] == "ranking_is_advisory_not_execution_authority"
    assert all(r["agent_id"] != "canary_probe" for r in board["ranking"])
    assert board["collector_audit"]["real_resolved"] == 4


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    result = emit_watchdog_reports(
        reliability_path=_write_reliability(tmp_path),
        audit_path=_write_paper_audit(tmp_path),
        resolved_path=_write_resolved(tmp_path, real_rows=2),
        dropbox_dir=tmp_path / "dropbox",
        dry_run=True,
    )
    assert result["emitted"] is False and result["dry_run"] is True
    assert not (tmp_path / "dropbox").exists()
