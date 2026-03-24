"""A/B/C inference result envelope for audit-safe composition.

[EXPERIMENTAL — ONLY ACTIVE IN SHADOW/CONTROL ROUTE MODES]
ABCInferenceEnvelope is only instantiated when a multi-path route profile
(primary_with_shadow, primary_with_control, or primary_with_shadow_and_control)
is explicitly activated via the MCP route-activate tool. In the default
primary_only production mode this module is never called during normal
analyze-pending runs.

ABCInferenceEnvelope is a pure composition artifact.
Creating or saving an envelope MUST NOT call analyze(), apply_to_document(),
update_analysis(), or any DB mutation (I-88).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PathResultEnvelope:
    """Single-path result reference for A, B, or C inference path."""

    path_id: str  # "A.external_llm" | "B.companion" | "C.rule"
    provider: str  # actual provider name
    analysis_source: str  # "external_llm" | "internal" | "rule"
    result_ref: str | None = None  # path to persisted artifact (e.g. shadow JSONL)
    summary: str | None = None
    scores: dict[str, object] = field(default_factory=dict)
    # scores may include: priority_score, sentiment_label, relevance_score,
    #                     impact_score, actionable, tags


@dataclass
class PathComparisonSummary:
    """Compact comparison between A and B/C path results."""

    compared_path: str  # e.g. "A_vs_B" | "A_vs_C"
    sentiment_match: bool | None = None
    actionable_match: bool | None = None
    tag_overlap: float | None = None
    deviations: dict[str, float] = field(default_factory=dict)
    # deviations: priority_delta, relevance_delta, impact_delta
    comparison_report_path: str | None = None  # link to EvaluationComparisonReport


@dataclass
class DistributionMetadata:
    """Informational metadata about distribution routing. Never auto-activates (I-87)."""

    route_profile: str
    active_primary_path: str
    distribution_targets: list[str] = field(default_factory=list)
    # distribution_targets: list of channel names that received this envelope
    decision_owner: str = "operator"  # always "operator" — no auto-decision
    activation_state: str = "audit_only"  # fixed in Sprint 14


@dataclass
class ABCInferenceEnvelope:
    """Audit-safe envelope wrapping primary, shadow, and control path results.

    Pure composition artifact — no DB writes, no routing changes (I-88).
    """

    document_id: str
    route_profile: str
    primary_result: PathResultEnvelope
    shadow_results: list[PathResultEnvelope] = field(default_factory=list)
    control_result: PathResultEnvelope | None = None
    comparison_summary: list[PathComparisonSummary] = field(default_factory=list)
    distribution_metadata: DistributionMetadata | None = None

    def to_json_dict(self) -> dict[str, object]:
        def _envelope(e: PathResultEnvelope) -> dict[str, object]:
            return {
                "path_id": e.path_id,
                "provider": e.provider,
                "analysis_source": e.analysis_source,
                "result_ref": e.result_ref,
                "summary": e.summary,
                "scores": e.scores,
            }

        def _comparison(c: PathComparisonSummary) -> dict[str, object]:
            return {
                "compared_path": c.compared_path,
                "sentiment_match": c.sentiment_match,
                "actionable_match": c.actionable_match,
                "tag_overlap": c.tag_overlap,
                "deviations": c.deviations,
                "comparison_report_path": c.comparison_report_path,
            }

        return {
            "report_type": "abc_inference_envelope",
            "document_id": self.document_id,
            "route_profile": self.route_profile,
            "primary_result": _envelope(self.primary_result),
            "shadow_results": [_envelope(s) for s in self.shadow_results],
            "control_result": (_envelope(self.control_result) if self.control_result else None),
            "comparison_summary": [_comparison(c) for c in self.comparison_summary],
            "distribution_metadata": (
                {
                    "route_profile": self.distribution_metadata.route_profile,
                    "active_primary_path": self.distribution_metadata.active_primary_path,
                    "distribution_targets": self.distribution_metadata.distribution_targets,
                    "decision_owner": self.distribution_metadata.decision_owner,
                    "activation_state": self.distribution_metadata.activation_state,
                }
                if self.distribution_metadata
                else None
            ),
        }


def _require_str(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_str(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string or null")
    return value


def _optional_bool(value: object, *, label: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a bool or null")
    return value


def _optional_float(value: object, *, label: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric or null")
    return float(value)


def _as_object_dict(value: object, *, label: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _as_string_list(value: object, *, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")

    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{label} entries must be strings")
        result.append(item)
    return result


def _as_float_dict(value: object, *, label: str) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")

    result: dict[str, float] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, (int, float)):
            raise ValueError(f"{label} entries must be numeric")
        result[key] = float(item)
    return result


def _parse_path_result_envelope(value: object, *, label: str) -> PathResultEnvelope:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return PathResultEnvelope(
        path_id=_require_str(value.get("path_id"), label=f"{label}.path_id"),
        provider=_require_str(value.get("provider"), label=f"{label}.provider"),
        analysis_source=_require_str(
            value.get("analysis_source"), label=f"{label}.analysis_source"
        ),
        result_ref=_optional_str(value.get("result_ref"), label=f"{label}.result_ref"),
        summary=_optional_str(value.get("summary"), label=f"{label}.summary"),
        scores=_as_object_dict(value.get("scores"), label=f"{label}.scores"),
    )


def _parse_comparison_summary(value: object, *, label: str) -> PathComparisonSummary:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return PathComparisonSummary(
        compared_path=_require_str(value.get("compared_path"), label=f"{label}.compared_path"),
        sentiment_match=_optional_bool(
            value.get("sentiment_match"), label=f"{label}.sentiment_match"
        ),
        actionable_match=_optional_bool(
            value.get("actionable_match"), label=f"{label}.actionable_match"
        ),
        tag_overlap=_optional_float(value.get("tag_overlap"), label=f"{label}.tag_overlap"),
        deviations=_as_float_dict(value.get("deviations"), label=f"{label}.deviations"),
        comparison_report_path=_optional_str(
            value.get("comparison_report_path"),
            label=f"{label}.comparison_report_path",
        ),
    )


def _parse_distribution_metadata(
    value: object,
    *,
    label: str,
) -> DistributionMetadata | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object or null")
    return DistributionMetadata(
        route_profile=_require_str(value.get("route_profile"), label=f"{label}.route_profile"),
        active_primary_path=_require_str(
            value.get("active_primary_path"), label=f"{label}.active_primary_path"
        ),
        distribution_targets=_as_string_list(
            value.get("distribution_targets"), label=f"{label}.distribution_targets"
        ),
        decision_owner=_optional_str(value.get("decision_owner"), label=f"{label}.decision_owner")
        or "operator",
        activation_state=_optional_str(
            value.get("activation_state"), label=f"{label}.activation_state"
        )
        or "audit_only",
    )


def abc_inference_envelope_from_dict(payload: dict[str, object]) -> ABCInferenceEnvelope:
    """Rehydrate a persisted ABCInferenceEnvelope without changing runtime behavior."""
    primary_payload = payload.get("primary_result")
    if not isinstance(primary_payload, dict):
        raise ValueError("primary_result must be an object")

    shadow_payload = payload.get("shadow_results")
    if shadow_payload is None:
        shadow_results: list[PathResultEnvelope] = []
    elif isinstance(shadow_payload, list):
        shadow_results = [
            _parse_path_result_envelope(item, label="shadow_results[]") for item in shadow_payload
        ]
    else:
        raise ValueError("shadow_results must be a list")

    comparison_payload = payload.get("comparison_summary")
    if comparison_payload is None:
        comparison_summary: list[PathComparisonSummary] = []
    elif isinstance(comparison_payload, list):
        comparison_summary = [
            _parse_comparison_summary(item, label="comparison_summary[]")
            for item in comparison_payload
        ]
    else:
        raise ValueError("comparison_summary must be a list")

    return ABCInferenceEnvelope(
        document_id=_require_str(payload.get("document_id"), label="document_id"),
        route_profile=_require_str(payload.get("route_profile"), label="route_profile"),
        primary_result=_parse_path_result_envelope(primary_payload, label="primary_result"),
        shadow_results=shadow_results,
        control_result=_parse_path_result_envelope(
            payload.get("control_result"), label="control_result"
        )
        if payload.get("control_result") is not None
        else None,
        comparison_summary=comparison_summary,
        distribution_metadata=_parse_distribution_metadata(
            payload.get("distribution_metadata"), label="distribution_metadata"
        ),
    )


def load_abc_inference_envelopes(path: Path | str) -> list[ABCInferenceEnvelope]:
    """Load persisted ABCInferenceEnvelope artifacts from JSON or JSONL."""
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"ABC inference envelope file not found: {source_path}")

    if source_path.suffix.lower() == ".json":
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid ABC envelope JSON: {source_path}") from exc
        if not isinstance(payload, dict):
            raise ValueError("ABC envelope JSON must be an object")
        return [abc_inference_envelope_from_dict(dict(payload))]

    envelopes: list[ABCInferenceEnvelope] = []
    for line_no, raw_line in enumerate(source_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid ABC envelope JSONL at line {line_no}: {source_path}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"ABC envelope row {line_no} must be an object")
        envelopes.append(abc_inference_envelope_from_dict(dict(payload)))
    return envelopes


def save_abc_inference_envelope(
    envelope: ABCInferenceEnvelope,
    output_path: Path | str,
) -> Path:
    """Write a single ABCInferenceEnvelope to JSON. Does NOT write to the DB (I-88)."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(envelope.to_json_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return out


def save_abc_inference_envelope_jsonl(
    envelopes: list[ABCInferenceEnvelope],
    output_path: Path | str,
) -> Path:
    """Append multiple ABCInferenceEnvelopes to a JSONL file (I-38: no in-place overwrite).

    Note: concurrent calls from parallel analyze-pending processes may interleave lines.
    This is acceptable for audit JSONL (each line is a self-contained JSON object).
    Do NOT run parallel analyze-pending sessions targeting the same output file.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for envelope in envelopes:
            f.write(json.dumps(envelope.to_json_dict()) + "\n")
    return out
