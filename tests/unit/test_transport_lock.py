from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "requirements-transport.lock"


def _locked_requirements() -> list[tuple[Requirement, list[str]]]:
    logical: list[str] = []
    current = ""
    for raw in LOCK.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        current += raw.strip()
        if current.endswith("\\"):
            current = current[:-1] + " "
        else:
            logical.append(current)
            current = ""
    assert not current
    parsed = []
    for line in logical:
        requirement_text = line.split(" --hash=", 1)[0].strip()
        hashes = re.findall(r"--hash=sha256:[0-9a-f]{64}", line)
        parsed.append((Requirement(requirement_text), hashes))
    return parsed


def test_transport_lock_is_fully_hashed_and_exact() -> None:
    requirements = _locked_requirements()
    assert requirements
    assert all(len(requirement.specifier) == 1 for requirement, _ in requirements)
    operators = {next(iter(requirement.specifier)).operator for requirement, _ in requirements}
    assert operators == {"=="}
    assert all(hashes for _, hashes in requirements)


def test_litellm_lock_version_matches_the_optional_extra() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extra = Requirement(project["project"]["optional-dependencies"]["litellm"][0])
    locked = {requirement.name.lower(): requirement for requirement, _ in _locked_requirements()}
    assert "litellm" in locked
    assert locked["litellm"].specifier == extra.specifier


def test_transport_lock_records_pi_resolution_target() -> None:
    header = "\n".join(LOCK.read_text(encoding="utf-8").splitlines()[:4])
    assert "--generate-hashes" in header
    assert "--python-platform aarch64-manylinux_2_36" in header
    assert "--python-version 3.12.3" in header
