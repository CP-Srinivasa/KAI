"""STAB-02 — Runtime-Identität: welcher Commit läuft im Prozess, welcher liegt im Checkout?

Hintergrund (25.08.2026, live): `kai-server` lief seit dem 18.08. auf `79e6fca7`,
der Checkout stand 23 Commits weiter, die Mainline 27 — und kein Signal zeigte
das an. `/health` kannte nur `{"status":"ok","version":"0.1.0"}`.

Die Tests hier prüfen die reinen Bausteine ohne laufenden Server: Commit-Ermittlung
(mit und ohne `git`-Aufruf, auch in Worktrees), Drift-Zählung, Lock-Hash,
Artefakt-Roundtrip und die Bewertungsfunktion, die aus Zahlen einen Befund macht.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core import runtime_identity as ri

_GIT = shutil.which("git")
requires_git = pytest.mark.skipif(_GIT is None, reason="git not available")


def _git(repo: Path, *args: str) -> str:
    assert _GIT is not None
    proc = subprocess.run(  # noqa: S603
        [_GIT, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(repo),
            "PATH": str(Path(_GIT).parent),
        },
    )
    return proc.stdout.strip()


def _make_repo(tmp_path: Path, commits: int = 1) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "requirements.lock").write_text("a==1\n", encoding="utf-8")
    for i in range(commits):
        (repo / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"c{i}")
    return repo


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


# ── Commit-Ermittlung ─────────────────────────────────────────────────────────


@requires_git
def test_read_checkout_commit_without_git_matches_rev_parse(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    expected = _git(repo, "rev-parse", "HEAD")

    def forbidden_run(*_a: object, **_k: object) -> None:
        raise AssertionError("cheap path must not spawn git")

    assert ri.read_checkout_commit(repo, run=forbidden_run) == expected  # type: ignore[arg-type]


@requires_git
def test_read_checkout_commit_in_linked_worktree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, commits=2)
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt), "HEAD~1")
    expected = _git(wt, "rev-parse", "HEAD")
    assert (wt / ".git").is_file(), "linked worktree keeps a .git FILE (gitdir: …)"
    assert ri.read_checkout_commit(wt) == expected
    assert ri.read_checkout_commit(wt) != _git(repo, "rev-parse", "HEAD")


@requires_git
def test_read_checkout_commit_from_packed_refs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    expected = _git(repo, "rev-parse", "HEAD")
    _git(repo, "pack-refs", "--all")
    assert not (repo / ".git" / "refs" / "heads" / "main").exists()
    assert ri.read_checkout_commit(repo) == expected


def test_read_checkout_commit_outside_repo_is_none(tmp_path: Path) -> None:
    def failing_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="fatal")

    assert ri.read_checkout_commit(tmp_path, run=failing_run) is None


# ── Drift ─────────────────────────────────────────────────────────────────────


@requires_git
def test_count_commits_between_counts_only_forward(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, commits=4)
    head = _git(repo, "rev-parse", "HEAD")
    old = _git(repo, "rev-parse", "HEAD~3")
    assert ri.count_commits_between(old, head, repo) == 3
    assert ri.count_commits_between(head, head, repo) == 0


def test_count_commits_between_is_none_when_git_fails(tmp_path: Path) -> None:
    def failing_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="")

    assert ri.count_commits_between("a" * 40, "b" * 40, tmp_path, run=failing_run) is None


# ── Capture + Report ──────────────────────────────────────────────────────────


@requires_git
def test_capture_and_drift_report_roundtrip(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, commits=2)
    start_commit = _git(repo, "rev-parse", "HEAD")
    identity = ri.capture_runtime_identity(repo, now=NOW, pid=4711)
    assert identity.runtime_commit == start_commit
    assert identity.started_at_utc == "2026-08-25T12:00:00+00:00"
    assert identity.pid == 4711
    assert identity.lock_sha256_at_start is not None

    # Checkout zieht weiter, Prozess bleibt stehen → Drift 1, Lock unverändert.
    (repo / "g.txt").write_text("g", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c-next")
    report = ri.drift_report(identity, repo, now=NOW + timedelta(hours=2))
    assert report["runtime_commit"] == start_commit
    assert report["checkout_commit"] == _git(repo, "rev-parse", "HEAD")
    assert report["drift_commits"] == 1
    assert report["uptime_s"] == pytest.approx(7200.0)
    assert report["lock_changed"] is False

    # Lock ändert sich → sichtbar, ohne Neustart.
    (repo / "requirements.lock").write_text("a==2\n", encoding="utf-8")
    assert ri.drift_report(identity, repo, now=NOW)["lock_changed"] is True


def test_drift_report_without_git_is_fail_soft(tmp_path: Path) -> None:
    identity = ri.RuntimeIdentity(
        schema=ri.SCHEMA,
        runtime_commit=None,
        started_at_utc=NOW.isoformat(),
        lock_sha256_at_start=None,
        pid=1,
    )
    report = ri.drift_report(identity, tmp_path, now=NOW + timedelta(seconds=30))
    assert report["drift_commits"] is None
    assert report["lock_changed"] is None
    assert report["uptime_s"] == pytest.approx(30.0)


def test_artifact_roundtrip_is_atomic_and_validated(tmp_path: Path) -> None:
    identity = ri.RuntimeIdentity(
        schema=ri.SCHEMA,
        runtime_commit="c" * 40,
        started_at_utc=NOW.isoformat(),
        lock_sha256_at_start="d" * 64,
        pid=99,
    )
    path = tmp_path / "artifacts" / "runtime" / "runtime_identity.json"
    ri.write_runtime_identity_artifact(identity, path)
    assert path.is_file()
    assert not list(path.parent.glob("*.tmp*")), "kein halbes Artefakt liegen lassen"
    assert ri.read_runtime_identity_artifact(path) == identity

    path.write_text(json.dumps({"schema": "other/v9"}), encoding="utf-8")
    assert ri.read_runtime_identity_artifact(path) is None
    assert ri.read_runtime_identity_artifact(tmp_path / "missing.json") is None


# ── Bewertung (rein) ──────────────────────────────────────────────────────────


def _report(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "runtime_commit": "a" * 40,
        "checkout_commit": "b" * 40,
        "drift_commits": 23,
        "started_at_utc": NOW.isoformat(),
        "uptime_s": 7 * 86400.0,
        "lock_changed": False,
    }
    base.update(over)
    return base


def test_evaluate_no_drift_is_no_finding() -> None:
    assert ri.evaluate_runtime_drift(_report(drift_commits=0), checkout_stable_for_s=99999) == []


def test_evaluate_fresh_drift_is_grace_not_finding() -> None:
    # Direkt nach einem Pull ist Drift normal — der Deploy ist noch unterwegs.
    assert ri.evaluate_runtime_drift(_report(drift_commits=3), checkout_stable_for_s=600) == []


def test_evaluate_drift_older_than_an_hour_is_warning() -> None:
    findings = ri.evaluate_runtime_drift(_report(drift_commits=3), checkout_stable_for_s=2 * 3600)
    assert [f.severity for f in findings] == ["warning"]
    assert "3 Commits" in findings[0].message
    assert "aaaaaaaa" in findings[0].message and "bbbbbbbb" in findings[0].message


def test_evaluate_drift_older_than_a_day_is_critical() -> None:
    findings = ri.evaluate_runtime_drift(_report(), checkout_stable_for_s=3 * 86400)
    assert [f.severity for f in findings] == ["critical"]


def test_evaluate_unknown_stability_uses_uptime_as_floor() -> None:
    # Kein Ref-mtime messbar: der Prozess läuft seit 7 Tagen, der Drift ist real.
    findings = ri.evaluate_runtime_drift(_report(), checkout_stable_for_s=None)
    assert [f.severity for f in findings] == ["critical"]


def test_evaluate_lock_change_is_its_own_warning() -> None:
    findings = ri.evaluate_runtime_drift(
        _report(drift_commits=0, lock_changed=True), checkout_stable_for_s=99999
    )
    assert [f.severity for f in findings] == ["warning"]
    assert "pip install -e ." in findings[0].message


def test_evaluate_unmeasurable_drift_is_not_zero() -> None:
    findings = ri.evaluate_runtime_drift(
        _report(drift_commits=None, checkout_commit=None), checkout_stable_for_s=None
    )
    assert [f.severity for f in findings] == ["warning"]
    assert "nicht messbar" in findings[0].message
