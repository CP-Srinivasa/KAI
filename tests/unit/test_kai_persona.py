"""Tests for app.messaging.kai_persona — YAML loader + schema validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.messaging.kai_persona import (
    KaiPersonaConfigError,
    REQUIRED_MOTTO,
    VALID_STATES,
    load_kai_persona,
    reset_persona_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_persona_cache()
    yield
    reset_persona_cache()


def test_load_real_yaml_passes_validation():
    persona = load_kai_persona()
    assert persona.motto == REQUIRED_MOTTO
    assert persona.name == "KAI"
    assert "Kinetic Artificial Intelligence" in persona.full_name
    assert set(persona.states.keys()) == set(VALID_STATES)


def test_state_priority_order_is_strict():
    persona = load_kai_persona()
    assert persona.states["ERROR"].priority > persona.states["WARNING"].priority
    assert persona.states["WARNING"].priority > persona.states["SIGNAL"].priority
    assert persona.states["SIGNAL"].priority > persona.states["SECURITY"].priority
    assert persona.states["SECURITY"].priority > persona.states["ANALYSIS"].priority
    assert persona.states["ANALYSIS"].priority > persona.states["IDLE"].priority
    assert persona.states["IDLE"].priority > persona.states["OFFLINE"].priority


def test_each_state_has_at_least_one_de_and_one_en_phrase():
    persona = load_kai_persona()
    for state in VALID_STATES:
        cfg = persona.states[state]
        assert len(cfg.phrases_de) >= 1, f"{state} missing DE phrases"
        assert len(cfg.phrases_en) >= 1, f"{state} missing EN phrases"


def test_to_snapshot_dict_exposes_motto_and_states():
    persona = load_kai_persona()
    snap = persona.to_snapshot_dict()
    assert snap["motto"] == REQUIRED_MOTTO
    assert "state_machine" in snap
    assert set(snap["state_machine"]["states"].keys()) == set(VALID_STATES)


def test_invalid_motto_raises(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "kai:\n"
        "  name: KAI\n"
        "  full_name: KAI\n"
        "  motto: 'totally wrong motto'\n"
        "  version: '1.0'\n"
        "  state_machine:\n"
        "    default_state: IDLE\n"
        "    priority_order: []\n"
        "    states: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(KaiPersonaConfigError) as exc:
        load_kai_persona(path=bad)
    assert "motto" in str(exc.value).lower()


def test_missing_state_raises(tmp_path: Path):
    bad = tmp_path / "missing_state.yaml"
    bad.write_text(
        "kai:\n"
        "  name: KAI\n"
        "  full_name: KAI\n"
        "  motto: 'Persona non grata'\n"
        "  version: '1.0'\n"
        "  state_machine:\n"
        "    default_state: IDLE\n"
        "    priority_order: []\n"
        "    states:\n"
        "      IDLE: {priority: 10, color: '#000', icon: a, animation: a, ui_behavior: a, severity: none, phrases_de: [a], phrases_en: [a]}\n",
        encoding="utf-8",
    )
    with pytest.raises(KaiPersonaConfigError) as exc:
        load_kai_persona(path=bad)
    assert "ANALYSIS" in str(exc.value) or "missing" in str(exc.value).lower()


def test_missing_file_raises(tmp_path: Path):
    nonexistent = tmp_path / "does_not_exist.yaml"
    with pytest.raises(KaiPersonaConfigError):
        load_kai_persona(path=nonexistent)


def test_empty_phrases_array_raises(tmp_path: Path):
    bad = tmp_path / "empty_phrases.yaml"
    states_yaml = "\n".join(
        f"      {s}: {{priority: {i}, color: '#000', icon: a, animation: a, ui_behavior: a, severity: none, phrases_de: [], phrases_en: [a]}}"
        for i, s in enumerate(VALID_STATES, start=1)
    )
    bad.write_text(
        "kai:\n"
        "  name: KAI\n"
        "  full_name: KAI\n"
        "  motto: 'Persona non grata'\n"
        "  version: '1.0'\n"
        "  state_machine:\n"
        "    default_state: IDLE\n"
        "    priority_order: []\n"
        "    states:\n"
        f"{states_yaml}\n",
        encoding="utf-8",
    )
    with pytest.raises(KaiPersonaConfigError) as exc:
        load_kai_persona(path=bad)
    assert "phrases_de" in str(exc.value)
