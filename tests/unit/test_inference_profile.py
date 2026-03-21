"""Tests for app/research/inference_profile.py — Sprint 14.3."""

from __future__ import annotations

import json

import pytest

from app.research.inference_profile import (
    VALID_ROUTE_PROFILES,
    DistributionTarget,
    InferenceRouteProfile,
    load_inference_route_profile,
    save_inference_route_profile,
)


def _make_profile(
    route_profile: str = "primary_only",
    active_primary_path: str = "A.external_llm",
    enabled_shadow_paths: list[str] | None = None,
    control_path: str | None = None,
) -> InferenceRouteProfile:
    return InferenceRouteProfile(
        profile_name="test_profile",
        route_profile=route_profile,
        active_primary_path=active_primary_path,
        enabled_shadow_paths=enabled_shadow_paths or [],
        control_path=control_path,
    )


# ---------------------------------------------------------------------------
# DistributionTarget
# ---------------------------------------------------------------------------


def test_distribution_target_to_dict_structure() -> None:
    target = DistributionTarget(
        channel="research_brief",
        include_paths=["A"],
        mode="primary_only",
        artifact_path="/tmp/brief.json",
    )
    d = target.to_dict()
    assert d["channel"] == "research_brief"
    assert d["include_paths"] == ["A"]
    assert d["mode"] == "primary_only"
    assert d["artifact_path"] == "/tmp/brief.json"


def test_distribution_target_artifact_path_optional() -> None:
    target = DistributionTarget(
        channel="shadow_audit_jsonl", include_paths=["B"], mode="audit_only"
    )
    assert target.artifact_path is None
    assert target.to_dict()["artifact_path"] is None


# ---------------------------------------------------------------------------
# InferenceRouteProfile.to_json_dict
# ---------------------------------------------------------------------------


def test_inference_route_profile_to_json_dict_structure() -> None:
    profile = _make_profile()
    d = profile.to_json_dict()
    assert "report_type" in d
    assert "profile_name" in d
    assert "route_profile" in d
    assert "active_primary_path" in d
    assert "enabled_shadow_paths" in d
    assert "control_path" in d
    assert "distribution_targets" in d
    assert "notes" in d


def test_inference_route_profile_report_type_always_present() -> None:
    profile = _make_profile()
    assert profile.to_json_dict()["report_type"] == "inference_route_profile"


def test_inference_route_profile_primary_only() -> None:
    profile = _make_profile(route_profile="primary_only")
    d = profile.to_json_dict()
    assert d["route_profile"] == "primary_only"
    assert d["enabled_shadow_paths"] == []
    assert d["control_path"] is None


def test_inference_route_profile_with_shadow_and_control() -> None:
    profile = _make_profile(
        route_profile="primary_with_shadow_and_control",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    d = profile.to_json_dict()
    assert d["route_profile"] == "primary_with_shadow_and_control"
    assert d["enabled_shadow_paths"] == ["B.companion"]
    assert d["control_path"] == "C.rule"


def test_inference_route_profile_with_distribution_targets() -> None:
    target = DistributionTarget(
        channel="shadow_audit_jsonl", include_paths=["B"], mode="audit_only"
    )
    profile = InferenceRouteProfile(
        profile_name="p",
        route_profile="primary_with_shadow",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=["B.companion"],
        distribution_targets=[target],
    )
    d = profile.to_json_dict()
    assert len(d["distribution_targets"]) == 1
    assert d["distribution_targets"][0]["channel"] == "shadow_audit_jsonl"


# ---------------------------------------------------------------------------
# save_inference_route_profile
# ---------------------------------------------------------------------------


def test_save_inference_route_profile_creates_file(tmp_path) -> None:
    profile = _make_profile()
    out = save_inference_route_profile(profile, tmp_path / "profile.json")
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["report_type"] == "inference_route_profile"
    assert data["profile_name"] == "test_profile"


def test_save_inference_route_profile_invalid_route_raises_value_error(tmp_path) -> None:
    profile = InferenceRouteProfile(
        profile_name="bad",
        route_profile="auto_switch",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=[],
    )
    with pytest.raises(ValueError, match="Invalid route_profile"):
        save_inference_route_profile(profile, tmp_path / "bad.json")


def test_save_inference_route_profile_rejects_non_primary_active_path(tmp_path) -> None:
    profile = InferenceRouteProfile(
        profile_name="bad_primary",
        route_profile="primary_only",
        active_primary_path="B.companion",
        enabled_shadow_paths=[],
    )
    with pytest.raises(ValueError, match="active_primary_path"):
        save_inference_route_profile(profile, tmp_path / "bad_primary.json")


def test_save_inference_route_profile_rejects_mismatched_shadow_control_combo(
    tmp_path,
) -> None:
    profile = InferenceRouteProfile(
        profile_name="bad_combo",
        route_profile="primary_with_shadow",
        active_primary_path="A.external_llm",
        enabled_shadow_paths=[],
        control_path="C.rule",
    )
    with pytest.raises(ValueError, match="primary_with_shadow"):
        save_inference_route_profile(profile, tmp_path / "bad_combo.json")


def test_save_inference_route_profile_all_valid_modes(tmp_path) -> None:
    for mode in VALID_ROUTE_PROFILES:
        profile = _make_profile(route_profile=mode)
        if mode == "primary_with_shadow":
            profile.enabled_shadow_paths = ["B.companion"]
        elif mode == "primary_with_control":
            profile.control_path = "C.rule"
        elif mode == "primary_with_shadow_and_control":
            profile.enabled_shadow_paths = ["B.companion"]
            profile.control_path = "C.rule"
        out = save_inference_route_profile(profile, tmp_path / f"{mode}.json")
        assert out.exists()


# ---------------------------------------------------------------------------
# load_inference_route_profile (roundtrip)
# ---------------------------------------------------------------------------


def test_load_inference_route_profile_roundtrip(tmp_path) -> None:
    target = DistributionTarget(
        channel="research_brief",
        include_paths=["A"],
        mode="primary_only",
        artifact_path="outputs/brief.json",
    )
    original = InferenceRouteProfile(
        profile_name="roundtrip_profile",
        route_profile="primary_with_shadow",
        active_primary_path="A.internal",
        enabled_shadow_paths=["B.companion"],
        control_path=None,
        distribution_targets=[target],
        notes=["Sprint 14 test note"],
    )
    path = save_inference_route_profile(original, tmp_path / "rt.json")
    loaded = load_inference_route_profile(path)

    assert loaded.profile_name == original.profile_name
    assert loaded.route_profile == original.route_profile
    assert loaded.active_primary_path == original.active_primary_path
    assert loaded.enabled_shadow_paths == original.enabled_shadow_paths
    assert loaded.control_path == original.control_path
    assert loaded.notes == original.notes
    assert len(loaded.distribution_targets) == 1
    assert loaded.distribution_targets[0].channel == "research_brief"
    assert loaded.distribution_targets[0].artifact_path == "outputs/brief.json"
