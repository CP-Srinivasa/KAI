"""KAI Phrase Engine — Python pendant for web/src/kai/phraseEngine.ts.

Reads the same phrase pool from the YAML config so frontend and backend stay
in lock-step. Adds a forbidden-phrase safety check so trading-related output
can never claim guaranteed profit.

Spec: docs/kai_persona/technical_ui_pack_v3_2.md §6 + §17.3
"""

from __future__ import annotations

import logging
import random
from typing import Literal

from app.messaging.kai_persona import KaiPersona, load_kai_persona

logger = logging.getLogger(__name__)

KaiLanguage = Literal["de", "en"]
KaiPhraseMode = Literal["hype", "mockery", "bad_data"]

# Same forbidden lists as web/src/kai/constants.ts
KAI_FORBIDDEN_PHRASES_DE: tuple[str, ...] = (
    "sicherer Gewinn",
    "garantierter Gewinn",
    "kann nicht verlieren",
    "100% sicher",
    "100 Prozent sicher",
)

KAI_FORBIDDEN_PHRASES_EN: tuple[str, ...] = (
    "guaranteed profit",
    "risk-free profit",
    "cannot lose",
    "100% safe",
)

# Extra-mode phrases (Prompt-Bibel V1 §10) — not state-keyed in YAML, kept here.
_EXTRA_MODE_PHRASES: dict[KaiPhraseMode, dict[KaiLanguage, tuple[str, ...]]] = {
    "hype": {
        "de": (
            "Social Buzz explodiert. Fundament noch duenn.",
            "Viel Laerm. Wenig Knochen.",
            "FOMO erkannt. Ich vertraue dem Ding noch nicht.",
        ),
        "en": (
            "Social buzz exploding. Foundation still thin.",
            "Lots of noise. Not much bone.",
            "FOMO detected. I do not trust this yet.",
        ),
    },
    "mockery": {
        "de": (
            "Mutig. Nicht klug. Aber mutig.",
            "Das ist kein Signal. Das ist Laerm mit Make-up.",
        ),
        "en": (
            "Bold. Not smart. But bold.",
            "That is not a signal. That is noise with makeup.",
        ),
    },
    "bad_data": {
        "de": (
            "Die Daten sind matschig. Ich traue dem Signal noch nicht.",
            "Input unsauber. Output mit Vorsicht geniessen.",
            "Garbage in, Glitch out.",
        ),
        "en": (
            "The data is mushy. I do not trust the signal yet.",
            "Input dirty. Handle output with care.",
            "Garbage in, glitch out.",
        ),
    },
}


def _phrases_for(persona: KaiPersona, state: str, language: KaiLanguage) -> tuple[str, ...]:
    cfg = persona.states.get(state)
    if cfg is None:
        return ()
    return cfg.phrases_de if language == "de" else cfg.phrases_en


def get_kai_phrase(
    state: str,
    language: KaiLanguage = "de",
    seed: int | None = None,
    persona: KaiPersona | None = None,
) -> str:
    """Return a phrase for a state from YAML, deterministic if seed given."""
    p = persona or load_kai_persona()
    phrases = _phrases_for(p, state, language)
    if not phrases:
        # Fall back to ERROR state phrases — never return empty.
        phrases = _phrases_for(p, "ERROR", language)
    if not phrases:
        return "Kein Kommentar verfuegbar." if language == "de" else "No comment available."

    if seed is not None:
        return phrases[abs(seed) % len(phrases)]
    return random.choice(phrases)


def get_kai_extra_mode_phrase(
    mode: KaiPhraseMode,
    language: KaiLanguage = "de",
    seed: int | None = None,
) -> str:
    pool = _EXTRA_MODE_PHRASES.get(mode, {}).get(language, ())
    if not pool:
        return get_kai_phrase("ANALYSIS", language, seed)
    if seed is not None:
        return pool[abs(seed) % len(pool)]
    return random.choice(pool)


def is_phrase_safe(text: str, language: KaiLanguage) -> bool:
    """True iff the text contains none of the forbidden financial-claim phrases."""
    needles = KAI_FORBIDDEN_PHRASES_DE if language == "de" else KAI_FORBIDDEN_PHRASES_EN
    lower = text.lower()
    return not any(n.lower() in lower for n in needles)


def assert_phrase_safe(text: str, language: KaiLanguage) -> None:
    """Raise if `text` violates the forbidden-phrase guard."""
    if not is_phrase_safe(text, language):
        raise ValueError(f"phrase contains forbidden financial-claim language: {text!r}")
