"""STAB-02 — der Health-Check meldet einen Server, der hinter seinem Checkout läuft.

Das Artefakt `artifacts/runtime/runtime_identity.json` schreibt der Server beim
Start. Der Health-Check (Timer, eigener Prozess) liest es und vergleicht mit dem
Checkout. Monitoring-Lehre 18.08.: ein gesunder Ausgang beweist keinen aktuellen
Code — deshalb ist der Abstand selbst der Befund.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.alerts import health_check as hc
from app.core import runtime_identity as ri

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _probe_on(monkeypatch) -> None:
    # conftest schaltet die Probe global ab — hier wird sie selbst getestet.
    monkeypatch.delenv("KAI_RUNTIME_IDENTITY_PROBE", raising=False)


def _write_artifact(adir: Path, commit: str) -> None:
    path = adir / "runtime" / "runtime_identity.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema": ri.SCHEMA,
                "runtime_commit": commit,
                "started_at_utc": "2026-08-18T20:30:09+00:00",
                "lock_sha256_at_start": None,
                "pid": 1,
            }
        ),
        encoding="utf-8",
    )


def test_missing_artifact_on_pi_is_a_warning(tmp_path: Path) -> None:
    issues = hc._check_runtime_identity(tmp_path, NOW, runs_on_pi=True)
    assert [i.severity for i in issues] == ["warning"]
    assert issues[0].component == "runtime_identity"


def test_missing_artifact_off_pi_is_silent(tmp_path: Path) -> None:
    assert hc._check_runtime_identity(tmp_path, NOW, runs_on_pi=False) == []


def test_drift_older_than_grace_is_reported(tmp_path: Path, monkeypatch) -> None:
    _write_artifact(tmp_path, "7" * 40)
    monkeypatch.setattr(
        hc,
        "drift_report",
        lambda identity, repo_dir, *, now: {
            "runtime_commit": identity.runtime_commit,
            "checkout_commit": "5" * 40,
            "drift_commits": 23,
            "started_at_utc": identity.started_at_utc,
            "uptime_s": 7 * 86400.0,
            "lock_changed": False,
        },
    )
    monkeypatch.setattr(hc, "checkout_stable_for_s", lambda repo_dir, *, now: 3 * 86400.0)
    issues = hc._check_runtime_identity(tmp_path, NOW, runs_on_pi=True)
    assert [i.severity for i in issues] == ["critical"]
    assert "23 Commits" in issues[0].message


def test_no_drift_is_no_finding(tmp_path: Path, monkeypatch) -> None:
    _write_artifact(tmp_path, "7" * 40)
    monkeypatch.setattr(
        hc,
        "drift_report",
        lambda identity, repo_dir, *, now: {
            "runtime_commit": identity.runtime_commit,
            "checkout_commit": identity.runtime_commit,
            "drift_commits": 0,
            "started_at_utc": identity.started_at_utc,
            "uptime_s": 100.0,
            "lock_changed": False,
        },
    )
    monkeypatch.setattr(hc, "checkout_stable_for_s", lambda repo_dir, *, now: 100.0)
    assert hc._check_runtime_identity(tmp_path, NOW, runs_on_pi=True) == []


def test_runtime_identity_check_is_wired_into_the_report(monkeypatch, tmp_path: Path) -> None:
    called: list[bool] = []

    def fake(adir: Path, now: datetime, *, runs_on_pi: bool) -> list[hc.HealthIssue]:
        called.append(True)
        return []

    monkeypatch.setattr(hc, "_check_runtime_identity", fake)
    hc.run_health_check_report(artifacts_dir=tmp_path, now=NOW)
    assert called, "_check_runtime_identity muss Teil des Reports sein"
