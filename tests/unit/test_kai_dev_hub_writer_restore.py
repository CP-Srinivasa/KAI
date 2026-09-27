"""Audit 27.09., point 4: writer lease per workspace, restore, "continue work".

Two clients must never write the same worktree unnoticed; a crashed client
must not block the next one; a snapshot must come back byte-exact in a NEW
worktree; and one status call must say in plain words how to continue.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
HUB_PATH = REPO / "scripts" / "kai_dev_hub.py"
sys.path.insert(0, str(HUB_PATH.parent))
SPEC = importlib.util.spec_from_file_location("kai_dev_hub_writer_restore", HUB_PATH)
assert SPEC and SPEC.loader
hub = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hub)

# A stand-in client that keeps running like OpenCode or Hermes would.
SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture()
def kai_repo(tmp_path: Path) -> Path:
    # Repository and hub state side by side, so the state never shows up as
    # untracked work inside the repository.
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("claude\n", encoding="utf-8")
    (repo / "docs" / "AI_HANDOFF.md").write_text("handoff\n", encoding="utf-8")
    (repo / "blob.bin").write_bytes(bytes(range(256)))
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _manage(worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(
        hub.workflow,
        "list_sessions",
        lambda _state: [
            {
                "session_id": "test-session",
                "created_at": "2026-09-27T00:00:00+00:00",
                "task": "KAI review",
                "worktree": str(worktree),
                "branch": _git(worktree, "branch", "--show-current"),
                "base_sha": _git(worktree, "rev-parse", "HEAD"),
            }
        ],
    )


@pytest.fixture()
def clients(kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Managed workspace whose OpenCode/Hermes starts spawn a real stand-in process."""
    _manage(kai_repo, tmp_path, monkeypatch)
    monkeypatch.setattr(hub, "ensure_ollama", lambda: None)
    monkeypatch.setattr(hub, "_ollama_models", lambda: {hub.LOCAL_MODEL})
    monkeypatch.setattr(
        hub,
        "_command",
        lambda name: {"opencode.cmd": "opencode.cmd", "hermes": "hermes.exe"}.get(name),
    )
    monkeypatch.setattr(hub, "_copy_to_clipboard", lambda _text: None)
    real_popen = hub.subprocess.Popen
    spawned: list[subprocess.Popen[bytes]] = []

    def start(args: list[str], **kwargs: Any) -> Any:
        if args[0] in {"opencode.cmd", "hermes.exe"}:
            process = real_popen(
                SLEEPER,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            spawned.append(process)
            return process
        return real_popen(args, **kwargs)

    monkeypatch.setattr(hub.subprocess, "Popen", start)
    yield SimpleNamespace(repo=kai_repo, spawned=spawned)
    for process in spawned:
        if process.poll() is None:
            process.kill()
            process.wait()


def _ledger_rows() -> list[dict[str, Any]]:
    ledger, _ = hub._handoff_paths()
    if not ledger.is_file():
        return []
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]


# --- 1. Writer lease -----------------------------------------------------------


def test_second_client_on_the_same_worktree_is_refused_and_names_the_writer(
    clients: Any,
) -> None:
    hub.launch_opencode(clients.repo, "local")
    first = clients.spawned[0]

    lease = hub.active_writer(clients.repo)
    assert lease is not None
    assert (lease["client"], lease["pid"]) == ("opencode-local", first.pid)
    with pytest.raises(hub.WriterBusyError, match=rf"opencode-local.*PID {first.pid}"):
        hub.launch_hermes(clients.repo)
    with pytest.raises(hub.WriterBusyError):
        hub.launch_opencode(clients.repo, "local")
    assert len(clients.spawned) == 1, "kein zweiter Client gestartet"
    assert "opencode-local" in hub.status(clients.repo)["active_writer"]


