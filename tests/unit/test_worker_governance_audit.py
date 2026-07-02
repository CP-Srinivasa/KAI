"""SENTR governance-audit worker mode + agent-mutation invariant (Issue #165).

- the worker mode is registered and read-only
- it reports governed / refused / ungoverned(legacy) counts from the journal +
  governance audit sidecar
- INVARIANT: agents may never mutate the registry (mutate_registry forbidden)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.agents.worker as worker
from app.agents.worker import HANDLERS
from app.security.governance.gates import check_agent_capability
from app.security.governance.models import (
    FORBIDDEN_AGENT_CAPABILITIES,
    AgentCapability,
)


def test_governance_audit_mode_is_registered() -> None:
    assert ("sentr", "governance-audit") in HANDLERS


@pytest.fixture()
def _isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(worker, "REPO_ROOT", tmp_path)
    # keep findings out of the real repo
    monkeypatch.setattr(worker, "_append_finding", lambda *a, **k: None)
    (tmp_path / "artifacts" / "governance").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_governance_audit_reports_counts(_isolated_repo: Path) -> None:
    journal = _isolated_repo / "artifacts" / "decision_journal.jsonl"
    sidecar = _isolated_repo / "artifacts" / "governance" / "decision_governance_audit.jsonl"
    # two journal decisions; one governed, one legacy-ungoverned; plus a refusal
    _write(journal, [{"decision_id": "dec_governed"}, {"decision_id": "dec_legacy"}])
    _write(
        sidecar,
        [
            {"decision_id": "dec_governed", "authorized": True, "blocker_codes": []},
            {
                "decision_id": "dec_rej",
                "authorized": False,
                "blocker_codes": ["MODEL_ENTRY_MISSING"],
            },
        ],
    )

    summary, result = worker._sentr_governance_audit(None)

    # refusal present → crit
    assert result == "crit"
    assert "1 refused" in summary or "refused" in summary
    assert "REFUSED" in summary
    assert "MODEL_ENTRY_MISSING" in summary
    # dec_legacy is in the journal but not governed → ungoverned legacy gap
    assert "ungoverned" in summary.lower()


def test_governance_audit_clean_when_all_governed(_isolated_repo: Path) -> None:
    journal = _isolated_repo / "artifacts" / "decision_journal.jsonl"
    sidecar = _isolated_repo / "artifacts" / "governance" / "decision_governance_audit.jsonl"
    _write(journal, [{"decision_id": "dec_1"}])
    _write(sidecar, [{"decision_id": "dec_1", "authorized": True, "blocker_codes": []}])

    _summary, result = worker._sentr_governance_audit(None)
    assert result == "ok"


def test_governance_audit_empty_is_not_crash(_isolated_repo: Path) -> None:
    summary, result = worker._sentr_governance_audit(None)
    assert result == "ok"
    assert "leer" in summary


def test_agents_cannot_mutate_registry() -> None:
    # invariant: mutate_registry stays forbidden and the capability gate denies it
    assert AgentCapability.MUTATE_REGISTRY.value in FORBIDDEN_AGENT_CAPABILITIES
    result = check_agent_capability("mutate_registry")
    assert result.allowed is False
