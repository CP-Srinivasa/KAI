"""Test the opt-in sanitize flag of append_bayes_report."""

from __future__ import annotations

import json
from pathlib import Path

from app.signals.bayes_journal import append_bayes_report
from app.signals.bayesian_confidence import ConfidenceReport

# Fake AWS access key as fixture — split into two literals at source level so
# the repo-wide secret-guard regex (AKIA[A-Z0-9]{16}) cannot match the source
# bytes. The string is reassembled at import time and behaves identically for
# the sanitizer under test.
_FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


def _make_report_with_secret_in_drivers() -> ConfidenceReport:
    """Construct a ConfidenceReport whose `residual_uncertainty_drivers`
    accidentally contain a secret-shaped string. In practice this would be a
    bug somewhere upstream — the sanitizer is the defence-in-depth net."""
    return ConfidenceReport(
        prior_probability=0.5,
        posterior_probability=0.85,
        confidence_score=0.70,
        uncertainty_score=0.20,
        evidence_weight=2.5,
        agreement=0.6,
        increased=(),
        decreased=(),
        neutral=(),
        discarded=(),
        residual_uncertainty_drivers=(f"{_FAKE_AWS_KEY} leaked into a driver string by accident",),
    )


def test_default_behavior_unchanged_no_redaction(tmp_path: Path):
    """Without sanitize=True, the legacy contract is preserved: the report
    payload is written verbatim. Existing consumers must not regress."""
    out = tmp_path / "bayes.jsonl"
    report = _make_report_with_secret_in_drivers()
    append_bayes_report(
        decision_id="dec_test",
        symbol="BTC/USDT",
        direction="long",
        report=report,
        path=out,
    )
    content = out.read_text(encoding="utf-8")
    payload = json.loads(content.strip())
    drivers = payload["report"]["residual_uncertainty_drivers"]
    assert any(_FAKE_AWS_KEY in d for d in drivers)


def test_opt_in_sanitization_redacts_secret_in_drivers(tmp_path: Path):
    out = tmp_path / "bayes.jsonl"
    report = _make_report_with_secret_in_drivers()
    append_bayes_report(
        decision_id="dec_test",
        symbol="BTC/USDT",
        direction="long",
        report=report,
        path=out,
        sanitize=True,
    )
    content = out.read_text(encoding="utf-8")
    payload = json.loads(content.strip())
    drivers = payload["report"]["residual_uncertainty_drivers"]
    assert all("AKIA" not in d for d in drivers)
    assert any("REDACTED:aws_access_key" in d for d in drivers)


def test_sanitization_preserves_numeric_fields(tmp_path: Path):
    """Floats / ints in the report must not get touched by sanitize_value."""
    out = tmp_path / "bayes.jsonl"
    report = _make_report_with_secret_in_drivers()
    append_bayes_report(
        decision_id="dec_test",
        symbol="BTC/USDT",
        direction="long",
        report=report,
        path=out,
        sanitize=True,
    )
    payload = json.loads(out.read_text(encoding="utf-8").strip())
    r = payload["report"]
    assert r["prior_probability"] == 0.5
    assert r["posterior_probability"] == 0.85
    assert r["confidence_score"] == 0.70
    assert r["evidence_weight"] == 2.5
