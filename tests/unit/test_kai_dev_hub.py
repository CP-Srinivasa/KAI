"""Behavioral tests for the operator-only KAI developer failover hub."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HUB_PATH = REPO / "scripts" / "kai_dev_hub.py"
sys.path.insert(0, str(HUB_PATH.parent))
SPEC = importlib.util.spec_from_file_location("kai_dev_hub", HUB_PATH)
assert SPEC and SPEC.loader
hub = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hub)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture()
def kai_repo(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude\n", encoding="utf-8")
    (tmp_path / "ARCHITECTURE.md").write_text("architecture\n", encoding="utf-8")
    (tmp_path / "docs" / "AI_HANDOFF.md").write_text("handoff\n", encoding="utf-8")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def _managed(kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr(hub, "STATE_ROOT", state)
    monkeypatch.setattr(
        hub.workflow,
        "list_sessions",
        lambda _state: [
            {
                "session_id": "test-session",
                "created_at": "2026-09-19T00:00:00+00:00",
                "task": "KAI review",
                "worktree": str(kai_repo),
                "branch": hub._git(kai_repo, "branch", "--show-current"),
                "base_sha": hub._git(kai_repo, "rev-parse", "HEAD"),
            }
        ],
    )


def test_context_pack_pins_repository_state_and_contracts(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)

    target = hub.context_pack(kai_repo)
    text = target.read_text(encoding="utf-8")

    assert "rules\n" in text
    assert "architecture\n" in text
    assert "KAI review" in text
    assert hub._git(kai_repo, "rev-parse", "HEAD") in text
    assert "AGENTS.md" in text and "AI_HANDOFF.md" in text
    assert "No agent may merge, deploy" in text
    assert ".env" in text and "app/ai" in text


def test_handoff_ledger_is_append_only_chained_and_detects_tampering(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    (kai_repo / "notes.txt").write_text("recover me", encoding="utf-8")
    first = hub.create_handoff(
        kai_repo,
        from_agent="OpenCode",
        to_agent="Hermes",
        task="Diagnose",
        completed="Read logs",
        open_items="Fix",
        assumptions="None",
        next_action="Implement",
        tests="not run",
    )
    second = hub.create_handoff(
        kai_repo,
        from_agent="Hermes",
        to_agent="Claude",
        task="Implement",
        completed="Patch",
        open_items="Review",
        assumptions="None",
        next_action="Review",
        tests="pytest passed",
    )

    assert second["previous_sha256"] == first["payload_sha256"]
    assert (Path(first["snapshot_path"]) / "untracked" / "notes.txt").read_text() == "recover me"
    assert "Completed: Read logs" in Path(first["context_pack_path"]).read_text(encoding="utf-8")
    assert hub.verify_handoffs() == (
        True,
        "2 Übergabe(n), 0 bestätigt; Hash-Kette unverändert.",
    )

    ledger, _ = hub._handoff_paths()
    rows = ledger.read_text(encoding="utf-8").splitlines()
    changed = json.loads(rows[0])
    changed["completed"] = "silently changed"
    rows[0] = json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ledger.write_text("\n".join(rows) + "\n", encoding="utf-8")

    valid, reason = hub.verify_handoffs()
    assert valid is False
    assert "SHA-256 stimmt nicht" in reason


def test_handoff_rejects_missing_identity_or_task(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    with pytest.raises(hub.HubError, match="Pflichtfelder"):
        hub.create_handoff(
            kai_repo,
            from_agent="",
            to_agent="Claude",
            task="",
            completed="",
            open_items="",
            assumptions="",
            next_action="",
            tests="",
        )
    assert not (hub.STATE_ROOT / "snapshots").exists()


def test_cloud_boundary_uses_only_loopback_and_dedicated_dev_names() -> None:
    text = HUB_PATH.read_text(encoding="utf-8")

    assert 'DEV_HOST = "127.0.0.1"' in text
    assert "DEV_PORT = 4001" in text
    assert "LITELLM_DEV_MASTER_KEY" in text
    assert "KAI_DEV_LITELLM_KEY" in text
    assert "LITELLM_MASTER_KEY" not in text.replace("LITELLM_DEV_MASTER_KEY", "")
    assert "ANTHROPIC_API_KEY" not in text
    assert "OPENAI_API_KEY" not in text
    assert "git push" not in text
    assert "git merge" not in text
    assert "systemctl" not in text


def test_local_route_is_pinned_to_installed_kai_model() -> None:
    assert hub.LOCAL_MODEL == "kai-qwen3-coder:30b-16k"
    assert hub.HERMES_LOCAL_MODEL == "kai-qwen3-coder:30b-64k"
    assert hub.DEV_MODELS == {"kai-dev-economy", "kai-dev-code", "kai-dev-frontier"}


def test_handoff_ack_requires_recipient_challenge_and_preserves_chain(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    receipt = hub.create_handoff(
        kai_repo,
        from_agent="OpenCode",
        to_agent="Hermes",
        task="Review an isolated test",
        completed="Read the test",
        open_items="Explain the failing assertion",
        assumptions="No production access",
        next_action="Review only",
        tests="not run",
    )
    with pytest.raises(hub.HubError, match="Challenge"):
        hub.acknowledge_handoff(
            kai_repo,
            handoff_id=receipt["handoff_id"],
            agent="Hermes",
            response="I understand the open item and will review the isolated assertion.",
        )
    response = (
        f"{receipt['ack_challenge']} Handoff {receipt['handoff_id']}: I received the review. "
        "I will explain the failing assertion without changing production."
    )
    ack = hub.acknowledge_handoff(
        kai_repo, handoff_id=receipt["handoff_id"], agent="Hermes", response=response
    )
    assert ack["handoff_sha256"] == receipt["payload_sha256"]
    assert hub.handoff_state(kai_repo)["pending"] == []
    monkeypatch.setattr(hub, "_port_open", lambda _port: False)
    assert hub.status(kai_repo)["handoff_state_global"]["acknowledged"] == 1
    assert hub.verify_handoffs()[0] is True
    with pytest.raises(hub.HubError, match="bereits bestätigt"):
        hub.acknowledge_handoff(
            kai_repo, handoff_id=receipt["handoff_id"], agent="Hermes", response=response
        )


def test_snapshot_rejects_untracked_secret_before_writing(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    (kai_repo / "api.secret").write_text("sensitive", encoding="utf-8")
    with pytest.raises(hub.workflow.WorkflowError, match="ungeeignet"):
        hub.workflow.snapshot(kai_repo, hub.STATE_ROOT, "blocked")
    assert not (hub.STATE_ROOT / "snapshots" / "blocked").exists()


def test_snapshot_rejects_changed_tracked_secret(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    (kai_repo / "api.secret").write_text("first", encoding="utf-8")
    _git(kai_repo, "add", "api.secret")
    _git(kai_repo, "commit", "-m", "fixture")
    (kai_repo / "api.secret").write_text("second", encoding="utf-8")
    with pytest.raises(hub.workflow.WorkflowError, match="Secret-Datei"):
        hub.workflow.snapshot(kai_repo, hub.STATE_ROOT, "blocked-tracked")
    assert not (hub.STATE_ROOT / "snapshots" / "blocked-tracked").exists()


def test_shared_checkout_cannot_launch_client(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "STATE_ROOT", tmp_path / "state")
    with pytest.raises(hub.workflow.WorkflowError, match="Neue Aufgabe"):
        hub.launch_opencode(kai_repo, "local")


def test_opencode_start_injects_context_and_pins_model(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    calls: list[list[str]] = []
    monkeypatch.setattr(hub, "ensure_ollama", lambda: None)
    monkeypatch.setattr(hub, "_ollama_models", lambda: {hub.LOCAL_MODEL})
    monkeypatch.setattr(
        hub, "_command", lambda name: "opencode.cmd" if name == "opencode.cmd" else None
    )
    real_popen = hub.subprocess.Popen

    def capture_client(args: list[str], **kwargs: object) -> object:
        if args[0] == "opencode.cmd":
            calls.append(args)
            return object()
        return real_popen(args, **kwargs)

    monkeypatch.setattr(hub.subprocess, "Popen", capture_client)

    hub.launch_opencode(kai_repo, "local")

    args = calls[0]
    assert args[args.index("-m") + 1] == f"ollama/{hub.LOCAL_MODEL}"
    prompt = args[args.index("--prompt") + 1]
    assert "rules" in prompt and "architecture" in prompt
    assert "This document is self-contained" in prompt
    assert "Lies zuerst C:" not in prompt


def test_cloud_probe_requires_real_reply_identity_and_positive_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reply(io.BytesIO):
        headers = {"x-litellm-response-cost": "0.000002"}

    def healthy(_request: object, timeout: int) -> Reply:
        assert timeout == 120
        return Reply(
            json.dumps({"model": "kimi-k2.7", "choices": [{"message": {"content": "ok"}}]}).encode()
        )

    monkeypatch.setattr(hub.urllib.request, "urlopen", healthy)
    report = hub._cloud_inference_probe("test-only", "kai-dev-code")
    assert report["model"] == "kimi-k2.7"
    assert report["cost_usd"] > 0

    class ZeroCost(Reply):
        headers = {"x-litellm-response-cost": "0"}

    monkeypatch.setattr(
        hub.urllib.request,
        "urlopen",
        lambda _request, timeout: ZeroCost(
            json.dumps({"model": "kimi-k2.7", "choices": [{"message": {"content": "ok"}}]}).encode()
        ),
    )
    with pytest.raises(hub.HubError, match="FAIL_CLOSED"):
        hub._cloud_inference_probe("test-only", "kai-dev-code")


def test_local_diagnostics_survive_cloud_and_pi_outage(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    monkeypatch.setattr(hub, "_port_open", lambda port: port == hub.OLLAMA_PORT)
    monkeypatch.setattr(hub, "_ollama_models", lambda: {hub.LOCAL_MODEL, hub.HERMES_LOCAL_MODEL})
    monkeypatch.setattr(hub, "_command", lambda _name: "installed")
    monkeypatch.setattr(hub, "automation_inventory", lambda: {"available": True, "tasks": []})
    monkeypatch.setattr(
        hub, "_local_inference_probe", lambda: {"route": "ollama-local", "response_proven": True}
    )
    monkeypatch.setattr(
        hub, "_fetch_dev_key", lambda: pytest.fail("local mode must never contact Pi")
    )

    report = hub.doctor(kai_repo, "local-inference")

    assert report["checks"]["offline_ready"] is True
    assert report["checks"]["cloud_tunnel_open"] is False
    assert report["checks"]["local_inference"]["response_proven"] is True


def test_offline_doctor_cold_starts_ollama_before_readiness(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    online = {"value": False}
    monkeypatch.setattr(
        hub,
        "_port_open",
        lambda port: online["value"] if port == hub.OLLAMA_PORT else False,
    )
    monkeypatch.setattr(hub, "ensure_ollama", lambda: online.update(value=True))
    monkeypatch.setattr(hub, "_ollama_models", lambda: {hub.LOCAL_MODEL, hub.HERMES_LOCAL_MODEL})
    monkeypatch.setattr(hub, "_command", lambda _name: "installed")
    monkeypatch.setattr(hub, "automation_inventory", lambda: {"available": True, "tasks": []})
    monkeypatch.setattr(
        hub, "_fetch_dev_key", lambda: pytest.fail("offline mode must never contact Pi")
    )

    report = hub.doctor(kai_repo, "offline")

    assert online["value"] is True
    assert report["checks"]["ollama_online"] is True
    assert report["checks"]["offline_ready"] is True


def test_offline_doctor_reports_failed_cold_start(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    monkeypatch.setattr(hub, "_port_open", lambda _port: False)
    monkeypatch.setattr(
        hub, "ensure_ollama", lambda: (_ for _ in ()).throw(hub.HubError("start failed"))
    )
    monkeypatch.setattr(hub, "_command", lambda _name: "installed")
    monkeypatch.setattr(hub, "automation_inventory", lambda: {"available": True, "tasks": []})

    report = hub.doctor(kai_repo, "offline")

    assert report["checks"]["offline_ready"] is False
    assert report["checks"]["ollama_start_error"] == "start failed"


def test_cloud_doctor_refreshes_tunnel_status_after_on_demand_start(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    tunnel = {"open": False}
    monkeypatch.setattr(
        hub,
        "_port_open",
        lambda port: tunnel["open"] if port == hub.DEV_PORT else False,
    )
    monkeypatch.setattr(hub, "_command", lambda _name: "installed")
    monkeypatch.setattr(hub, "automation_inventory", lambda: {"available": True, "tasks": []})

    def start() -> str:
        tunnel["open"] = True
        return "dev-test-key"

    monkeypatch.setattr(hub, "start_cloud", start)
    monkeypatch.setattr(
        hub,
        "_cloud_inference_probe",
        lambda _key, route: {"route": route, "response_proven": True, "cost_usd": 0.0001},
    )

    report = hub.doctor(kai_repo, "cloud")

    assert report["checks"]["cloud_tunnel_open"] is True
    assert len(report["checks"]["dev_routes"]) == 2


def test_new_task_branches_from_fresh_authoritative_remote(kai_repo: Path, tmp_path: Path) -> None:
    state = tmp_path / "state"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    _git(kai_repo, "remote", "add", "origin", str(remote))
    _git(kai_repo, "branch", "claude/p7/reentry-ia-codex-cycle")
    _git(kai_repo, "push", "origin", "claude/p7/reentry-ia-codex-cycle")
    row = hub.workflow.new_task(kai_repo, state, "controlled review")
    path = Path(row["worktree"])
    try:
        assert path.is_dir()
        assert hub.workflow._git(path, "rev-parse", "HEAD") == row["base_sha"]
        assert hub.workflow.require_session(path, state)["session_id"] == row["session_id"]
    finally:
        _git(kai_repo, "worktree", "remove", str(path))


# --- KAI-DEV-INDEPENDENCE-02: handoff identity, task sources, cleanup ---------


def _with_secret_catalogue(kai_repo: Path) -> None:
    """The source guard reuses the repository's single secret catalogue."""
    for name in ("scripts/secret_guard.py", "app/security/secret_patterns.py"):
        target = kai_repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO / name).read_text(encoding="utf-8"), encoding="utf-8")
    (kai_repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    (kai_repo / "app" / "security" / "__init__.py").write_text("", encoding="utf-8")
    (kai_repo / "scripts" / "dev_reserve.sh").write_text(
        "#!/usr/bin/env bash\nPORT=4001\n" + "echo reserve\n" * 3, encoding="utf-8"
    )
    _git(kai_repo, "add", ".")
    _git(kai_repo, "commit", "-m", "sources")


def _handoff(kai_repo: Path, **extra: object) -> dict[str, object]:
    return hub.create_handoff(
        kai_repo,
        from_agent="OpenCode",
        to_agent="Kimi",
        task="Review dev reserve",
        completed="Read runbook",
        open_items="Check proxy start",
        assumptions="No production access",
        next_action="Review scripts/dev_reserve.sh",
        tests="not run",
        **extra,
    )


def test_context_pack_names_handoff_id_distinct_from_session(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    receipt = _handoff(kai_repo)
    text = Path(str(receipt["context_pack_path"])).read_text(encoding="utf-8")

    assert f"Handoff ID: {receipt['handoff_id']}" in text
    assert "Session ID (workspace, not the handoff): test-session" in text
    assert f"Handoff receipt SHA-256: {receipt['payload_sha256']}" in text
    assert f"Previous receipt SHA-256: {receipt['previous_sha256']}" in text
    assert "ledger.jsonl" in text
    # The ID appears before any included document, so it cannot be missed.
    assert text.index(f"Handoff ID: {receipt['handoff_id']}") < text.index("## Included source")


def test_ack_must_quote_handoff_id_not_session_id(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    receipt = _handoff(kai_repo)
    wrong = (
        f"{receipt['ack_challenge']} Session test-session received. I will review the "
        "proxy start in scripts/dev_reserve.sh without production access."
    )
    with pytest.raises(hub.HubError, match="Übergabe-ID"):
        hub.acknowledge_handoff(
            kai_repo, handoff_id=str(receipt["handoff_id"]), agent="Kimi", response=wrong
        )
    right = wrong.replace("Session test-session", f"Handoff {receipt['handoff_id']}")
    ack = hub.acknowledge_handoff(
        kai_repo, handoff_id=str(receipt["handoff_id"]), agent="Kimi", response=right
    )
    assert ack["schema_version"] == 2
    assert hub.verify_handoffs()[0] is True


def test_task_sources_are_pinned_hashed_and_truncation_is_visible(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    _with_secret_catalogue(kai_repo)
    long_file = kai_repo / "scripts" / "long.py"
    long_file.write_text("x = 1\n" * 5_000, encoding="utf-8")
    _git(kai_repo, "add", ".")
    _git(kai_repo, "commit", "-m", "long")
    receipt = _handoff(kai_repo, sources=["scripts/dev_reserve.sh", "scripts\\long.py"])
    text = Path(str(receipt["context_pack_path"])).read_text(encoding="utf-8")
    blob = hub._git(kai_repo, "rev-parse", "HEAD:scripts/dev_reserve.sh")
    data = (kai_repo / "scripts" / "dev_reserve.sh").read_bytes()

    assert receipt["context_sources"] == ["scripts/dev_reserve.sh", "scripts/long.py"]
    assert "## Task source: scripts/dev_reserve.sh" in text
    assert f"Git blob at HEAD: {blob}" in text
    assert f"SHA-256 of working file: {hub.hashlib.sha256(data).hexdigest()}" in text
    assert "echo reserve" in text
    assert "## Task source: scripts/long.py" in text
    assert "TRUNCATED" in text.split("## Task source: scripts/long.py", 1)[1]
    # Unselected repository files are not transferred.
    assert "secret_patterns" not in text.split("## Task-specific sources", 1)[1]


def _write_untracked(repo: Path) -> None:
    (repo / "untracked.py").write_text("x = 1\n", encoding="utf-8")


def _noop(_repo: Path) -> None:
    return None


@pytest.mark.parametrize(
    ("setup", "path", "message"),
    [
        (_write_untracked, "untracked.py", "nicht versioniert"),
        (_noop, "../outside.py", "außerhalb"),
        (_noop, ".env", "Secret"),
        (_noop, "missing.py", "nicht versioniert"),
    ],
)
def test_task_sources_reject_unsafe_selection_before_writing(
    kai_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    setup: Callable[[Path], None],
    path: str,
    message: str,
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    _with_secret_catalogue(kai_repo)
    setup(kai_repo)
    with pytest.raises(hub.workflow.WorkflowError, match=message):
        _handoff(kai_repo, sources=[path])
    assert not (hub.STATE_ROOT / "handoffs" / "ledger.jsonl").exists()
    assert not (hub.STATE_ROOT / "snapshots").exists()


def test_task_source_with_secret_content_is_refused_without_echoing_it(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    _with_secret_catalogue(kai_repo)
    token = "sk-proj-" + "A" * 30
    (kai_repo / "leak.py").write_text(f"KEY = '{token}'\n", encoding="utf-8")
    _git(kai_repo, "add", ".")
    _git(kai_repo, "commit", "-m", "leak")
    with pytest.raises(hub.workflow.WorkflowError) as caught:
        _handoff(kai_repo, sources=["leak.py"])
    assert "leak.py:1" in str(caught.value)
    assert token not in str(caught.value)


def test_task_sources_fail_closed_without_secret_catalogue(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    with pytest.raises(hub.workflow.WorkflowError, match="Secret-Prüfung"):
        _handoff(kai_repo, sources=["AGENTS.md"])


def test_task_source_count_is_bounded(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed(kai_repo, tmp_path, monkeypatch)
    _with_secret_catalogue(kai_repo)
    with pytest.raises(hub.workflow.WorkflowError, match="höchstens"):
        _handoff(kai_repo, sources=[f"f{i}.py" for i in range(20)])


def test_stop_cloud_cleans_up_after_dead_tunnel_without_killing_foreign_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "STATE_ROOT", tmp_path / "state")
    hub._save_cloud_pid(4242, "proxy-and-tunnel")
    killed: list[list[str]] = []
    monkeypatch.setattr(hub, "_pid_is_ssh", lambda _pid: False)
    monkeypatch.setattr(hub, "_run", lambda args, **_kw: killed.append(args))
    monkeypatch.setattr(hub, "_stop_remote_proxy", lambda: True)

    assert hub.stop_cloud() is True
    assert killed == []
    assert not hub._cloud_state_file().exists()


def test_stop_cloud_keeps_state_while_pi_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "STATE_ROOT", tmp_path / "state")
    hub._save_cloud_pid(4242, "proxy-and-tunnel")
    monkeypatch.setattr(hub, "_pid_is_ssh", lambda _pid: False)
    monkeypatch.setattr(hub, "_stop_remote_proxy", lambda: False)

    assert hub.stop_cloud() is False
    assert hub._cloud_state_file().exists()


def test_start_cloud_cleans_up_when_tunnel_dies_during_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(hub, "_fetch_dev_key", lambda: "dev-test-key")
    monkeypatch.setattr(hub, "_port_open", lambda _port: False)
    monkeypatch.setattr(hub, "_remote_proxy_is_open", lambda: False)
    stopped: list[bool] = []

    def stop() -> bool:
        stopped.append(True)
        return True

    monkeypatch.setattr(hub, "stop_cloud", stop)

    class Dead:
        pid = 777

        def poll(self) -> int:
            return 255

    monkeypatch.setattr(hub.subprocess, "Popen", lambda *_a, **_kw: Dead())
    with pytest.raises(hub.HubError, match="vorzeitig"):
        hub.start_cloud()
    assert stopped == [True]


def test_automation_success_comes_from_action_result_not_task_completion() -> None:
    text = HUB_PATH.read_text(encoding="utf-8")
    assert "Id=201" in text
    assert "Id=102" not in text
    assert "last_failure" in text
