"""``/help`` — der Befehlskatalog des Operator-Bots, aus dem God-File extrahiert.

Reiner Text, kein Verhalten: ``telegram_bot.py`` sendet ihn unveraendert. Wer
einen Befehl in ``_dispatch`` ergaenzt, traegt ihn hier ein — der Test in
``tests/unit/test_telegram_bot.py`` liest den gesendeten Text.
"""

from __future__ import annotations

HELP_TEXT = (
    "*KAI Help & Support*\n"
    "\n"
    "*Read-only views*\n"
    "/status — system status\n"
    "/positions — paper positions\n"
    "/exposure — paper exposure and risk\n"
    "/signals — active signals\n"
    "/signalstatus — signal pipeline\n"
    "/tagesbericht — daily report\n"
    "/alertstatus — alert delivery status\n"
    "/quality — quality-bar metrics\n"
    "/annotate — annotate alerts\n"
    "\n"
    "*Actions*\n"
    "/signal BUY BTC 65000 — submit a trading signal\n"
    "/approve dec\\_xxx — approve a decision\n"
    "/reject dec\\_xxx — reject a decision\n"
    "/pause — pause the system\n"
    "/resume — resume the system\n"
    "/kill — emergency stop\n"
    "\n"
    "*Lightning (D-277, HOTP ab Policy-Schwelle)*\n"
    "/pay <bolt11> — Rechnung pruefen: Betrag, Gebuehr, Policy\n"
    "/pay ok \\[<hotp>\\] — freigeben und senden · /pay cancel · /pay status\n"
    "\n"
    "*Live-Mode (HOTP-gated, Phase 0)*\n"
    "/live status — current state + caps\n"
    "/live unlock <hotp> — unlock for live trades\n"
    "/live lock — relock immediately\n"
    "/trade SYM SIDE QTY ENTRY SL HOTP \\[EX\\] — place live limit order\n"
    "\n"
    "*Message types*\n"
    "[NEWS] — information only, never triggers execution\n"
    "[SIGNAL] — structured trade instruction, schema-validated\n"
    "[EXCHANGE_RESPONSE] — execution status update\n"
    "\n"
    "Telegram is view-only; the JSON envelope is the source of truth. "
    "SIGNAL entries without required fields fail closed.\n"
    "\n"
    "*Navigation*\n"
    "/menu — open the main menu\n"
    "/menu\\_reload — reload menu config\n"
    "/menu\\_validate — validate menu config\n"
    "/hilfe — show this help"
)

__all__ = ["HELP_TEXT"]
