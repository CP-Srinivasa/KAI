"""Loader: Bayes-Audit + Outcome-Map → OutcomePair-Stream.
   Dashboard: /dashboard/api/calibration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import dashboard as dashboard_mod
from app.api.routers.dashboard import router
from app.learning.calibration import compute_calibration
from app.learning.calibration_loader import pairs_from_bayes_audit
from app.learning.regime_lookup import RegimeLookup
from app.signals.bayes_journal import append_bayes_report
from app.signals.bayesian_confidence import build_default_engine, build_news_evidence


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _seed_audit(audit_path: Path, *, n: int, posterior_for_long: float = 0.7) -> list[str]:
    """Seed audit with deterministic posteriors. Returns list of decision_ids."""
    engine = build_default_engine()
    decision_ids: list[str] = []
    for i in range(n):
        # Forciert posterior ≈ posterior_for_long durch Wahl des Priors
        report = engine.evaluate(
            [build_news_evidence(relevance=0.0, sentiment_aligned_with_signal=True)],
            prior_probability=posterior_for_long,
        )
        decision_id = f"dec_{i:03d}"
        decision_ids.append(decision_id)
        append_bayes_report(
            decision_id=decision_id,
            symbol="BTC/USDT",
            direction="long",
            report=report,
            path=audit_path,
        )
    return decision_ids


# ─── Loader ──────────────────────────────────────────────────────────────────


class TestLoader:
    def test_empty_audit_yields_empty_pairs(self, tmp_path: Path) -> None:
        pairs = pairs_from_bayes_audit(
            bayes_audit_path=tmp_path / "missing.jsonl",
            outcomes={},
        )
        assert pairs == []

    def test_unmatched_decision_ids_are_skipped(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        ids = _seed_audit(audit, n=5)
        # Map deckt nur die ersten 2 IDs ab
        outcomes = {ids[0]: 1, ids[1]: 0}
        pairs = pairs_from_bayes_audit(bayes_audit_path=audit, outcomes=outcomes)
        assert len(pairs) == 2
        assert {p.decision_id for p in pairs} == {ids[0], ids[1]}

    def test_invalid_outcome_value_is_skipped(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        ids = _seed_audit(audit, n=3)
        outcomes = {ids[0]: 1, ids[1]: 5, ids[2]: 0}  # 5 illegal
        pairs = pairs_from_bayes_audit(bayes_audit_path=audit, outcomes=outcomes)
        assert {p.decision_id for p in pairs} == {ids[0], ids[2]}

    def test_regime_lookup_tags_pairs_with_active_regime(self, tmp_path: Path) -> None:
        """Step 5: regime_lookup taggt OutcomePair.regime aus Snapshot ≤ entry.timestamp_utc."""
        audit = tmp_path / "audit.jsonl"
        ids = _seed_audit(audit, n=3)

        # Regime-State so seeden, dass ALLE Bayes-Einträge nach 06:00 UTC liegen
        # (append_bayes_report nutzt datetime.now(UTC) — wir wählen ein
        # Snapshot weit in der Vergangenheit, das wird Bin für alle Einträge).
        regime_state_dir = tmp_path / "regime_state"
        regime_state_dir.mkdir()
        (regime_state_dir / "btc_regime.jsonl").write_text(
            '{"asset":"BTC","timestamp":"2000-01-01T00:00:00Z",'
            '"regime":"breakout_up","vol_class":"vol_low","confidence":1.0}\n',
            encoding="utf-8",
        )
        lookup = RegimeLookup.from_artifacts(regime_state_dir)

        outcomes = dict.fromkeys(ids, 1)
        pairs = pairs_from_bayes_audit(
            bayes_audit_path=audit, outcomes=outcomes, regime_lookup=lookup
        )

        assert len(pairs) == 3
        assert all(p.regime == "breakout_up|vol_low" for p in pairs)

    def test_regime_lookup_none_keeps_regime_unset(self, tmp_path: Path) -> None:
        """Backwards-compat: ohne regime_lookup bleibt OutcomePair.regime None."""
        audit = tmp_path / "audit.jsonl"
        ids = _seed_audit(audit, n=2)
        pairs = pairs_from_bayes_audit(
            bayes_audit_path=audit, outcomes=dict.fromkeys(ids, 1)
        )
        assert all(p.regime is None for p in pairs)

    def test_regime_lookup_miss_leaves_regime_none(self, tmp_path: Path) -> None:
        """Lookup-Miss (Asset nicht im Index) → regime bleibt None, kein Crash."""
        audit = tmp_path / "audit.jsonl"
        ids = _seed_audit(audit, n=2)  # symbol="BTC/USDT"

        regime_state_dir = tmp_path / "regime_state"
        regime_state_dir.mkdir()
        (regime_state_dir / "eth_regime.jsonl").write_text(
            '{"asset":"ETH","timestamp":"2000-01-01T00:00:00Z",'
            '"regime":"trend_up","vol_class":"vol_low","confidence":1.0}\n',
            encoding="utf-8",
        )
        lookup = RegimeLookup.from_artifacts(regime_state_dir)

        pairs = pairs_from_bayes_audit(
            bayes_audit_path=audit,
            outcomes=dict.fromkeys(ids, 1),
            regime_lookup=lookup,
        )
        assert len(pairs) == 2
        assert all(p.regime is None for p in pairs)

    def test_short_direction_inverts_predicted_probability(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        engine = build_default_engine()
        report = engine.evaluate(
            [build_news_evidence(relevance=0.0, sentiment_aligned_with_signal=True)],
            prior_probability=0.7,
        )
        append_bayes_report(
            decision_id="dec_short_001",
            symbol="ETH/USDT",
            direction="short",
            report=report,
            path=audit,
        )
        pairs = pairs_from_bayes_audit(
            bayes_audit_path=audit,
            outcomes={"dec_short_001": 1},
        )
        assert len(pairs) == 1
        # posterior was 0.7; for short, predicted_probability for the SHORT
        # signal "winning" is 1 - 0.7 = 0.3
        assert pairs[0].predicted_probability == pytest.approx(0.3, abs=1e-6)


# ─── Calibration End-to-End mit Loader ───────────────────────────────────────


def test_loader_to_calibration_pipeline_overconfident(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    ids = _seed_audit(audit, n=40, posterior_for_long=0.9)
    # Tatsächliche Hit-Rate nur 50% → starkes Overconfidence-Signal
    outcomes = {decision_id: i % 2 for i, decision_id in enumerate(ids)}
    pairs = pairs_from_bayes_audit(bayes_audit_path=audit, outcomes=outcomes)
    report = compute_calibration(pairs)
    assert report.n_pairs == 40
    assert report.expected_calibration_error is not None
    assert report.expected_calibration_error >= 0.30
    assert any("over" in n.lower() or "Re-Calibration" in n for n in report.notes)


# ─── Dashboard-Endpoint ──────────────────────────────────────────────────────


@pytest.fixture()
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "bayes_confidence_audit.jsonl"


def test_calibration_endpoint_empty_returns_zero_report(audit_path: Path) -> None:
    app = _make_app()
    with patch.object(dashboard_mod, "_BAYES_AUDIT", audit_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/calibration")
    assert r.status_code == 200
    body = r.json()
    assert body["n_pairs"] == 0
    assert body["brier_score"] is None
    assert body["bins"] == []
    assert body["sample_sufficient"] is False


def test_calibration_endpoint_with_outcomes_returns_metrics(audit_path: Path) -> None:
    ids = _seed_audit(audit_path, n=40, posterior_for_long=0.9)
    outcomes = {decision_id: i % 2 for i, decision_id in enumerate(ids)}
    app = _make_app()
    with (
        patch.object(dashboard_mod, "_BAYES_AUDIT", audit_path),
        patch.object(dashboard_mod, "_BAYES_OUTCOME_MAP", outcomes),
    ):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/calibration")
    assert r.status_code == 200
    body = r.json()
    assert body["n_pairs"] == 40
    assert body["brier_score"] is not None
    assert body["expected_calibration_error"] is not None
    assert body["sample_sufficient"] is True
    assert len(body["bins"]) == 10
    assert body["outcome_map_size"] == 40


def test_calibration_endpoint_clamps_n_bins(audit_path: Path) -> None:
    app = _make_app()
    with patch.object(dashboard_mod, "_BAYES_AUDIT", audit_path):
        with TestClient(app) as client:
            r_low = client.get("/dashboard/api/calibration?n_bins=1")
            r_high = client.get("/dashboard/api/calibration?n_bins=999")
    # n_bins gets clamped — endpoint stays 200, no errors
    assert r_low.status_code == 200
    assert r_high.status_code == 200


def test_calibration_endpoint_no_store_cache_header(audit_path: Path) -> None:
    app = _make_app()
    with patch.object(dashboard_mod, "_BAYES_AUDIT", audit_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/calibration")
    assert "no-store" in r.headers.get("cache-control", "")
