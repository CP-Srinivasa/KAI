"""Tests for app/research/active_route.py - Sprint 15."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.active_route import (
    DEFAULT_ACTIVE_ROUTE_PATH,
    ActiveRouteState,
    activate_route_profile,
    deactivate_route_profile,
    load_active_route_state,
)
from app.research.inference_profile import (
    InferenceRouteProfile,
    save_inference_route_profile,
)


def _write_profile(
    tmp_path: Path,
    route_profile: str = "primary_only",
    profile_name: str = "test_profile",
    enabled_shadow_paths: list[str] | None = None,
    control_path: str | None = None,
) -> Path:
    profile = InferenceRouteProfile(
        profile_name=profile_name,
        route_profile=route_profile,
        active_primary_path="A.external_llm",
        enabled_shadow_paths=enabled_shadow_paths or [],
        control_path=control_path,
    )
    path = tmp_path / "profile.json"
    save_inference_route_profile(profile, path)
    return path


def test_active_route_state_has_shadow_false_when_empty() -> None:
    state = ActiveRouteState(
        profile_path="/tmp/p.json",
        profile_name="p",
        route_profile="primary_only",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=[],
        control_path=None,
    )
    assert state.has_shadow is False


def test_active_route_state_has_shadow_true_when_paths() -> None:
    state = ActiveRouteState(
        profile_path="/tmp/p.json",
        profile_name="p",
        route_profile="primary_with_shadow",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=["B.companion"],
        control_path=None,
    )
    assert state.has_shadow is True


def test_active_route_state_has_control_false_when_none() -> None:
    state = ActiveRouteState(
        profile_path="/tmp/p.json",
        profile_name="p",
        route_profile="primary_only",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=[],
        control_path=None,
    )
    assert state.has_control is False


def test_active_route_state_has_control_true_when_set() -> None:
    state = ActiveRouteState(
        profile_path="/tmp/p.json",
        profile_name="p",
        route_profile="primary_with_control",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=[],
        control_path="C.rule",
    )
    assert state.has_control is True


def test_active_route_state_to_dict_structure() -> None:
    state = ActiveRouteState(
        profile_path="/tmp/p.json",
        profile_name="my_profile",
        route_profile="primary_with_shadow",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=["B.companion"],
        control_path=None,
        abc_envelope_output="artifacts/abc/envelopes.jsonl",
    )
    d = state.to_dict()
    assert d["profile_name"] == "my_profile"
    assert d["route_profile"] == "primary_with_shadow"
    assert d["enabled_shadow_paths"] == ["B.companion"]
    assert d["control_path"] is None
    assert d["abc_envelope_output"] == "artifacts/abc/envelopes.jsonl"
    assert "activated_at" in d


def test_activate_route_profile_creates_state_file(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path)
    state_path = tmp_path / "active.json"
    state = activate_route_profile(profile_path, state_path)

    assert state_path.exists()
    assert state.profile_name == "test_profile"
    assert state.route_profile == "primary_only"


def test_activate_route_profile_state_file_is_valid_json(tmp_path: Path) -> None:
    profile_path = _write_profile(
        tmp_path, route_profile="primary_with_shadow", enabled_shadow_paths=["B.companion"]
    )
    state_path = tmp_path / "active.json"
    activate_route_profile(profile_path, state_path)

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["route_profile"] == "primary_with_shadow"
    assert data["enabled_shadow_paths"] == ["B.companion"]


def test_activate_route_profile_custom_abc_output(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path)
    state_path = tmp_path / "active.json"
    state = activate_route_profile(
        profile_path, state_path, abc_envelope_output="custom/path.jsonl"
    )
    assert state.abc_envelope_output == "custom/path.jsonl"


def test_activate_route_profile_default_abc_output(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path)
    state_path = tmp_path / "active.json"
    state = activate_route_profile(profile_path, state_path)
    assert "abc_envelopes" in state.abc_envelope_output


def test_activate_route_profile_missing_profile_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        activate_route_profile(tmp_path / "nonexistent.json", tmp_path / "active.json")


def test_activate_route_profile_creates_parent_dirs(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path)
    state_path = tmp_path / "nested" / "deep" / "active.json"
    activate_route_profile(profile_path, state_path)
    assert state_path.exists()


def test_activate_route_profile_records_profile_path(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path)
    state_path = tmp_path / "active.json"
    state = activate_route_profile(profile_path, state_path)
    assert str(profile_path.resolve()) == state.profile_path


def test_activate_route_profile_with_shadow_and_control(tmp_path: Path) -> None:
    profile_path = _write_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        enabled_shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    state_path = tmp_path / "active.json"
    state = activate_route_profile(profile_path, state_path)
    assert state.has_shadow is True
    assert state.has_control is True
    assert state.control_path == "C.rule"


def test_load_active_route_state_returns_none_when_missing(tmp_path: Path) -> None:
    result = load_active_route_state(tmp_path / "missing.json")
    assert result is None


def test_load_active_route_state_roundtrip(tmp_path: Path) -> None:
    profile_path = _write_profile(
        tmp_path,
        route_profile="primary_with_shadow",
        enabled_shadow_paths=["B.companion"],
    )
    state_path = tmp_path / "active.json"
    original = activate_route_profile(
        profile_path, state_path, abc_envelope_output="out/envelopes.jsonl"
    )
    loaded = load_active_route_state(state_path)

    assert loaded is not None
    assert loaded.profile_name == original.profile_name
    assert loaded.route_profile == original.route_profile
    assert loaded.enabled_shadow_paths == original.enabled_shadow_paths
    assert loaded.abc_envelope_output == "out/envelopes.jsonl"
    assert loaded.has_shadow is True


def test_deactivate_route_profile_returns_true_when_existed(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path)
    state_path = tmp_path / "active.json"
    activate_route_profile(profile_path, state_path)
    assert deactivate_route_profile(state_path) is True


def test_deactivate_route_profile_removes_file(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path)
    state_path = tmp_path / "active.json"
    activate_route_profile(profile_path, state_path)
    deactivate_route_profile(state_path)
    assert not state_path.exists()


def test_deactivate_route_profile_returns_false_when_missing(tmp_path: Path) -> None:
    assert deactivate_route_profile(tmp_path / "missing.json") is False


def test_deactivate_then_load_returns_none(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path)
    state_path = tmp_path / "active.json"
    activate_route_profile(profile_path, state_path)
    deactivate_route_profile(state_path)
    assert load_active_route_state(state_path) is None


def test_default_active_route_path_is_in_artifacts() -> None:
    assert "artifacts" in str(DEFAULT_ACTIVE_ROUTE_PATH)
    assert DEFAULT_ACTIVE_ROUTE_PATH.name.endswith(".json")
