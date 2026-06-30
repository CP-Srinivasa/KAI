"""F-06/KAI-05: agent-roster contract.

Pins the truth that the dashboard roster (`_AGENTS`) and the autonomous worker
(`HANDLERS`) agree on which agents are actually worker-backed — so the dashboard
never implies autonomous execution an interactive agent never performs.
"""

from __future__ import annotations

from app.agents.worker import HANDLERS
from app.api.routers.agents import _AGENTS


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
