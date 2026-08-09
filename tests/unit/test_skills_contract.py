"""Contract for the repo-local Claude Code skills under `.claude/skills/`.

Skills are project config, not session config: CLAUDE.md §7 drives the daily
analysis through `daily-strategy-review`, and versioned agent definitions point
at `source-expansion` and `research-crosscheck`. They are version-controlled
since 2026-08-09 (targeted .gitignore exception), which is what makes these
checks possible at all.

The reference check exists because CLAUDE.md pointed at
`.claude/skill/Testing-Regeln` -- singular directory, wrong name, nothing on
disk -- in two places without anything noticing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / ".claude" / "skills"

_FRONTMATTER_NAME_RE = re.compile(r"^name:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_FRONTMATTER_DESC_RE = re.compile(r"^description:[ \t]*(\S.*)$", re.MULTILINE)

# Any `.claude/skill/foo` or `.claude/skills/foo` written down in project docs
# or in an agent definition. Singular `skill` is matched on purpose: it is a
# plausible typo and must be reported rather than silently ignored.
_SKILL_REF_RE = re.compile(r"\.claude/skills?/([A-Za-z0-9][A-Za-z0-9_-]*)")

_REFERENCING_FILES = ("CLAUDE.md", "AGENTS.md")

# Every `kai-...` written in backticks. The prefix is the project's own
# namespace, so a name in it is a claim that something by that name exists.
# The path check above only catches `.claude/skills/foo`; a bare name with a
# typo would still slip through, which is exactly how CLAUDE.md ended up
# telling Claude Code to "always" use a skill that was never once loaded.
_KAI_NAME_RE = re.compile(r"`(kai-[a-z0-9-]+)`")

# Skills that live at user level (`~/.claude/skills/`) rather than in the repo.
# They stay there on purpose: work happens from several working directories and
# a repo-local skill would be invisible outside the repo. CI cannot see them, so
# the set is curated here -- that is the trade-off, and it is written down
# rather than implied.
_EXTERNAL_SKILLS = {
    "kai-auftrag",
    "kai-deploy-regeln",
    "kai-master-coding-regeln",
    "kai-systematic-debugging",
    "kai-testing-regeln",
    "kai-verification-before-completion",
}

# `kai-` names that are deliberately NOT skills. Without this the guard would
# report systemd units as broken references. Note the trap these two encode:
# they are namesakes of the Watchdog *agent* and their running says nothing
# about whether that agent runs (see .claude/agents/watchdog.md).
_NON_SKILL_KAI_NAMES = {
    "kai-server-health-watchdog",
    "kai-service-watchdog",
}


def _skill_dirs() -> list[Path]:
    """Skill directories, or skip locally when `.claude/` is not checked out.

    Sparse worktrees may omit it. Never skip in CI, where the checkout is
    complete -- a skipped guard reads exactly like a satisfied one.
    """
    if not _SKILLS_DIR.is_dir():
        if os.environ.get("CI"):
            raise AssertionError(f"{_SKILLS_DIR} is missing although CI runs a full checkout")
        pytest.skip(f"{_SKILLS_DIR} not checked out (sparse worktree)")
    return sorted(p for p in _SKILLS_DIR.iterdir() if p.is_dir())


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{path} has no YAML frontmatter"
    return text.split("---", 2)[1]


def test_every_skill_directory_has_a_skill_file() -> None:
    for directory in _skill_dirs():
        assert (directory / "SKILL.md").is_file(), (
            f"{directory.name}/ has no SKILL.md; Claude Code cannot load it"
        )


def test_skill_name_matches_its_directory() -> None:
    for directory in _skill_dirs():
        match = _FRONTMATTER_NAME_RE.search(_frontmatter(directory / "SKILL.md"))
        assert match, f"{directory.name}/SKILL.md declares no name"
        assert match.group(1) == directory.name, (
            f"{directory.name}/SKILL.md declares name={match.group(1)!r}; "
            f"skills are resolved by directory, so the two must agree"
        )


def test_every_skill_declares_a_description() -> None:
    # Without a description the model cannot tell when the skill applies, so an
    # installed-but-undescribed skill is dead weight.
    for directory in _skill_dirs():
        match = _FRONTMATTER_DESC_RE.search(_frontmatter(directory / "SKILL.md"))
        assert match, f"{directory.name}/SKILL.md declares no description"


def test_documented_skill_paths_exist() -> None:
    available = {d.name for d in _skill_dirs()}
    sources = [_REPO_ROOT / name for name in _REFERENCING_FILES]
    sources += sorted((_REPO_ROOT / ".claude" / "agents").glob("*.md"))

    broken: list[str] = []
    for source in sources:
        if not source.is_file():
            continue
        for referenced in _SKILL_REF_RE.findall(source.read_text(encoding="utf-8")):
            if referenced not in available:
                broken.append(f"{source.relative_to(_REPO_ROOT)} -> .claude/skills/{referenced}")

    assert not broken, "reference points at a skill that does not exist: " + "; ".join(broken)


def test_every_kai_name_resolves_to_something_real() -> None:
    """No `kai-...` in the docs may name something that does not exist.

    A bare name is the form that failed silently before: CLAUDE.md prescribed a
    testing skill twice, the name was wrong, and it was never loaded once.
    """
    from app.api.routers.agents import _AGENTS

    known = set(_AGENTS) | {d.name for d in _skill_dirs()} | _EXTERNAL_SKILLS
    known |= _NON_SKILL_KAI_NAMES

    sources = [_REPO_ROOT / name for name in _REFERENCING_FILES]
    sources += sorted((_REPO_ROOT / ".claude" / "agents").glob("*.md"))
    sources += sorted(_SKILLS_DIR.glob("*/SKILL.md"))

    unknown: list[str] = []
    for source in sources:
        if not source.is_file():
            continue
        for name in _KAI_NAME_RE.findall(source.read_text(encoding="utf-8")):
            if name not in known:
                unknown.append(f"{source.relative_to(_REPO_ROOT)} -> `{name}`")

    assert not unknown, (
        "names something that is neither a registered agent, a repo skill, a "
        "known user-level skill nor a declared non-skill: " + "; ".join(sorted(set(unknown)))
    )


def test_external_skill_list_stays_disjoint_from_repo_skills() -> None:
    # A skill that exists in both places would drift, which is the failure the
    # agent roster already went through.
    overlap = _EXTERNAL_SKILLS & {d.name for d in _skill_dirs()}
    assert not overlap, f"skill listed as external but also present in the repo: {sorted(overlap)}"
