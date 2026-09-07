"""Canonical JSON and presentation-only Markdown rendering."""

from __future__ import annotations

import json

from scripts.litellm_shadow_eval.models import EvaluationReport


def canonical_json(report: EvaluationReport) -> str:
    """Stable JSON: sorted keys, finite floats, no payload or prompt content."""
    return (
        json.dumps(
            report.to_dict(),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def comparable_json(report: EvaluationReport) -> str:
    """Kanonisches JSON OHNE ``generated_at`` -- fuer Determinismus-Vergleiche.

    Zwei Laeufe ueber dieselbe Evidenz muessen dasselbe Ergebnis liefern. Der
    Erzeugungszeitpunkt ist der einzige Teil des Berichts, der sich zwischen
    zwei solchen Laeufen legitim unterscheidet; ihn beim Vergleich mitzuzaehlen
    wuerde Determinismus unpruefbar machen. Er bleibt im echten Bericht stehen
    -- ein Bericht ohne Zeitpunkt waere nicht nachvollziehbar.
    """
    value = report.to_dict()
    value.pop("generated_at", None)
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def markdown_summary(report: EvaluationReport) -> str:
    lines = [
        "# LiteLLM Shadow Evidence Evaluation",
        "",
        f"- Tool version: `{report.tool_version}`",
        f"- Generated at: `{report.generated_at}`",
        f"- Policy hash: `{report.policy_hash}`",
        f"- Input SHA-256: `{report.input_sha256}`",
        f"- Records: `{report.record_count}`",
        f"- Invalid records: `{report.invalid_record_count}`",
        "",
        "| Route | Decision | Complete | Incomplete | Shadow success | Shadow schema | Quality |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for route in report.routes:
        metrics = report.metrics[route]
        decision = report.decisions[route]
        lines.append(
            "| "
            + " | ".join(
                [
                    route,
                    decision.status.value + ("" if decision.primary_ready else " (kein PRIMARY)"),
                    str(metrics.complete_pair_count),
                    str(metrics.incomplete_pair_count),
                    _display(metrics.shadow_success_rate),
                    _display(metrics.shadow_schema_valid_rate),
                    metrics.quality.status,
                ]
            )
            + " |"
        )
        if decision.reasons:
            lines.append(f"\nReasons `{route}`: " + ", ".join(decision.reasons))
    lines.extend(
        [
            "",
            "This report is advisory evidence only. It never activates PRIMARY.",
            "Consensus PRIMARY is always forbidden.",
            "",
        ]
    )
    return "\n".join(lines)


def _display(value: float | None) -> str:
    return "UNKNOWN" if value is None else f"{value:.6f}".rstrip("0").rstrip(".")


__all__ = ["canonical_json", "comparable_json", "markdown_summary"]
