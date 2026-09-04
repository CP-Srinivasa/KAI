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


# ── Release-Modus (Immutable-Release-Cutover 2026-09-04) ─────────────────────
#
# Befund: nach dem Cutover lief kai-server aus /home/ubuntu/releases/<SHA>/ —
# kein .git dort, /health meldete runtime_commit=null, checkout_commit=null,
# drift_commits=null. Der Waechter im Checkout haette zudem den WANDERNDEN
# Checkout als Referenz genommen, obwohl der Daemon an das aktive Release
# gebunden ist. Referenz ist ab jetzt das aktive Release (``current``), der
# Checkout nur noch ohne Release.

SHA_A = "a" * 40
SHA_B = "b" * 40


def _release_tree(root: Path, sha: str, *, manifest_sha: str | None = None) -> Path:
    """Ein Release-Baum, wie ``pi_make_release.sh`` ihn legt: kein .git, aber release.json."""
    rel = root / "releases" / sha
    rel.mkdir(parents=True)
    (rel / "requirements.lock").write_text("a==1\n", encoding="utf-8")
    (rel / "release.json").write_text(
        json.dumps(
            {
                "schema": "kai_release/v1",
                "repo_sha": sha if manifest_sha is None else manifest_sha,
                "release_path": str(rel),
                "release_tree_sha256": "0" * 64,
                "requirements_lock_sha256": "0" * 64,
                "python_version": "3.12.0",
                "created_at_utc": NOW.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return rel


def _activate(root: Path, rel: Path) -> Path:
    """``current`` auf ``rel`` legen — Symlink; ohne Symlink-Recht eine Kopie des Manifests."""
    link = root / "current"
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    try:
        link.symlink_to(rel, target_is_directory=True)
    except OSError:
        link.mkdir()
        shutil.copy(rel / "release.json", link / "release.json")
    return link


def test_capture_in_release_tree_reads_manifest_commit(tmp_path: Path) -> None:
    rel = _release_tree(tmp_path, SHA_A)
    identity = ri.capture_runtime_identity(rel, now=NOW, pid=1)
    assert identity.runtime_commit == SHA_A
    assert identity.runtime_source == "release"
    assert identity.lock_sha256_at_start is not None


@requires_git
def test_capture_in_checkout_reports_source_checkout(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    identity = ri.capture_runtime_identity(repo, now=NOW, pid=1)
    assert identity.runtime_commit == _git(repo, "rev-parse", "HEAD")
    assert identity.runtime_source == "checkout"


def test_manifest_with_invalid_sha_is_ignored(tmp_path: Path) -> None:
    rel = _release_tree(tmp_path, SHA_A, manifest_sha="not-a-sha")
    identity = ri.capture_runtime_identity(rel, now=NOW, pid=1)
    assert identity.runtime_commit is None
    assert identity.runtime_source is None


def test_capture_outside_any_tree_has_no_source(tmp_path: Path) -> None:
    identity = ri.capture_runtime_identity(tmp_path, now=NOW, pid=1)
    assert identity.runtime_commit is None
    assert identity.runtime_source is None


def test_drift_report_in_release_uses_active_release_as_reference(tmp_path: Path) -> None:
    rel_a = _release_tree(tmp_path, SHA_A)
    rel_b = _release_tree(tmp_path, SHA_B)
    _activate(tmp_path, rel_a)
    identity = ri.capture_runtime_identity(rel_a, now=NOW, pid=1)

    report = ri.drift_report(identity, rel_a, now=NOW)
    assert report["runtime_commit"] == SHA_A
    assert report["checkout_commit"] == SHA_A
    assert report["reference_source"] == "release"
    assert report["runtime_source"] == "release"
    assert report["drift_commits"] == 0

    # Neues Release aktiviert, Prozess nicht neu gestartet: Referenz wandert,
    # die Anzahl ist ohne git nicht zaehlbar — aber die Abweichung ist belegt.
    _activate(tmp_path, rel_b)
    report = ri.drift_report(identity, rel_a, now=NOW)
    assert report["runtime_commit"] == SHA_A
    assert report["checkout_commit"] == SHA_B
    assert report["drift_commits"] is None


@requires_git
def test_drift_report_from_checkout_prefers_active_release(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, commits=2)
    c2 = _git(repo, "rev-parse", "HEAD")
    c1 = _git(repo, "rev-parse", "HEAD~1")
    rel_1 = _release_tree(tmp_path, c1)
    _activate(tmp_path, rel_1)
    identity = ri.capture_runtime_identity(rel_1, now=NOW, pid=1)
    assert identity.runtime_commit == c1

    # Der Waechter laeuft im Checkout (HEAD=c2), der Daemon auf Release c1 —
    # das ist KEIN Drift, solange c1 das aktive Release ist.
    report = ri.drift_report(identity, repo, now=NOW)
    assert report["checkout_commit"] == c1
    assert report["reference_source"] == "release"
    assert report["drift_commits"] == 0

    # Release c2 aktiviert, Daemon weiter auf c1: Drift 1, gezaehlt ueber git im Checkout.
    _activate(tmp_path, _release_tree(tmp_path, c2))
    report = ri.drift_report(identity, repo, now=NOW)
    assert report["checkout_commit"] == c2
    assert report["drift_commits"] == 1


@requires_git
def test_drift_report_without_release_falls_back_to_checkout(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    identity = ri.capture_runtime_identity(repo, now=NOW, pid=1)
    report = ri.drift_report(identity, repo, now=NOW)
    assert report["reference_source"] == "checkout"
    assert report["checkout_commit"] == identity.runtime_commit
    assert report["drift_commits"] == 0


def test_reference_stable_for_s_reads_current_link_age(tmp_path: Path) -> None:
    rel = _release_tree(tmp_path, SHA_A)
    link = _activate(tmp_path, rel)
    stable = ri.reference_stable_for_s(rel, now=datetime.now(UTC) + timedelta(hours=3))
    assert stable is not None
    assert stable == pytest.approx(3 * 3600.0, abs=120.0)
    assert link.exists()


def test_reference_stable_for_s_without_release_is_checkout_stability(tmp_path: Path) -> None:
    assert ri.reference_stable_for_s(tmp_path, now=NOW) is None


def test_artifact_roundtrip_keeps_runtime_source(tmp_path: Path) -> None:
    identity = ri.RuntimeIdentity(
        schema=ri.SCHEMA,
        runtime_commit=SHA_A,
        started_at_utc=NOW.isoformat(),
        lock_sha256_at_start="d" * 64,
        pid=99,
        runtime_source="release",
    )
    path = tmp_path / "runtime_identity.json"
    ri.write_runtime_identity_artifact(identity, path)
    assert ri.read_runtime_identity_artifact(path) == identity


def test_evaluate_unequal_reference_without_count_is_a_finding() -> None:
    report = _report(drift_commits=None, runtime_commit=SHA_A, checkout_commit=SHA_B)
    assert ri.evaluate_runtime_drift(report, checkout_stable_for_s=600) == []
    warn = ri.evaluate_runtime_drift(report, checkout_stable_for_s=2 * 3600)
    assert [f.severity for f in warn] == ["warning"]
    assert "aaaaaaaa" in warn[0].message
    assert "bbbbbbbb" in warn[0].message
    crit = ri.evaluate_runtime_drift(report, checkout_stable_for_s=3 * 86400)
    assert [f.severity for f in crit] == ["critical"]


def test_evaluate_equal_reference_without_count_is_no_drift_finding() -> None:
    report = _report(drift_commits=0, runtime_commit=SHA_A, checkout_commit=SHA_A)
    assert ri.evaluate_runtime_drift(report, checkout_stable_for_s=None) == []
