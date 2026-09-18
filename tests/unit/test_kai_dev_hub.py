"""Behavioral tests for the operator-only KAI developer failover hub."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HUB_PATH = REPO / "scripts" / "kai_dev_hub.py"
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


def test_context_pack_pins_repository_state_and_contracts(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "STATE_ROOT", tmp_path / "state")

    target = hub.context_pack(kai_repo)
    text = target.read_text(encoding="utf-8")

    assert str(kai_repo) in text
    assert hub._git(kai_repo, "rev-parse", "HEAD") in text
    assert "AGENTS.md" in text and "AI_HANDOFF.md" in text
    assert "do not deploy, merge, push" in text
    assert ".env" in text and "app/ai" in text


def test_handoff_ledger_is_append_only_chained_and_detects_tampering(
    kai_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "STATE_ROOT", tmp_path / "state")
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
    assert hub.verify_handoffs() == (
        True,
        "2 Übergabe(n) kryptografisch verkettet und unverändert.",
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
    monkeypatch.setattr(hub, "STATE_ROOT", tmp_path / "state")
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
