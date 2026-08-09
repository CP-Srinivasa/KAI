"""F-06/KAI-05: agent-roster contract.

Pins the truth that the dashboard roster (`_AGENTS`) and the autonomous worker
(`HANDLERS`) agree on which agents are actually worker-backed — so the dashboard
never implies autonomous execution an interactive agent never performs.
"""

from __future__ import annotations

import re

from app.agents.worker import HANDLERS
from app.api.routers.agents import _AGENTS

# Pinned so that adding or removing a Claude-Code-only agent is a deliberate
# edit. The roster lives in three places -- this dict, CLAUDE.md's auto-routing
# table and `.claude/agents/*.md` -- and they drifted apart once already:
# kai-finder was registered here without a definition file, sentr had a
# definition that the working directory could not reach. `.claude/` is
# gitignored, so this file is the only register CI can enforce.
_INTERACTIVE = {
    "dali",
    "neo",
    "satoshi",
    "kai-finder",
    "einstein",
    "xqu",
    "architecture-red-team",
    "data-quality-inspector",
}

_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _handler_agents() -> set[str]:
    return {agent for (agent, _mode) in HANDLERS}


def test_every_worker_handler_agent_is_autonomous() -> None:
    for slug in _handler_agents():
        assert slug in _AGENTS, f"worker handler references unknown agent: {slug}"
        assert _AGENTS[slug].wiring == "autonomous", (
            f"{slug} has a worker handler but is wiring={_AGENTS[slug].wiring!r}"
        )


def test_autonomous_set_equals_worker_backed_set() -> None:
    autonomous = {slug for slug, defn in _AGENTS.items() if defn.wiring == "autonomous"}
    # An "autonomous" agent with no handler is a dashboard promise nothing
    # fulfils; a handler agent not marked autonomous slips past the guard.
    assert autonomous == _handler_agents() == {"watchdog", "sentr", "architect"}


def test_interactive_agents_have_no_worker_handler() -> None:
    handlers = _handler_agents()
    for slug, defn in _AGENTS.items():
        if defn.wiring == "interactive":
            assert slug not in handlers, f"{slug} is interactive but has a worker handler"


def test_every_agent_declares_a_known_wiring() -> None:
    for slug, defn in _AGENTS.items():
        assert defn.wiring in {"autonomous", "interactive"}, slug


def test_interactive_set_is_pinned() -> None:
    interactive = {slug for slug, defn in _AGENTS.items() if defn.wiring == "interactive"}
    assert interactive == _INTERACTIVE


def test_slug_is_kebab_case_and_matches_its_key() -> None:
    for key, defn in _AGENTS.items():
        assert defn.slug == key, f"dict key {key!r} != slug {defn.slug!r}"
        assert _SLUG_RE.fullmatch(key), f"slug is not kebab-case: {key!r}"


def test_every_agent_declares_modes_and_permissions() -> None:
    for slug, defn in _AGENTS.items():
        assert defn.modes, f"{slug} declares no modes"
        assert defn.permissions, f"{slug} declares no permissions"
