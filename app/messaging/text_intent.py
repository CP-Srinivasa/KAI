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

import httpx

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
        if not self._api_key:
            return _NOT_CONFIGURED

        # Build user message with optional context
        if context:
            user_content = (
                f"Aktueller KAI-Systemkontext:\n{context}\n\n"
                f"Operator-Nachricht: {text}"
            )
        else:
            user_content = text

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
            "max_tokens": 800,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                return IntentResult(
                    intent=parsed.get("intent", "chat"),
                    response=parsed.get("response", "Keine Antwort generiert."),
                    signal=parsed.get("signal"),
                    mapped_command=parsed.get("mapped_command"),
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("[TEXT_INTENT] Processing error: %s", exc)
            return _FALLBACK
