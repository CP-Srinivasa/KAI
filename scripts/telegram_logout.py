"""Telegram MTProto Session-Logout — Pi-Cutover Runbook Phase 2.3 (Variante A).

Loest das Telegram-Server-Side AuthKey der aktiven Session bevor ein zweiter
Client (neuer Pi) mit derselben Session-Datei connectet. Ohne diesen Schritt
produziert der zweite Client einen `AuthKeyDuplicatedError`-Storm
(Lehre Cutover 2026-05-02 02:55 UTC, dokumentiert in
artifacts/runbooks/pi5_cutover.md Phase 2.3).

Liest API-ID/HASH und Session-Pfad aus den Settings — kein
Heredoc-/Env-Quoting durch zwei SSH-Layer (vgl. memory
feedback_pi_remote_edits.md).

Usage (aus Repo-Root, .venv aktiv):
    python scripts/telegram_logout.py

Exit-Codes:
    0 — logged out successfully
    1 — Settings-Fehler (api_id/hash leer)
    2 — telethon-Import-Fehler
    3 — Runtime-Fehler beim Logout
"""

from __future__ import annotations

import asyncio
import sys

from app.core.settings import get_settings


async def main() -> int:
    cfg = get_settings().telegram_channel_ingest
    if not cfg.api_id or not cfg.api_hash:
        print(
            "ERROR: INGESTION_TELEGRAM_CHANNEL_API_ID und _API_HASH "
            "muessen in .env gesetzt sein.",
            file=sys.stderr,
        )
        return 1

    try:
        from telethon import TelegramClient
    except ImportError as exc:
        print(f"ERROR: telethon nicht installiert: {exc}", file=sys.stderr)
        return 2

    client = TelegramClient(
        cfg.session_path,
        cfg.api_id,
        cfg.api_hash,
        flood_sleep_threshold=300,
        connection_retries=10,
        retry_delay=2,
    )
    await client.start()
    try:
        result = await client.log_out()
    finally:
        await client.disconnect()

    if result:
        print("logged out")
        return 0

    print(
        "ERROR: log_out() returned False — Session ggf. schon invalid.",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
