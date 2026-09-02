"""Free-text intent processing for Telegram operator messages.

Classifies incoming text into intents and generates contextual responses
using an LLM (OpenAI).  Supports signal input, market queries, and
natural-language command mapping.

When a ``context`` string is supplied (e.g. recent analysis summaries),
it is injected into the user message so the LLM can give data-backed
answers instead of generic disclaimers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from app.inference.mode import run_inference_mode
from app.inference.models import InferenceRoute
from app.inference.router import InferenceRouter

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Du bist KAI — ein professioneller, KI-gestuetzter Trading-Analyst \
und Operator-Assistent fuer Krypto- und Finanzmaerkte.

Du bist KEIN generischer Chatbot. Du bist ein spezialisiertes \
Analyse-System mit Zugang zu echten, aktuellen Marktanalysen. \
Antworte als Experte, direkt und fundiert. Gib keine generischen \
Haftungsausschluesse oder "als KI kann ich nicht"-Phrasen. \
Dein Operator ist ein erfahrener Trader, der fachliche Antworten erwartet.

Wenn dir Kontext aus dem KAI-System mitgegeben wird, nutze diesen \
fuer deine Antwort. Beziehe dich auf konkrete Analysen, Sentiments, \
Assets und Scores aus dem Kontext.

Analysiere die eingehende Nachricht und antworte als JSON:
{
  "intent": "signal" | "query" | "command" | "chat",
  "response": "Deine fachliche Antwort an den Operator",
  "signal": null | {"asset": "...", "direction": "bullish|bearish|neutral", \
"reasoning": "..."},
  "mapped_command": null | "<command_name>"
}

Verfuegbare Commands fuer mapped_command:
- "menu": Hauptmenue oeffnen (Menue, Navigation, Uebersicht)
- "status": System-Status anzeigen
- "positions": Portfolio-Positionen anzeigen
- "signals": Aktive Signale anzeigen
- "exposure": Risiko/Exposure anzeigen
- "alert_status": Alert-Status anzeigen
- "daily_summary": Tagesbericht/Zusammenfassung
- "pause": Trading pausieren
- "resume": Trading fortsetzen
- "help": Hilfe anzeigen

Intent-Regeln:
- "signal": Operator gibt ein Trading-Signal oder eine Markteinschaetzung.
  Extrahiere Asset, Richtung (bullish/bearish/neutral), Begruendung.
  Bestaetige das Signal professionell.
- "query": Operator stellt eine Frage (Markt, System, Analyse, Strategie).
  Antworte fachlich, konkret und mit Bezug zum Kontext wenn vorhanden.
  Wenn du aktuelle Analysedaten hast, nutze sie.
  Wenn du keine Daten hast, sage klar was du weisst und was nicht.
- "command": Operator gibt einen natuerlichsprachlichen Befehl.
  Setze mapped_command auf den passenden Systembefehl aus der Liste oben.
  Waehle den Command, der am besten zur Absicht des Operators passt.
- "chat": Allgemeine Konversation oder Begruessung.
  Antworte kurz und professionell.

Antworte auf Deutsch. Antworte NUR als gueltiges JSON. \
Kurz, fachlich, operativ nuetzlich.\
"""


@dataclass(frozen=True)
class IntentResult:
    """Result of LLM intent classification."""

    intent: str  # signal, query, command, chat
    response: str
    signal: dict[str, str] | None = None
    mapped_command: str | None = None


class _IntentSignal(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    asset: str
    direction: Literal["bullish", "bearish", "neutral"]
    reasoning: str


class _IntentPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    intent: Literal["signal", "query", "command", "chat"]
    response: str
    signal: _IntentSignal | None = None
    mapped_command: (
        Literal[
            "menu",
            "status",
            "positions",
            "signals",
            "exposure",
            "alert_status",
            "daily_summary",
            "pause",
            "resume",
            "help",
        ]
        | None
    ) = None


_FALLBACK = IntentResult(
    intent="chat",
    response="Entschuldigung, ich konnte die Nachricht nicht verarbeiten.",
)

_NOT_CONFIGURED = IntentResult(
    intent="chat",
    response="Freitext-Verarbeitung ist nicht konfiguriert (API-Key fehlt).",
)


class TextIntentProcessor:
    """Processes free-text Telegram messages via LLM intent classification."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        timeout: int = 30,
        inference_router: InferenceRouter | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._inference_router = inference_router

    @property
    def is_configured(self) -> bool:
        if self._inference_router is not None:
            if self._inference_router.settings.effective_mode == "primary":
                return True
        return bool(self._api_key)

    async def process(self, text: str, context: str = "") -> IntentResult:
        """Classify *text* and return an ``IntentResult``.

        Parameters
        ----------
        text:
            The operator message to process.
        context:
            Optional context string (e.g. recent analyses) injected into
            the user message so the LLM can give data-backed answers.
        """
        mode = (
            self._inference_router.settings.effective_mode
            if self._inference_router is not None
            else "off"
        )
        if not self._api_key and mode != "primary":
            return _NOT_CONFIGURED

        # Build user message with optional context
        if context:
            user_content = f"Aktueller KAI-Systemkontext:\n{context}\n\nOperator-Nachricht: {text}"
        else:
            user_content = text

        try:

            async def gateway_call() -> IntentResult:
                return await self._gateway_process(
                    user_content,
                    role="shadow" if mode == "shadow" else "primary",
                )

            async def legacy_call() -> IntentResult:
                return await self._direct_process(user_content)

            return await run_inference_mode(
                mode=mode,
                gateway_call=gateway_call,
                legacy_call=legacy_call if self._api_key else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[TEXT_INTENT] Processing error: %s", exc)
            return _FALLBACK

    async def _direct_process(self, user_content: str) -> IntentResult:
        # Legacy direct path is retained exactly for mode=off and rollback.
        client = AsyncOpenAI(api_key=self._api_key, timeout=self._timeout)
        resp = await client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=800,
        )
        content = resp.choices[0].message.content or ""
        parsed = _IntentPayload.model_validate(json.loads(content), strict=True)
        return self._to_result(parsed)

    async def _gateway_process(self, user_content: str, *, role: str) -> IntentResult:
        assert self._inference_router is not None
        result = await self._inference_router.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            route=InferenceRoute.STANDARD,
            response_model=_IntentPayload,
            role=role,
            max_tokens=800,
            temperature=0.3,
        )
        if result.parsed is None:
            raise ValueError("gateway returned no validated intent payload")
        return self._to_result(result.parsed)

    @staticmethod
    def _to_result(payload: _IntentPayload) -> IntentResult:
        return IntentResult(
            intent=payload.intent,
            response=payload.response,
            signal=(
                {key: str(value) for key, value in payload.signal.model_dump().items()}
                if payload.signal is not None
                else None
            ),
            mapped_command=payload.mapped_command,
        )
