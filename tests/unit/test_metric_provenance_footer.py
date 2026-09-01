"""STAB-2026-09-01 §29 — every decision-relevant metric states its population.

The contract carried a value, a window and a source artifact, but not the two
things that make a percentage checkable: its numerator and its denominator. Five
numbers therefore stood side by side on the surface with no way to tell whether
their denominators differed for a reason or by accident:

    73.49 %  over 166 resolved          (all-time directional precision)
    72.41 %  over 116, 84 hits          (forward window — a strict subset)
    72.84 %  over the active split
    73.49 %  over the P10 tier          (identical to the first, which is a finding)
    19890    annotation ROWS            (not a rate, not a denominator)

Differing numbers are fine. UNEXPLAINED differing numbers are not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.observability.dashboard_precision_metrics import precision_metric_contracts
from app.observability.metric_provenance import (
    artifact_sha256,
    code_sha,
    config_sha256,
    metric_contract,
)

REQUIRED_FIELDS = (
    "population_id",
    "window_start_utc",
    "window_end_utc",
    "sample_size",
    "numerator",
    "denominator",
    "computed_at_utc",
    "source_artifact",
    "source_artifact_sha256",
    "code_sha",
    "config_sha256",
    "status",
    "status_reason",
)


def _contract(**over):
    base = {
        "value": 73.49,
        "unit": "percent",
        "semantic_type": "directional_precision",
        "scope": "all_time_resolved",
        "source_artifact": Path("pyproject.toml"),
        "generated_at": "2026-09-01T12:00:00Z",
        "artifact_updated_at": lambda _p: "2026-09-01T11:00:00Z",
        "artifact_stale_status": lambda _p: "ok",
        "population_id": "directional_alerts_resolved_all_time",
        "numerator": 122,
        "denominator": 166,
        "since": "2026-01-01T00:00:00Z",
        "until": "2026-09-01T12:00:00Z",
    }
    base.update(over)
    return metric_contract(**base)


# --------------------------------------------------------------------------
# The footer contract
# --------------------------------------------------------------------------
@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_every_required_provenance_field_is_present(field: str) -> None:
    assert field in _contract()


def test_the_denominator_is_carried_not_implied() -> None:
    c = _contract()
    assert c["numerator"] == 122
    assert c["denominator"] == 166
    assert c["population_id"] == "directional_alerts_resolved_all_time"


def test_code_and_config_are_pinned() -> None:
    c = _contract()
    # In a git checkout both resolve; the contract must at least carry the keys
    # and never a placeholder that looks like a value.
    assert c["code_sha"] == code_sha()
    assert c["config_sha256"] == config_sha256()
    assert c["code_sha"] != "unknown"
    assert c["config_sha256"] != "unknown"


def test_the_artifact_is_pinned_by_content_not_only_by_path() -> None:
    c = _contract()
    assert c["source_artifact_sha256"] == artifact_sha256(Path("pyproject.toml"))
    assert len(c["source_artifact_sha256"]) == 64


def test_an_unreadable_artifact_yields_empty_not_a_fake_digest() -> None:
    """NEGATIVE CONTROL: a missing file must not produce a plausible-looking hash."""
    assert artifact_sha256(Path("definitely_not_here.jsonl")) == ""
    c = _contract(source_artifact=Path("definitely_not_here.jsonl"))
    assert c["source_artifact_sha256"] == ""


def test_status_reason_falls_back_to_the_warning_not_to_silence() -> None:
    c = _contract(quality_status="warning", warning="no fills in the window")
    assert c["status"] == "warning"
    assert c["status_reason"] == "no fills in the window"


# --------------------------------------------------------------------------
# The five named numbers
# --------------------------------------------------------------------------
def _precision_contracts():
    quality = {
        "precision_pct": 73.49,
        "hits": 122,
        "resolved_count": 166,
        "forward_precision_pct": 72.41,
        "forward_hits": 84,
        "forward_resolved": 116,
        "active_precision_pct": 72.84,
        "active_hits": 80,
        "active_resolved": 110,
        "high_priority_hit_rate_pct": 73.49,
        "high_priority_hits": 61,
        "high_priority_resolved": 83,
        "annotations_total": 19890,
    }

    def contract(**kw):
        return metric_contract(
            artifact_updated_at=lambda _p: None,
            artifact_stale_status=lambda _p: "ok",
            **kw,
        )

    return precision_metric_contracts(
        quality=quality,
        generated_at="2026-09-01T12:00:00Z",
        outcomes_artifact=Path("pyproject.toml"),
        contract=contract,
    )


def test_all_five_named_numbers_have_a_population() -> None:
    """UNEXPLAINED_DENOMINATOR_DIFF = 0 for the values the brief called out."""
    contracts = _precision_contracts()
    expected = {
        "directional_precision_pct",
        "forward_precision_pct",
        "active_precision_pct",
        "high_priority_hit_rate_pct",
        "annotation_rows_total",
    }
    assert expected <= set(contracts)
    for name, c in contracts.items():
        assert c["population_id"], f"{name} has no population_id"
        assert c["denominator"] is not None, f"{name} has no denominator"


def test_the_two_identical_percentages_are_distinguishable_by_population() -> None:
    """73.49 % appears twice. Without a population that reads as one number."""
    c = _precision_contracts()
    a = c["directional_precision_pct"]
    b = c["high_priority_hit_rate_pct"]
    assert a["value"] == b["value"] == 73.49
    assert a["population_id"] != b["population_id"]
    assert a["denominator"] != b["denominator"]


def test_the_forward_window_is_declared_a_subset() -> None:
    c = _precision_contracts()
    fwd = c["forward_precision_pct"]
    allt = c["directional_precision_pct"]
    assert fwd["denominator"] < allt["denominator"]
    assert "subset" in (fwd["explanation"] or "").lower()


def test_the_annotation_count_is_marked_as_not_a_rate() -> None:
    """19890 is a row count and must never be read as a precision denominator."""
    c = _precision_contracts()["annotation_rows_total"]
    assert c["unit"] == "count"
    assert c["is_decision_relevant"] is False
    assert "not a rate" in (c["explanation"] or "").lower()
