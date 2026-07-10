"""Shadow use cases (ADR 0015 Phase 2) — read-only, untrusted analysis.

Each use case: allowlisted context -> redacted prompt -> TaskRouter -> LLMResult.
Output is rendered by CALLERS as a clearly marked untrusted block; nothing here
writes outside artifacts/ and nothing feeds execution.
"""

from __future__ import annotations

from pathlib import Path

from app.intelligence.context import BuiltContext, ContextBuilder
from app.intelligence.core import LLMResult
from app.intelligence.router import TaskRouter

_PROMPTS = {
    "daily_review_summary": (
        "Du bist ein nüchterner Analyse-Assistent. Fasse den folgenden KAI-Daily-"
        "Review zusammen: max. 5 Sätze summary, offene Punkte als caveats. "
        "Erfinde NICHTS; zitiere nur, was in den Dokumenten steht; nenne die "
        "Dokumentpfade, auf die du dich stützt, im Feld evidence."
    ),
    "anomaly_explain": (
        "Du bist ein nüchterner Ops-Assistent. Erkläre die wahrscheinlichste "
        "Ursache der folgenden Timer-/Source-Anomalie NUR anhand der Dokumente. "
        "Unsicherheiten explizit als caveats; evidence = Dokumentpfade."
    ),
    "doc_qa": (
        "Beantworte die Frage AUSSCHLIESSLICH aus den beigefügten ADR-/Runbook-"
        "Dokumenten. Wenn die Antwort dort nicht steht: summary='nicht in den "
        "Dokumenten belegt'. evidence = Dokumentpfade."
    ),
}


def _run(
    task_type: str,
    doc_paths: list[str],
    extra: str = "",
    *,
    workspace_root: Path | None = None,
    router: TaskRouter | None = None,
) -> tuple[LLMResult, BuiltContext]:
    active_router = router or TaskRouter()
    # Allowlist MUST come from the same settings as the router — a caller-injected
    # router with eigener Config darf nicht stillschweigend von den Guards abweichen.
    settings = active_router.settings
    builder = ContextBuilder(workspace_root or Path.cwd(), settings.allowlist_paths())
    context = builder.build(doc_paths)
    prompt = f"{_PROMPTS[task_type]}\n\n{extra}\n\n{context.text}".strip()
    result = active_router.run(
        task_type,
        prompt,
        input_refs=context.input_refs,
        redaction_count=context.redaction_count,
    )
    return result, context


def daily_review_summary(
    review_path: str, *, workspace_root: Path | None = None, router: TaskRouter | None = None
) -> LLMResult:
    return _run(
        "daily_review_summary", [review_path], workspace_root=workspace_root, router=router
    )[0]


def anomaly_explain(
    doc_paths: list[str],
    anomaly_description: str,
    *,
    workspace_root: Path | None = None,
    router: TaskRouter | None = None,
) -> LLMResult:
    return _run(
        "anomaly_explain",
        doc_paths,
        f"ANOMALIE: {anomaly_description}",
        workspace_root=workspace_root,
        router=router,
    )[0]


def doc_qa(
    doc_paths: list[str],
    question: str,
    *,
    workspace_root: Path | None = None,
    router: TaskRouter | None = None,
) -> LLMResult:
    return _run(
        "doc_qa", doc_paths, f"FRAGE: {question}", workspace_root=workspace_root, router=router
    )[0]


def render_untrusted_block(result: LLMResult) -> str:
    """Canonical human-facing rendering: schema fields only, clearly marked."""
    if not result.ok or result.data is None:
        return f"[LLM-SHADOW unavailable: {result.fallback_reason} (provider={result.provider})]"
    lines = [
        "[LLM-SHADOW — UNTRUSTED ANALYSIS, nicht handlungsleitend]",
        f"summary: {result.data['summary']}",
        f"confidence: {result.data['confidence']}",
        f"evidence: {', '.join(result.data['evidence']) or '—'}",
    ]
    for caveat in result.data.get("caveats", []):
        lines.append(f"caveat: {caveat}")
    return "\n".join(lines)
