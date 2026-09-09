"""Analysis adapter that puts the existing provider behind ``app.ai``."""

from __future__ import annotations

from typing import Any

from app.ai.audit import analysis_prompt_scope
from app.ai.config import InferenceSettings
from app.ai.runtime import LiteLLMRequest, invoke
from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.analysis.prompts import (
    ACTIVE_SYSTEM_PROMPT,
    ACTIVE_SYSTEM_PROMPT_SHA256,
    ACTIVE_SYSTEM_PROMPT_VERSION,
    format_user_prompt,
)

_MAX_TEXT_CHARS = 6000

#: Ausgabedeckel der Schattenanfrage.
#:
#: Bei denkenden Modellen ist das KEIN Ausgabedeckel: der Denkaufwand zaehlt
#: gegen dasselbe Budget wie die Antwort. Am 2026-09-09 auf kai-pi5 mit dieser
#: Nutzlast gemessen, vier reale Dokumente, `gemini/gemini-3.6-flash`:
#:
#:   Bedarf 814..1506 Completion-Token, davon 405..1136 Denken.
#:   VIER von acht Laeufen lagen ueber den 1024, die hier vorher standen.
#:
#: Ein zu kleiner Deckel schneidet mitten im JSON ab, und der Parser meldet
#: dann `Invalid JSON: EOF while parsing a string` -- wer das liest, sucht beim
#: Modell statt beim Budget. Der Wert hat deshalb Luft nach oben: ein hoher
#: Deckel kostet nichts, weil nur erzeugte Token bezahlt werden.
MAX_TOKENS = 4096


def parse_analysis_body(body: dict[str, Any], *, user_prompt: str) -> LLMAnalysisOutput:
    """Den Antwortkoerper in das Analyse-Schema lesen -- oder ehrlich scheitern.

    Frei von der Anfrage, damit die Abschnitt-Erkennung pruefbar ist, ohne
    einen Transport zu stellen.
    """
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LiteLLM analysis response has no choices")
    erste = choices[0] if isinstance(choices[0], dict) else {}
    message = erste.get("message")
    raw = message.get("content") if isinstance(message, dict) else None
    if not isinstance(raw, str) or not raw:
        raise ValueError("LiteLLM analysis response has no JSON content")

    # VOR dem Parsen: ein abgeschnittenes Ergebnis ist kein Formfehler des
    # Modells, sondern ein zu kleines Budget. Beides endet ohne Analyse, aber
    # nur eines davon behebt der Operator an der richtigen Stelle. Auch ein
    # zufaellig noch parsbares Ergebnis gilt hier als unvollstaendig -- eine
    # gekuerzte Analyse wie eine ganze zu behandeln waere schlimmer.
    if erste.get("finish_reason") == "length":
        raise ValueError(
            "LiteLLM analysis response was truncated (finish_reason=length) — "
            f"max_tokens={MAX_TOKENS} reicht nicht; bei denkenden Modellen zaehlt "
            "der Denkaufwand gegen dasselbe Budget"
        )

    output = LLMAnalysisOutput.model_validate_json(raw)
    output.raw_prompt = user_prompt
    output.raw_response = raw
    usage = body.get("usage")
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if isinstance(prompt_tokens, int):
            output.prompt_tokens = prompt_tokens
        if isinstance(completion_tokens, int):
            output.completion_tokens = completion_tokens
    return output


class ControlPlaneAnalysisProvider(BaseAnalysisProvider):
    """Preserve the direct provider while adding governed shadow/primary transport."""

    def __init__(
        self,
        direct: BaseAnalysisProvider,
        settings: InferenceSettings,
        *,
        force_off: bool = False,
    ) -> None:
        self._direct = direct
        self._settings = settings.model_copy(update={"enabled": False}) if force_off else settings

    @property
    def provider_name(self) -> str:
        return self._direct.provider_name

    @property
    def model(self) -> str | None:
        return self._direct.model

    def __getattr__(self, name: str) -> Any:
        """Preserve ensemble/runtime metadata used by the existing pipeline."""
        return getattr(self._direct, name)

    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput:
        user_prompt = format_user_prompt(
            title=title,
            text=text[:_MAX_TEXT_CHARS],
            context=context,
        )

        def parse(body: dict[str, Any]) -> LLMAnalysisOutput:
            return parse_analysis_body(body, user_prompt=user_prompt)

        with analysis_prompt_scope(
            version=ACTIVE_SYSTEM_PROMPT_VERSION, prompt_hash=ACTIVE_SYSTEM_PROMPT_SHA256
        ):
            routed = await invoke(
                purpose="analysis",
                direct_call=lambda: self._direct.analyze(title=title, text=text, context=context),
                direct_provider=self._direct.provider_name,
                direct_model=self._direct.model or "",
                litellm=LiteLLMRequest(
                    parser=parse,
                    payload={
                        "messages": [
                            {"role": "system", "content": ACTIVE_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": MAX_TOKENS,
                    },
                ),
                settings=self._settings,
            )
        output = routed.value
        if routed.transport == "litellm" and routed.outcome is not None:
            selected = routed.outcome.authoritative_attempt
            if selected is not None and selected.trace.identity_proven:
                output.provider_used = selected.trace.actual_provider
        return output


__all__ = ["MAX_TOKENS", "ControlPlaneAnalysisProvider", "parse_analysis_body"]
