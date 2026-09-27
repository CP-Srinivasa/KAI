"""Das Dependency-Audit ist ein Gate — und bleibt eins (Audit 27.09.2026).

Vorher: pip-audit nur in der PR-CI, web/ ganz ohne Audit, keine naechtliche Pruefung.
Diese Tests halten fest, dass beide Pflicht-Checks und der Nachtlauf dasselbe Skript
aufrufen und die Ausnahmen nur an EINER Stelle stehen.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dependency_audit.sh"


def _steps(workflow: str, job: str) -> list[str]:
    data = yaml.safe_load((REPO / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    return [str(step.get("run", "")) for step in data["jobs"][job]["steps"]]


def test_the_required_security_check_audits_both_python_locks() -> None:
    assert any("dependency_audit.sh python" in run for run in _steps("ci.yml", "security"))


def test_the_required_frontend_check_audits_web() -> None:
    runs = _steps("ci.yml", "web")
    audit = next(i for i, run in enumerate(runs) if "dependency_audit.sh web" in run)
    assert audit < next(i for i, run in enumerate(runs) if "npm run build" in run)


def test_a_nightly_run_audits_everything() -> None:
    data = yaml.safe_load(
        (REPO / ".github" / "workflows" / "dependency-audit.yml").read_text(encoding="utf-8")
    )
    triggers = data.get(True) or data.get("on")
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    assert any("dependency_audit.sh all" in run for run in _steps("dependency-audit.yml", "audit"))


def test_ignores_live_only_in_the_script() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--ignore-vuln" not in ci
    assert "--ignore-vuln" in SCRIPT.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_an_unknown_mode_is_a_usage_error() -> None:
    result = subprocess.run(
        [shutil.which("bash") or "bash", str(SCRIPT), "bogus"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
