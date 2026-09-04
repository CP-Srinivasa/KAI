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
from typing import Any

from openai import AsyncOpenAI

from app.ai.audit import llm_call_scope
from app.ai.runtime import LiteLLMRequest, invoke

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
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def process(
        self, text: str, context: str = "", *, correlation_id: str | None = None
    ) -> IntentResult:
        """Classify *text* and return an ``IntentResult``.

        Parameters
        ----------
        text:
            The operator message to process.
        context:
            Optional context string (e.g. recent analyses) injected into
            the user message so the LLM can give data-backed answers.
        correlation_id:
            Optional request id from the caller. Keyword-only and optional, so
            every existing call site stays valid; when absent the audit scope
            generates one (NEO-F-008).
        """
        if not self._api_key:
            return _NOT_CONFIGURED

        # Build user message with optional context
        if context:
            user_content = f"Aktueller KAI-Systemkontext:\n{context}\n\nOperator-Nachricht: {text}"
        else:
            user_content = text

        messages: list[Any] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        def parse_content(content: str) -> dict[str, object]:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("intent payload is not a JSON object")
            return parsed

        def parse_litellm(body: dict[str, object]) -> dict[str, object]:
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("intent response has no choices")
            message = choices[0].get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                raise ValueError("intent response has no content")
            return parse_content(content)

        async def direct_call() -> dict[str, object]:
            client = AsyncOpenAI(api_key=self._api_key, timeout=self._timeout)
            async with llm_call_scope(
                purpose="intent",
                provider="openai",
                model=self._model,
                correlation_id=correlation_id,
            ) as scope:
                resp = await client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.3,
                    max_tokens=800,
                )
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    scope.set_tokens(
                        getattr(usage, "prompt_tokens", 0),
                        getattr(usage, "completion_tokens", 0),
                    )
                content = resp.choices[0].message.content or ""
                return parse_content(content)

        try:
            routed = await invoke(
                purpose="intent",
                direct_call=direct_call,
                direct_provider="openai",
                direct_model=self._model,
                litellm=LiteLLMRequest(
                    parser=parse_litellm,
                    payload={
                        "messages": messages,
                        "response_format": {"type": "json_object"},
                        "temperature": 0.3,
                        "max_tokens": 800,
                    },
                ),
                correlation_id=correlation_id,
            )
            parsed = routed.value
            intent = parsed.get("intent")
            response = parsed.get("response")
            signal = parsed.get("signal")
            mapped_command = parsed.get("mapped_command")
            return IntentResult(
                intent=intent if isinstance(intent, str) else "chat",
                response=response if isinstance(response, str) else "Keine Antwort generiert.",
                signal=(
                    {str(key): str(value) for key, value in signal.items()}
                    if isinstance(signal, dict)
                    else None
                ),
                mapped_command=mapped_command if isinstance(mapped_command, str) else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[TEXT_INTENT] Processing error: %s", exc)
            return _FALLBACK
