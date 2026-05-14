"""Audit-Sidecar für Bayesian-Confidence-Reports — JSONL-Roundtrip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.signals.bayes_journal import (
    SCHEMA_VERSION,
    BayesAuditEntry,
    append_bayes_report,
    load_bayes_reports,
)
from app.signals.bayesian_confidence import build_default_engine, build_news_evidence


def _make_report(relevance: float = 0.8):
    engine = build_default_engine()
    return engine.evaluate(
        [build_news_evidence(relevance=relevance, sentiment_aligned_with_signal=True)],
        prior_probability=0.5,
    )


def test_append_creates_file_and_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "sub" / "audit.jsonl"
    report = _make_report()
    written = append_bayes_report(
        decision_id="dec_001",
        symbol="BTC/USDT",
        direction="long",
        report=report,
        path=target,
    )
    assert written == target
    assert target.exists()


def test_roundtrip_preserves_payload(tmp_path: Path) -> None:
    target = tmp_path / "audit.jsonl"
    report = _make_report(relevance=0.9)
    append_bayes_report(
        decision_id="dec_round",
        symbol="ETH/USDT",
        direction="short",
        report=report,
        path=target,
    )
    entries = load_bayes_reports(target)
    assert len(entries) == 1
    e = entries[0]
    assert e.schema_version == SCHEMA_VERSION
    assert e.decision_id == "dec_round"
    assert e.symbol == "ETH/USDT"
    assert e.direction == "short"
    # Posterior aus dem Report kommt 1:1 durch
    assert e.report["posterior_probability"] == report.posterior_probability
    assert "increased" in e.report
    assert "decreased" in e.report


def test_appends_are_additive(tmp_path: Path) -> None:
    target = tmp_path / "audit.jsonl"
    for i in range(3):
        append_bayes_report(
            decision_id=f"dec_{i:03d}",
            symbol="BTC/USDT",
            direction="long",
            report=_make_report(relevance=0.5 + 0.1 * i),
            path=target,
        )
    entries = load_bayes_reports(target)
    assert [e.decision_id for e in entries] == ["dec_000", "dec_001", "dec_002"]


def test_load_skips_malformed_rows_without_raising(tmp_path: Path) -> None:
    target = tmp_path / "audit.jsonl"
    append_bayes_report(
        decision_id="dec_ok",
        symbol="BTC/USDT",
        direction="long",
        report=_make_report(),
        path=target,
    )
    # Garbage-Zeile dazwischenwerfen
    with target.open("a", encoding="utf-8") as fh:
        fh.write("{not-valid-json}\n")
        fh.write('{"missing_required_fields": true}\n')
    entries = load_bayes_reports(target)
    assert len(entries) == 1
    assert entries[0].decision_id == "dec_ok"


def test_missing_file_returns_empty_list(tmp_path: Path) -> None:
    assert load_bayes_reports(tmp_path / "does_not_exist.jsonl") == []


def test_audit_failure_returns_none_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pfad ist eine Datei → mkdir(parents=True) wirft → Funktion frisst Fehler
    bad = Path("/proc/self/cmdline/audit.jsonl") if Path("/proc").exists() else Path("\0bad")
    written = append_bayes_report(
        decision_id="dec_fail",
        symbol="BTC/USDT",
        direction="long",
        report=_make_report(),
        path=bad,
    )
    assert written is None


def test_lines_are_valid_jsonl(tmp_path: Path) -> None:
    target = tmp_path / "audit.jsonl"
    append_bayes_report(
        decision_id="dec_jsonl",
        symbol="BTC/USDT",
        direction="long",
        report=_make_report(),
        path=target,
    )
    raw = target.read_text(encoding="utf-8").strip()
    payload = json.loads(raw)
    BayesAuditEntry.model_validate(payload)
