"""Tests for app.learning.threshold_observations (Step 4 production wiring)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.learning.threshold_observations import (
    ALLOWED_SCORE_FIELDS,
    observations_from_audit,
)
from app.signals.bayes_journal import append_bayes_report
from app.signals.bayesian_confidence import build_default_engine, build_news_evidence

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _seed_bayes(audit_path: Path, decision_id: str, *, prior: float = 0.7) -> None:
    engine = build_default_engine()
    report = engine.evaluate(
        [build_news_evidence(relevance=0.0, sentiment_aligned_with_signal=True)],
        prior_probability=prior,
    )
    append_bayes_report(
        decision_id=decision_id,
        symbol="BTC/USDT",
        direction="long",
        report=report,
        path=audit_path,
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _loop_row(decision_id: str, order_id: str) -> dict:
    return {
        "decision_id": decision_id,
        "order_id": order_id,
        "fill_simulated": True,
        "symbol": "BTC/USDT",
    }


def _exec_close(order_id: str, pnl: float) -> dict:
    return {
        "event": "position_closed",
        "order_id": order_id,
        "trade_pnl_usd": pnl,
    }


def _all_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return (
        tmp_path / "loop_audit.jsonl",
        tmp_path / "exec_audit.jsonl",
        tmp_path / "bayes_audit.jsonl",
    )


# ─── Core join + score wiring ─────────────────────────────────────────────────


def test_observations_join_bayes_score_with_realized_pnl(tmp_path: Path) -> None:
    loop, execp, bayes = _all_paths(tmp_path)
    _seed_bayes(bayes, "dec_001")
    _seed_bayes(bayes, "dec_002")
    _write_jsonl(loop, [_loop_row("dec_001", "ord_A"), _loop_row("dec_002", "ord_B")])
    _write_jsonl(execp, [_exec_close("ord_A", 42.0), _exec_close("ord_B", -17.5)])

    obs = observations_from_audit(
        loop_audit_path=loop, exec_audit_path=execp, bayes_audit_path=bayes
    )

    assert len(obs) == 2
    by_id = {o.observation_id: o for o in obs}
    assert by_id["dec_001"].realized_pnl_usd == pytest.approx(42.0)
    assert by_id["dec_002"].realized_pnl_usd == pytest.approx(-17.5)
    # confidence_score is a float in [0, 1]; just verify it was populated.
    for o in obs:
        assert 0.0 <= o.score <= 1.0


def test_decisions_without_close_event_are_skipped(tmp_path: Path) -> None:
    loop, execp, bayes = _all_paths(tmp_path)
    _seed_bayes(bayes, "dec_open")
    _write_jsonl(loop, [_loop_row("dec_open", "ord_open")])
    # No close event for ord_open
    _write_jsonl(execp, [])

    obs = observations_from_audit(
        loop_audit_path=loop, exec_audit_path=execp, bayes_audit_path=bayes
    )
    assert obs == []


def test_close_event_without_bayes_entry_is_skipped(tmp_path: Path) -> None:
    """Operator-Manual-Trade ohne Bayes-Audit landet nicht im Output."""
    loop, execp, bayes = _all_paths(tmp_path)
    _write_jsonl(loop, [_loop_row("dec_manual", "ord_manual")])
    _write_jsonl(execp, [_exec_close("ord_manual", 100.0)])
    # bayes file does not exist at all → leeres laden.
    obs = observations_from_audit(
        loop_audit_path=loop, exec_audit_path=execp, bayes_audit_path=bayes
    )
    assert obs == []


def test_close_event_without_loop_row_is_skipped(tmp_path: Path) -> None:
    """Order ohne JOIN auf decision_id wird übersprungen."""
    loop, execp, bayes = _all_paths(tmp_path)
    _seed_bayes(bayes, "dec_001")
    _write_jsonl(loop, [])  # no order ↔ decision mapping
    _write_jsonl(execp, [_exec_close("ord_orphan", 25.0)])

    obs = observations_from_audit(
        loop_audit_path=loop, exec_audit_path=execp, bayes_audit_path=bayes
    )
    assert obs == []


# ─── Tier-Closes + Aggregation ────────────────────────────────────────────────


def test_multiple_close_events_per_order_are_summed(tmp_path: Path) -> None:
    """Tier-Closes (T1/T2/T3/T4) summieren sich auf einen Trade-PnL."""
    loop, execp, bayes = _all_paths(tmp_path)
    _seed_bayes(bayes, "dec_001")
    _write_jsonl(loop, [_loop_row("dec_001", "ord_A")])
    _write_jsonl(
        execp,
        [
            {"event": "position_partial_closed", "order_id": "ord_A", "trade_pnl_usd": 10.0},
            {"event": "position_partial_closed", "order_id": "ord_A", "trade_pnl_usd": 20.0},
            {"event": "position_closed", "order_id": "ord_A", "trade_pnl_usd": 5.0},
        ],
    )
    obs = observations_from_audit(
        loop_audit_path=loop, exec_audit_path=execp, bayes_audit_path=bayes
    )
    assert len(obs) == 1
    assert obs[0].realized_pnl_usd == pytest.approx(35.0)


# ─── Score-Field-Validation ───────────────────────────────────────────────────


def test_invalid_score_field_raises(tmp_path: Path) -> None:
    loop, execp, bayes = _all_paths(tmp_path)
    with pytest.raises(ValueError, match="score_field must be one of"):
        observations_from_audit(
            loop_audit_path=loop,
            exec_audit_path=execp,
            bayes_audit_path=bayes,
            score_field="brier_score",  # not allowed
        )


def test_posterior_probability_score_field_works(tmp_path: Path) -> None:
    loop, execp, bayes = _all_paths(tmp_path)
    _seed_bayes(bayes, "dec_001", prior=0.85)
    _write_jsonl(loop, [_loop_row("dec_001", "ord_A")])
    _write_jsonl(execp, [_exec_close("ord_A", 10.0)])

    obs = observations_from_audit(
        loop_audit_path=loop,
        exec_audit_path=execp,
        bayes_audit_path=bayes,
        score_field="posterior_probability",
    )
    assert len(obs) == 1
    # Posterior should differ from confidence (different aggregation).
    assert 0.0 <= obs[0].score <= 1.0


# ─── Sanity ───────────────────────────────────────────────────────────────────


def test_allowed_score_fields_constant_documented() -> None:
    assert ALLOWED_SCORE_FIELDS == {"confidence_score", "posterior_probability"}


def test_missing_files_yield_empty_list(tmp_path: Path) -> None:
    obs = observations_from_audit(
        loop_audit_path=tmp_path / "missing_loop.jsonl",
        exec_audit_path=tmp_path / "missing_exec.jsonl",
        bayes_audit_path=tmp_path / "missing_bayes.jsonl",
    )
    assert obs == []


# ─── End-to-end to the optimizer ──────────────────────────────────────────────


def test_observations_feed_into_optimizer_e2e(tmp_path: Path) -> None:
    """Smoke-test the full audit → optimizer pipe — verifies the wiring
    produces a structurally valid report. We don't assert a specific
    decision because the synthetic confidence scores depend on the
    BayesEngine's evidence-weighting (the unit-tests for the optimizer
    cover decision semantics on hand-crafted observations)."""
    from app.learning.threshold_optimizer import ThresholdConfig, optimize_threshold

    loop, execp, bayes = _all_paths(tmp_path)
    loop_rows = []
    exec_rows = []
    for i in range(50):
        decision_id = f"dec_win_{i:03d}"
        order_id = f"ord_w_{i:03d}"
        _seed_bayes(bayes, decision_id, prior=0.85)
        loop_rows.append(_loop_row(decision_id, order_id))
        exec_rows.append(_exec_close(order_id, 100.0))
    for i in range(50):
        decision_id = f"dec_lose_{i:03d}"
        order_id = f"ord_l_{i:03d}"
        _seed_bayes(bayes, decision_id, prior=0.55)
        loop_rows.append(_loop_row(decision_id, order_id))
        exec_rows.append(_exec_close(order_id, -80.0))
    _write_jsonl(loop, loop_rows)
    _write_jsonl(execp, exec_rows)

    obs = observations_from_audit(
        loop_audit_path=loop, exec_audit_path=execp, bayes_audit_path=bayes
    )
    assert len(obs) == 100

    # Use a grid that covers the actual confidence-score range produced by
    # the BayesEngine (which sits below 0.50 for the test fixtures here).
    grid = tuple(round(0.05 + 0.05 * i, 2) for i in range(18))  # 0.05..0.90
    cfg = ThresholdConfig(threshold_grid=grid, min_observations=30)
    report = optimize_threshold(
        observations=obs, baseline_threshold=0.0, config=cfg
    )
    assert report.n_observations == 100
    assert report.baseline_n_passing == 100
    assert report.baseline_pnl_usd == pytest.approx(1000.0)  # 50*100 + 50*-80 = 1000
    assert report.grid  # non-empty grid
    assert report.decision in {"approve", "neutral", "reject", "insufficient_data"}
