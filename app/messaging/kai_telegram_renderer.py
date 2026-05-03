"""KAI Telegram Renderer — compact card layouts.

Spec: docs/kai_persona/technical_ui_pack_v3_2.md §10
       docs/kai_persona/final_execution_prompt_v3_4.md §11

DALI-Audit (2026-05-03): 2-tier format adopted — Headline (asset · direction · confidence · risk),
then comment, then footer with entry/SL/data-basis. Less wall-of-text on mobile.

Markdown is escaped via _escape_markdown_v1 to keep umlauts intact and avoid
breakage from underscores or asterisks in symbol names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.messaging.kai_phrase_engine import KaiLanguage, assert_phrase_safe

# Telegram MarkdownV1 reserved characters that must be escaped in user text.
# We use V1 (not V2) for backwards compatibility with the existing operator bot.
_MARKDOWN_V1_SPECIAL = re.compile(r"([_*`\[])")


def _escape_markdown(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    return _MARKDOWN_V1_SPECIAL.sub(r"\\\1", text)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Signal Card
# ---------------------------------------------------------------------------

KaiTradingMode = Literal["WATCHLIST", "PAPERTRADE", "LIVETRADE", "SIMULATION"]
KaiDirection = Literal["LONG", "SHORT", "NEUTRAL", "NO_TRADE"]
KaiRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
KaiDataQuality = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]


@dataclass(frozen=True)
class KaiSignalCard:
    asset: str
    mode: KaiTradingMode
    direction: KaiDirection
    confidence: int
    risk: KaiRiskLevel
    entry: str
    stop_loss: str
    data_basis: tuple[str, ...]
    data_quality: KaiDataQuality
    timestamp: str
    comment: str


@dataclass(frozen=True)
class KaiWarningCard:
    target: str
    problem: str
    risk: KaiRiskLevel
    action: str
    timestamp: str
    comment: str


@dataclass(frozen=True)
class KaiSecurityCard:
    area: str
    status: str
    priority: KaiRiskLevel
    last_check: str
    result: str
    next_step: str
    comment: str


def render_kai_signal_card(signal: KaiSignalCard, language: KaiLanguage = "de") -> str:
    assert_phrase_safe(signal.comment, language)

    title = f"KAI SIGNAL // {signal.mode}"
    asset = _escape_markdown(signal.asset)
    direction = signal.direction
    risk = signal.risk
    quality = signal.data_quality
    entry = _escape_markdown(signal.entry or "—")
    stop_loss = _escape_markdown(signal.stop_loss or "—")
    data_basis = _escape_markdown(", ".join(signal.data_basis) or "—")
    timestamp = _escape_markdown(signal.timestamp)
    comment = _escape_markdown(signal.comment)

    if language == "en":
        return (
            f"*{title}*\n\n"
            f"{asset} · {direction} · {signal.confidence}% · Risk {risk}\n\n"
            f"„{comment}“\n\n"
            f"— Entry: {entry}\n"
            f"— Stop: {stop_loss}\n"
            f"— Basis: {data_basis} (Quality {quality})\n"
            f"— {timestamp}"
        )

    return (
        f"*{title}*\n\n"
        f"{asset} · {direction} · {signal.confidence}% · Risiko {risk}\n\n"
        f"„{comment}“\n\n"
        f"— Entry: {entry}\n"
        f"— Stop: {stop_loss}\n"
        f"— Basis: {data_basis} (Qualitaet {quality})\n"
        f"— {timestamp}"
    )


def render_kai_warning_card(warning: KaiWarningCard, language: KaiLanguage = "de") -> str:
    assert_phrase_safe(warning.comment, language)
    target = _escape_markdown(warning.target)
    problem = _escape_markdown(warning.problem)
    action = _escape_markdown(warning.action)
    timestamp = _escape_markdown(warning.timestamp)
    comment = _escape_markdown(warning.comment)

    if language == "en":
        return (
            "*KAI WARNING // RISK*\n\n"
            f"Target: {target}\n"
            f"Problem: {problem}\n"
            f"Risk: {warning.risk}\n"
            f"Action: {action}\n"
            f"Time: {timestamp}\n\n"
            f"„{comment}“"
        )

    return (
        "*KAI WARNING // RISK*\n\n"
        f"Asset/System: {target}\n"
        f"Problem: {problem}\n"
        f"Risiko: {warning.risk}\n"
        f"Aktion: {action}\n"
        f"Zeit: {timestamp}\n\n"
        f"„{comment}“"
    )


def render_kai_security_card(card: KaiSecurityCard, language: KaiLanguage = "de") -> str:
    assert_phrase_safe(card.comment, language)
    area = _escape_markdown(card.area)
    status = _escape_markdown(card.status)
    last = _escape_markdown(card.last_check)
    result = _escape_markdown(card.result)
    nxt = _escape_markdown(card.next_step)
    comment = _escape_markdown(card.comment)

    if language == "en":
        return (
            "*KAI SYSTEM // SECURITY*\n\n"
            f"Area: {area}\n"
            f"Status: {status}\n"
            f"Priority: {card.priority}\n"
            f"Last check: {last}\n"
            f"Result: {result}\n"
            f"Next: {nxt}\n\n"
            f"„{comment}“"
        )

    return (
        "*KAI SYSTEM // SECURITY*\n\n"
        f"Bereich: {area}\n"
        f"Status: {status}\n"
        f"Prioritaet: {card.priority}\n"
        f"Letzte Pruefung: {last}\n"
        f"Ergebnis: {result}\n"
        f"Naechster Schritt: {nxt}\n\n"
        f"„{comment}“"
    )


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------


def render_kai_main_menu(language: KaiLanguage = "de") -> dict[str, object]:
    """Returns Telegram-friendly main menu structure (caller wraps as InlineKeyboard)."""
    if language == "en":
        return {
            "title": "KAI // CONTROL PANEL",
            "rows": [
                ["Scan Market", "Show Signals"],
                ["Check Risk", "Check Portfolio"],
                ["Paper Trading", "Live Trading"],
                ["Simulation", "News Radar"],
                ["Social Buzz", "Watchdog Report"],
                ["SENTR Security", "Settings"],
            ],
        }
    return {
        "title": "KAI // CONTROL PANEL",
        "rows": [
            ["Markt scannen", "Signale anzeigen"],
            ["Risiko pruefen", "Portfolio pruefen"],
            ["Papertrading", "Livetrading"],
            ["Simulation", "News Radar"],
            ["Social Buzz", "Watchdog Report"],
            ["SENTR Security", "Einstellungen"],
        ],
    }