def test_crashed_client_is_taken_over_without_blocking_and_logged(clients: Any) -> None:
    hub.launch_opencode(clients.repo, "local")
    crashed = clients.spawned[0]
    crashed.kill()
    crashed.wait()

    assert hub.active_writer(clients.repo) is None
    hub.launch_hermes(clients.repo)

    lease = hub.active_writer(clients.repo)
    assert lease is not None
    assert (lease["client"], lease["pid"]) == ("hermes-local", clients.spawned[1].pid)
    events = [
        json.loads(line)
        for line in (hub.STATE_ROOT / "writers" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event"] == "dead_lease_taken_over"
    assert events[-1]["previous"]["pid"] == crashed.pid
    # A dead lease is housekeeping, not an operator decision: no ledger entry.
    assert _ledger_rows() == []


def test_explicit_take_over_is_recorded_in_the_ledger(clients: Any) -> None:
    hub.launch_opencode(clients.repo, "local")
    first = clients.spawned[0]

    hub.launch_hermes(clients.repo, take_over=True)

    event = _ledger_rows()[-1]
    assert event["event"] == "writer_takeover"
    assert event["previous_writer"]["pid"] == first.pid
    assert event["new_client"] == "hermes-local"
    assert hub.verify_handoffs()[0] is True
    lease = hub.active_writer(clients.repo)
    assert lease is not None and lease["pid"] == clients.spawned[1].pid
    # Taking over records the decision; it does not kill the old client.
    assert first.poll() is None
    assert hub._parser().parse_args(["open", "hermes-local", "--take-over"]).take_over is True


# A second hub process starting a client on the same worktree. Its clock is
# read between the lease check and the lease write, so without an atomic
# check-and-write both processes would pass the check and both start.
_RACE_CLIENT = """
import importlib.util, subprocess, sys, time
from datetime import datetime
from pathlib import Path

hub_path, worktree, signals = (Path(arg) for arg in sys.argv[1:4])
name = sys.argv[4]
sys.path.insert(0, str(hub_path.parent))
spec = importlib.util.spec_from_file_location("kai_dev_hub", hub_path)
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)


class SlowClock(datetime):
    @classmethod
    def now(cls, tz=None):
        time.sleep(0.4)
        return datetime.now(tz)


hub.datetime = SlowClock
(signals / f"ready-{name}").touch()
while not (signals / "go").exists():
    time.sleep(0.001)
try:
    process = hub._start_writer(
        worktree,
        f"client-{name}",
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
except hub.WriterBusyError as exc:
    print("BUSY", exc)
else:
    print("STARTED", process.pid)
"""


def test_two_hub_processes_cannot_both_start_a_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr(hub, "STATE_ROOT", state)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    signals = tmp_path / "signals"
    signals.mkdir()
    env = {**os.environ, "KAI_DEV_HUB_HOME": str(state)}
    peers = [
        subprocess.Popen(
            [sys.executable, "-B", "-c", _RACE_CLIENT, str(HUB_PATH), str(worktree), str(signals)]
            + [n],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for n in ("a", "b")
    ]
    started: list[int] = []
    try:
        deadline = time.monotonic() + 60
        while not all((signals / f"ready-{n}").exists() for n in ("a", "b")):
            assert all(peer.poll() is None for peer in peers), "Peer vorzeitig beendet"
            assert time.monotonic() < deadline, "Peers nicht bereit"
            time.sleep(0.01)
        (signals / "go").touch()
        outputs = [peer.communicate(timeout=60) for peer in peers]
        lines = [out.strip() for out, _err in outputs]
        started = [int(line.split()[1]) for line in lines if line.startswith("STARTED")]
        assert sorted(line.split()[0] for line in lines) == ["BUSY", "STARTED"], outputs
        lease = hub.active_writer(worktree)
        assert lease is not None and lease["pid"] == started[0]
        busy = next(line for line in lines if line.startswith("BUSY"))
        assert f"PID {started[0]}" in busy
    finally:
        (signals / "go").touch()
        for peer in peers:
            if peer.poll() is None:
                peer.kill()
        for pid in started:
            os.kill(pid, signal.SIGTERM)


# --- 2. Restore ------------------------------------------------------------------


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


@pytest.fixture()
def work(kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real task worktree (checked out like the hub's) with unsaved work in it."""
    original = tmp_path / "orig"
    _git(kai_repo, "worktree", "add", str(original), "-b", "codex/dev-task-orig")
    _manage(original, tmp_path, monkeypatch)
    # Mixed line endings on purpose: an LF edit inside a CRLF checkout does
    # not survive a patch round trip under core.autocrlf=true.
    (original / "AGENTS.md").write_bytes(b"rules\nsecond line written with LF\n")
    (original / "blob.bin").write_bytes(bytes(reversed(range(256))))
    (original / "CLAUDE.md").unlink()
    (original / "notes").mkdir()
    (original / "notes" / "todo.txt").write_bytes(b"open item\r\nnext step\r\n")
    receipt = hub.create_handoff(
        original,
        from_agent="OpenCode",
        to_agent="Hermes",
        task="Restore proof",
        completed="Edited three files",
        open_items="Continue elsewhere",
        assumptions="None",
        next_action="Resume from snapshot",
        tests="not run",
    )
    created: list[Path] = []
    yield SimpleNamespace(repo=kai_repo, original=original, receipt=receipt, created=created)
    for path in [*created, original]:
        subprocess.run(
            ["git", "-C", str(kai_repo), "worktree", "remove", "--force", str(path)],
            capture_output=True,
            check=False,
        )


def test_restore_dry_run_verifies_hashes_and_changes_nothing(work: Any) -> None:
    before = _git(work.repo, "worktree", "list", "--porcelain")

    plan = hub.restore(work.repo, str(work.receipt["handoff_id"]))

    assert plan["applied"] is False
    assert plan["verified"] is True
    assert plan["byte_exact"] is True
    assert plan["base_head"] == _git(work.original, "rev-parse", "HEAD")
    for name in ("AGENTS.md", "blob.bin", "CLAUDE.md"):
        assert name in plan["tracked_stat"]
    assert plan["untracked"] == ["notes/todo.txt"]
    assert not Path(plan["target_worktree"]).exists()
    assert _git(work.repo, "worktree", "list", "--porcelain") == before
    assert "TROCKENLAUF" in hub.render_restore(plan)


def test_restore_resumes_work_byte_exact_in_a_new_worktree(work: Any) -> None:
    original_tree = _tree(work.original)

    result = hub.restore(work.repo, str(work.receipt["handoff_id"]), apply=True)
    target = Path(result["target_worktree"])
    work.created.append(target)

    assert result["applied"] is True
    assert target.resolve() != work.original.resolve()
    assert _git(target, "branch", "--show-current") == result["target_branch"]
    assert _tree(target) == original_tree, "kein Byte der Arbeit verloren"
    assert _tree(work.original) == original_tree, "aktiver Arbeitsbereich unberührt"


def test_restore_refuses_a_tampered_snapshot(work: Any) -> None:
    snapshot = Path(str(work.receipt["snapshot_path"]))
    (snapshot / "untracked" / "notes" / "todo.txt").write_bytes(b"silently changed\n")

    with pytest.raises(hub.workflow.WorkflowError, match="Hash-Prüfung"):
        hub.restore(work.repo, str(work.receipt["handoff_id"]))
    with pytest.raises(hub.workflow.WorkflowError, match="Hash-Prüfung"):
        hub.restore(work.repo, str(work.receipt["handoff_id"]), apply=True)


def test_restore_is_refused_while_a_writer_lives_in_the_source_workspace(
    work: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = hub._start_writer(
        work.original,
        "opencode-local",
        SLEEPER,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        plan = hub.restore(work.repo, str(work.receipt["handoff_id"]))
        assert plan["source_writer"]["pid"] == writer.pid
        with pytest.raises(hub.WriterBusyError, match=f"PID {writer.pid}"):
            hub.restore(work.repo, str(work.receipt["handoff_id"]), apply=True)
    finally:
        writer.kill()
        writer.wait()
    assert not list(work.repo.parent.glob(f"{work.repo.name}-dev-restore-*"))


# --- 3. Continue work --------------------------------------------------------------


def test_continue_reports_the_resume_state_in_plain_words(
    clients: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(hub, "_port_open", lambda port: port == hub.OLLAMA_PORT)
    (clients.repo / "wip.txt").write_text("unsaved\n", encoding="utf-8")
    receipt = hub.create_handoff(
        clients.repo,
        from_agent="OpenCode",
        to_agent="Kimi",
        task="Review",
        completed="Read",
        open_items="Answer",
        assumptions="None",
        next_action="Kimi bestätigt und prüft den Diff",
        tests="not run",
    )
    hub.launch_opencode(clients.repo, "local")

    assert hub.main(["--repo", str(clients.repo), "continue"]) == 0
    text = capsys.readouterr().out
    assert "Aufgabe: KAI review" in text
    assert "1 geänderte Datei" in text
    assert f"opencode-local (PID {clients.spawned[0].pid}" in text
    assert f"{hub.LOCAL_MODEL}: installiert" in text
    assert "Antwort bewiesen: nein" in text
    assert "Cloud-Tunnel: geschlossen" in text
    assert f"Übergabe {receipt['handoff_id']} an Kimi wartet auf Bestätigung" in text
    assert "Kimi bestätigt und prüft den Diff" in text
    assert "{" not in text, "Klartext, kein JSON"

    # A proven answer is only claimed after the probe really produced one.
    monkeypatch.setattr(hub, "automation_inventory", lambda: {"available": True, "tasks": []})
    monkeypatch.setattr(
        hub,
        "_local_inference_probe",
        lambda: {"route": "ollama-local", "model": hub.LOCAL_MODEL, "response_proven": True},
    )
    hub.doctor(clients.repo, "local-inference")
    assert hub.main(["--repo", str(clients.repo), "continue", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["local"]["model_installed"] is True
    assert report["local"]["response_proven"] is True
    assert report["writer"]["pid"] == clients.spawned[0].pid
    assert report["pending_handoffs"][0]["handoff_id"] == receipt["handoff_id"]
