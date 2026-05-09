"""KAI Chat Engine — Intent-Router + Hybrid-Responder.

Phase 2 (2026-05-09): Operator stellt im KaiLiveWidget Fragen, KAI antwortet.
Hybrid:
  - Trading-Intent  -> Read-only Tools (Portfolio, Positionen, PnL)
  - Smalltalk      -> GPT-4o im Persona-Stil (motto, traits, forbidden phrases)

Architektur-Constraints:
  - Keine Trading-State-Mutationen. Alles read-only (KAI-Audit ausgenommen).
  - Token-Budget GPT-4o: 150 Tokens output (knappe, KAI-typische Repliken).
  - Antwort-Sprache folgt der Anfrage-Sprache (de/en), Default de.
  - Auf Fehler im LLM-Call: deterministische Fallback-Phrase, kein Throw.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.core.settings import get_settings
from app.execution.portfolio_read import build_portfolio_snapshot
from app.messaging.kai_persona import KaiPersonaConfigError, load_kai_persona

logger = logging.getLogger(__name__)

ChatIntent = Literal["trading", "smalltalk"]
ChatSource = Literal["system", "gpt4o", "fallback"]


@dataclass(frozen=True)
class ChatReply:
    reply: str
    intent: ChatIntent
    source: ChatSource


_TRADING_KEYWORDS_DE = {
    "portfolio", "position", "positionen", "trade", "trades", "gewinn", "verlust",
    "pnl", "balance", "equity", "kasse", "cash", "bilanz", "btc", "eth", "kurs",
    "kurse", "monitor", "signal", "signale", "stop", "loss", "tier", "fill",
    "verdient", "verloren", "geschlossen", "offen", "exposure", "risiko",
    "buy", "sell", "kaufen", "verkaufen",
}
_TRADING_KEYWORDS_EN = {
    "portfolio", "position", "positions", "trade", "trades", "profit", "loss",
    "pnl", "balance", "equity", "cash", "btc", "eth", "price", "monitor",
    "signal", "signals", "stop", "tier", "fill", "earned", "lost", "closed",
    "open", "exposure", "risk", "buy", "sell",
}


def _word_set(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-ZÀ-ſ]+", text.lower()))


def classify_intent(message: str, language: str) -> ChatIntent:
    words = _word_set(message)
    pool = _TRADING_KEYWORDS_DE if language == "de" else _TRADING_KEYWORDS_EN
    return "trading" if words & pool else "smalltalk"


async def _respond_trading(message: str, language: str) -> ChatReply:
    try:
        snap = await build_portfolio_snapshot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kai-chat] portfolio snapshot failed: %s", exc)
        if language == "de":
            return ChatReply(
                reply="Portfolio-Snapshot gerade nicht erreichbar. Versuch es in einer Minute nochmal.",
                intent="trading", source="fallback",
            )
        return ChatReply(
            reply="Portfolio snapshot unavailable. Try again in a minute.",
            intent="trading", source="fallback",
        )

    if not snap.available or snap.position_count == 0:
        if language == "de":
            return ChatReply(
                reply=f"Kasse {snap.cash_usd:.2f} USD, keine offenen Positionen. Realized PnL {snap.realized_pnl_usd:.2f}.",
                intent="trading", source="system",
            )
        return ChatReply(
            reply=f"Cash {snap.cash_usd:.2f} USD, no open positions. Realized PnL {snap.realized_pnl_usd:.2f}.",
            intent="trading", source="system",
        )

    pos_lines: list[str] = []
    for p in snap.positions:
        mark = f"{p.market_price:.4f}" if p.market_price is not None else "?"
        upnl = p.unrealized_pnl_usd if p.unrealized_pnl_usd is not None else 0.0
        pos_lines.append(
            f"{p.symbol} qty={p.quantity:.4f} @ {p.avg_entry_price:.4f} -> {mark} (uPnL {upnl:+.2f})"
        )

    if language == "de":
        head = (
            f"Equity {snap.total_equity_usd:.2f} USD ({snap.position_count} offen, "
            f"Kasse {snap.cash_usd:.2f}, realized {snap.realized_pnl_usd:.2f})."
        )
    else:
        head = (
            f"Equity {snap.total_equity_usd:.2f} USD ({snap.position_count} open, "
            f"cash {snap.cash_usd:.2f}, realized {snap.realized_pnl_usd:.2f})."
        )
    return ChatReply(reply=head + "\n" + "\n".join(pos_lines), intent="trading", source="system")


def _build_persona_system_prompt(language: str) -> str:
    try:
        persona = load_kai_persona()
        motto = persona.motto
        archetypes = ", ".join((persona.identity.get("archetype") or [])[:3])
        primary = ", ".join((persona.personality.get("primary_traits") or [])[:6])
        forbidden = ", ".join((persona.personality.get("forbidden_traits") or [])[:5])
    except (KaiPersonaConfigError, Exception):  # noqa: BLE001
        motto = "Persona non grata"
        archetypes = "rogue_ai_media_host, cyberpunk_news_anchor"
        primary = "frech, charmant, provokant, analytisch, sarkastisch, sicherheitsbewusst"
        forbidden = "generischer_chatbot, niedliches_maskottchen, corporate_sprechpuppe"

    if language == "de":
        return (
            f"Du bist KAI. Motto: \"{motto}\". Archetyp: {archetypes}. "
            f"Charakter: {primary}. Du bist NICHT: {forbidden}. "
            "Antworte in 1-2 kurzen Saetzen, trocken, leicht patzig, on-brand. "
            "Keine Marketing-Floskeln, keine Hoeflichkeitsphrasen, kein Disclaimer-Geschwafel. "
            "Wenn du etwas nicht weisst, sag es kurz und direkt."
        )
    return (
        f"You are KAI. Motto: \"{motto}\". Archetype: {archetypes}. "
        f"Character: {primary}. You are NOT: {forbidden}. "
        "Reply in 1-2 short sentences, dry, slightly snide, on-brand. "
        "No marketing fluff, no pleasantries, no disclaimer talk. "
        "If you do not know, say so briefly and directly."
    )


async def _respond_smalltalk(message: str, language: str) -> ChatReply:
    settings = get_settings()
    api_key = settings.providers.openai_api_key
    model = settings.providers.openai_model or "gpt-4o"

    if not api_key:
        logger.warning("[kai-chat] no openai_api_key configured")
        if language == "de":
            return ChatReply(
                reply="Smalltalk-Modus offline. Frag mich was zum Trading.",
                intent="smalltalk", source="fallback",
            )
        return ChatReply(
            reply="Smalltalk mode offline. Ask me about trading.",
            intent="smalltalk", source="fallback",
        )

    from openai import AsyncOpenAI
    system_prompt = _build_persona_system_prompt(language)
    client = AsyncOpenAI(api_key=api_key, timeout=20.0)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            max_tokens=150,
            temperature=0.7,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("empty_completion")
        return ChatReply(reply=text, intent="smalltalk", source="gpt4o")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kai-chat] gpt-4o call failed: %s", exc)
        if language == "de":
            return ChatReply(
                reply="Verbindung zur KI-Schicht stockt. Versuch es nochmal.",
                intent="smalltalk", source="fallback",
            )
        return ChatReply(
            reply="LLM layer hiccuped. Try again.",
            intent="smalltalk", source="fallback",
        )


async def chat(message: str, language: str = "de") -> ChatReply:
    msg = (message or "").strip()
    if not msg:
        if language == "de":
            return ChatReply(reply="Sag was.", intent="smalltalk", source="system")
        return ChatReply(reply="Say something.", intent="smalltalk", source="system")

    lang = language if language in ("de", "en") else "de"
    intent = classify_intent(msg, lang)
    if intent == "trading":
        return await _respond_trading(msg, lang)
    return await _respond_smalltalk(msg, lang)


# ─────────────────────────────────────────────────────────────────────────────
# Voice transcription (Web frontend MediaRecorder → Whisper).
# 2026-05-09 Phase 2.3: Web Speech API fiel auf Mobile-Browsern (Brave/Firefox/
# Samsung Internet) durch [not-allowed]-Errors aus. MediaRecorder + Backend-
# Whisper ist konsistent in jedem Browser, kostet ~0.006 USD/min.
# ─────────────────────────────────────────────────────────────────────────────

# Whisper-1 wurde stark auf YouTube-Untertiteln trainiert und reproduziert deren
# Outro-Boilerplate, wenn das Audio still/leer/zu kurz ist. Frontend filtert
# bereits <8KB Blobs (KaiLiveWidget Min-Size-Schutz), aber sehr leise oder
# stille Aufnahmen oberhalb des Bytes-Thresholds kommen durch und werden hier
# als Halluzination erkannt. Liste ist kuratiert anhand realer Whisper-Outputs
# (DE + EN); Match ist Case-insensitiv mit Substring.
_WHISPER_HALLUCINATION_PHRASES: tuple[str, ...] = (
    # Amara.org-Untertitel (häufigste Halluzination im DE-Modell)
    "untertitel der amara",
    "subtitles by the amara",
    "untertitelung im auftrag",
    "untertitel im auftrag des zdf",
    "untertitelung aufgrund",
    # YouTube-Outro Standard
    "vielen dank für's zuschauen",
    "vielen dank fürs zuschauen",
    "danke für's zuschauen",
    "danke fürs zuschauen",
    "thank you for watching",
    "thanks for watching",
    # Subscribe-CTAs
    "don't forget to subscribe",
    "like and subscribe",
    "please subscribe",
    "abonniert den kanal",
    "vergesst nicht zu abonnieren",
    # Music/silence-Marker (Whisper legt Nicht-Sprache als Music-Tag aus)
    "[musik]",
    "[music]",
    "*musik*",
    "*music*",
    "♪",
    # Kurze Boilerplate-Outros
    "bis zum nächsten mal",
    "tschüss und bis bald",
    "see you next time",
)


def _is_whisper_hallucination(text: str) -> bool:
    """Return True if *text* matches a known Whisper-1 hallucination phrase.

    Substring match, case-insensitive, on stripped text. Whitelist-only —
    short legit replies ("ja", "nein", "kauf BTC") slip through and are
    handled by the frontend chat dispatcher. False-positives on user-typed
    legitimate content are unlikely because the list targets distinctive
    YouTube-outro phrases, not natural conversation.
    """
    if not text:
        return False
    norm = text.strip().lower()
    if not norm:
        return False
    return any(phrase in norm for phrase in _WHISPER_HALLUCINATION_PHRASES)


async def transcribe_audio_via_whisper(
    audio_data: bytes,
    filename: str = "voice.webm",
    language: str = "de",
) -> str | None:
    """Transcribe audio bytes via OpenAI Whisper. Returns text or None on failure.

    Reuses the same OpenAI API key as the chat engine. Determines mime-type
    from the file extension (browser-supplied). On error: silent None,
    handler logs a warning. Caller decides what to surface to the user.
    """
    import httpx
    settings = get_settings()
    api_key = settings.providers.openai_api_key
    if not api_key:
        logger.warning("[kai-voice] no openai_api_key configured")
        return None

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "webm"
    mime_map = {
        "webm": "audio/webm",
        "ogg": "audio/ogg",
        "oga": "audio/ogg",
        "mp3": "audio/mpeg",
        "mp4": "audio/mp4",
        "m4a": "audio/mp4",
        "wav": "audio/wav",
    }
    mime = mime_map.get(ext, "audio/webm")
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": (f"voice.{ext}", audio_data, mime)}
    data = {"model": "whisper-1", "language": language if language in ("de", "en") else "de"}

    head_hex = audio_data[:8].hex()
    logger.info(
        "[kai-voice] whisper request: size=%d ext=%s mime=%s lang=%s head=%s",
        len(audio_data), ext, mime, data["language"], head_hex,
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=headers,
                files=files,
                data=data,
            )
            resp.raise_for_status()
            payload = resp.json()
            text = (payload.get("text") or "").strip()
            if text and _is_whisper_hallucination(text):
                logger.warning(
                    "[kai-voice] whisper hallucination filtered: %r (size=%d)",
                    text[:120], len(audio_data),
                )
                return None
            if text:
                logger.info("[kai-voice] transcribed %d chars: %s", len(text), text[:120])
                return text
            logger.warning(
                "[kai-voice] whisper returned empty. size=%d mime=%s payload-keys=%s",
                len(audio_data), mime, list(payload.keys()),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kai-voice] whisper error: %s", exc)
    return None
