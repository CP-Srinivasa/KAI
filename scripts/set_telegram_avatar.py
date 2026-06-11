"""Set Telegram-Profilbild fuer den KAI-Telethon-User-Account.

Verwendet existierende Pi-Session (artifacts/telegram_channel.session).
Premium-Check: video bei premium, sonst PNG-Fallback.

Aufruf (auf Pi 5):
    cd /home/kai/ai_analyst_trading_bot
    .venv/bin/python scripts/set_telegram_avatar.py /tmp/kai_avatar_square.mp4 \
        /home/kai/ai_analyst_trading_bot/web/public/assets/kai/master/kai_master_v1.png
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.functions.photos import UploadProfilePhotoRequest

from app.core.settings import TelegramChannelIngestSettings


async def main(video_path: str, png_fallback: str) -> int:
    cfg = TelegramChannelIngestSettings()
    if not cfg.api_id or not cfg.api_hash:
        print("ERROR: api_id/api_hash not set (env: INGESTION_TELEGRAM_CHANNEL_API_ID/_HASH)")
        return 2

    session_path = Path(cfg.session_path)
    if not session_path.is_absolute():
        session_path = Path("/home/kai/ai_analyst_trading_bot") / session_path
    if not session_path.exists():
        print(f"ERROR: session file not found: {session_path}")
        return 3

    client = TelegramClient(str(session_path).rsplit(".session", 1)[0], cfg.api_id, cfg.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print("ERROR: telethon session not authorized")
        await client.disconnect()
        return 4

    me = await client.get_me()
    is_premium = bool(getattr(me, "premium", False))
    print(f"Account: {me.first_name!r} id={me.id} premium={is_premium}")

    if is_premium and Path(video_path).exists():
        uploaded = await client.upload_file(video_path)
        result = await client(UploadProfilePhotoRequest(video=uploaded))
        print(f"OK: animated profile photo uploaded ({video_path}) -> {type(result).__name__}")
    else:
        if not is_premium:
            print("INFO: account has no Telegram Premium - falling back to static PNG")
        if not Path(png_fallback).exists():
            print(f"ERROR: png fallback not found: {png_fallback}")
            await client.disconnect()
            return 5
        uploaded = await client.upload_file(png_fallback)
        result = await client(UploadProfilePhotoRequest(file=uploaded))
        print(f"OK: static profile photo uploaded ({png_fallback}) -> {type(result).__name__}")

    await client.disconnect()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: set_telegram_avatar.py <video.mp4> <fallback.png>")
        sys.exit(1)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))
