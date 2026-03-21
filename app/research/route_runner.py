"""Route-driven shadow/control inference runner for analyze-pending (I-92, I-93).

Sprint 17 — wires ActiveRouteState into the analyze-pending pipeline.
Primary path writes to DB only (I-92). Shadow/control write to audit JSONL only (I-93).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.domain.document import AnalysisResult
from app.research.abc_result import (
    ABCInferenceEnvelope,
    DistributionMetadata,
    PathComparisonSummary,
    PathResultEnvelope,
)
from app.research.active_route import ActiveRouteState

if TYPE_CHECKING:
    from app.analysis.base.interfaces import BaseAnalysisProvider
    from app.core.domain.document import CanonicalDocument

def map_path_to_provider_name(path_id: str) -> str:
    """Extract provider name from a path_id string.

    Examples:
      'A.external_llm' → 'external_llm'
      'B.companion'    → 'companion'
      'C.rule'         → 'rule'
      'companion'      → 'companion'  (no dot: returned as-is)
    """
    if "." in path_id:
        return path_id.split(".", 1)[1]
    return path_id


def _path_analysis_source(path_id: str) -> str:
    """Map a full path_id to an analysis_source string.

    `A` may legally host `external_llm`, `internal`, or `rule`, so using only the
    path letter would misclassify `A.internal` and `A.rule`.
    """
    provider_name = map_path_to_provider_name(path_id).strip().lower()
    if provider_name in {"rule", "fallback"}:
        return "rule"
    if provider_name in {"internal", "companion"}:
        return "internal"
    if provider_name in {"external_llm", "openai", "anthropic", "gemini"}:
        return "external_llm"
    return "unknown"


def build_path_result_from_llm_output(
    path_id: str,
    provider_name: str,
    llm_output: LLMAnalysisOutput | None,
    error: str | None = None,
) -> PathResultEnvelope:
    """Build a PathResultEnvelope from a raw LLMAnalysisOutput (shadow/control paths)."""
    scores: dict[str, object] = {}
    summary: str | None = None
    if llm_output is not None:
        scores = {
            "recommended_priority": llm_output.recommended_priority,
            "sentiment_label": str(llm_output.sentiment_label),
            "relevance_score": llm_output.relevance_score,
            "impact_score": llm_output.impact_score,
            "actionable": llm_output.actionable,
        }
        summary = llm_output.short_reasoning
    return PathResultEnvelope(
        path_id=path_id,
        provider=provider_name,
        analysis_source=_path_analysis_source(path_id),
        summary=f"error: {error}" if error else summary,
        scores=scores,
    )


def build_path_result_from_analysis_result(
    path_id: str,
    provider_name: str,
    analysis_result: AnalysisResult | None,
) -> PathResultEnvelope:
    """Build a PathResultEnvelope from a normalized AnalysisResult (primary path)."""
    scores: dict[str, object] = {}
    summary: str | None = None
    if analysis_result is not None:
        scores = {
            "recommended_priority": analysis_result.recommended_priority,
            "sentiment_label": str(analysis_result.sentiment_label),
            "relevance_score": analysis_result.relevance_score,
            "impact_score": analysis_result.impact_score,
            "actionable": analysis_result.actionable,
        }
        summary = analysis_result.explanation_short or None
    return PathResultEnvelope(
        path_id=path_id,
        provider=provider_name,
        analysis_source=_path_analysis_source(path_id),
        summary=summary,
        scores=scores,
    )


def build_comparison_summaries(
    primary_result: PathResultEnvelope,
    shadow_results: list[PathResultEnvelope],
    control_result: PathResultEnvelope | None,
) -> list[PathComparisonSummary]:
    """Build compact A-vs-B and A-vs-C deviation summaries."""
    comparisons: list[PathComparisonSummary] = []
    others = list(shadow_results)
    if control_result is not None:
        others.append(control_result)

    p = primary_result.scores
    for other in others:
        o = other.scores
        letter = other.path_id.split(".")[0].upper() if "." in other.path_id else "X"
        label = f"A_vs_{letter}"

        sentiment_match: bool | None = None
        actionable_match: bool | None = None
        if p and o:
            s_p = p.get("sentiment_label")
            s_o = o.get("sentiment_label")
            if s_p is not None and s_o is not None:
                sentiment_match = s_p == s_o
            a_p = p.get("actionable")
            a_o = o.get("actionable")
            if a_p is not None and a_o is not None:
                actionable_match = a_p == a_o

        deviations: dict[str, float] = {}
        for key in ("recommended_priority", "relevance_score", "impact_score"):
            p_val = p.get(key)
            o_val = o.get(key)
            if isinstance(p_val, (int, float)) and isinstance(o_val, (int, float)):
                deviations[f"{key}_delta"] = round(abs(float(p_val) - float(o_val)), 4)

        comparisons.append(
            PathComparisonSummary(
                compared_path=label,
                sentiment_match=sentiment_match,
                actionable_match=actionable_match,
                deviations=deviations,
            )
        )
    return comparisons


def build_abc_envelope(
    document_id: str,
    route_state: ActiveRouteState,
    primary_provider_name: str,
    primary_analysis_result: AnalysisResult | None,
    shadow_outcomes: list[tuple[str, str, LLMAnalysisOutput | None, str | None]],
    control_outcome: tuple[str, str, LLMAnalysisOutput | None, str | None] | None,
) -> ABCInferenceEnvelope:
    """Build an ABCInferenceEnvelope from multi-path analysis results (I-88, I-93).

    shadow_outcomes: list of (path_id, provider_name, llm_output, error)
    control_outcome: (path_id, provider_name, llm_output, error) | None

    Does NOT write to DB (I-93). Caller must append result to audit JSONL.
    """
    primary_result = build_path_result_from_analysis_result(
        path_id=route_state.active_primary_path,
        provider_name=primary_provider_name,
        analysis_result=primary_analysis_result,
    )

    shadow_results = [
        build_path_result_from_llm_output(path_id, provider_name, llm_output, error)
        for path_id, provider_name, llm_output, error in shadow_outcomes
    ]

    control_result: PathResultEnvelope | None = None
    if control_outcome is not None:
        c_path_id, c_provider, c_llm, c_error = control_outcome
        control_result = build_path_result_from_llm_output(
            c_path_id, c_provider, c_llm, c_error
        )

    comparison_summary = build_comparison_summaries(
        primary_result, shadow_results, control_result
    )

    distribution_metadata = DistributionMetadata(
        route_profile=route_state.route_profile,
        active_primary_path=route_state.active_primary_path,
        # "active" — Sprint 17: route is live (operator-activated via route-activate).
        # Contrast with Sprint 14 abc-run which sets "audit_only" for post-hoc construction.
        activation_state="active",
    )

    return ABCInferenceEnvelope(
        document_id=document_id,
        route_profile=route_state.route_profile,
        primary_result=primary_result,
        shadow_results=shadow_results,
        control_result=control_result,
        comparison_summary=comparison_summary,
        distribution_metadata=distribution_metadata,
    )


async def run_route_provider(
    provider: BaseAnalysisProvider,
    document: CanonicalDocument,
) -> tuple[LLMAnalysisOutput | None, str | None]:
    """Run a shadow/control provider against a document. Returns (output, error).

    Never raises — all exceptions are captured as error strings (I-92: primary path unaffected).
    """
    try:
        output = await provider.analyze(
            title=document.title or "",
            text=document.cleaned_text or document.raw_text or "",
        )
        return output, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
