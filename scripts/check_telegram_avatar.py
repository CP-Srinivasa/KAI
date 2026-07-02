"""Read-only: Telegram-Profilbild-Historie pruefen.

Listet alle Profilbilder des authenticated User-Accounts.
Aendert NICHTS.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from telethon import TelegramClient

from app.core.settings import TelegramChannelIngestSettings


async def main() -> int:
    cfg = TelegramChannelIngestSettings()
    if not cfg.api_id or not cfg.api_hash:
        print("ERROR: api_id/api_hash not set")
        return 2

    session_path = Path(cfg.session_path)
    if not session_path.is_absolute():
        session_path = Path("/home/kai/ai_analyst_trading_bot") / session_path

    client = TelegramClient(str(session_path).rsplit(".session", 1)[0], cfg.api_id, cfg.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print("ERROR: not authorized")
        await client.disconnect()
        return 4

    me = await client.get_me()
    print(
        f"Account: first_name={me.first_name!r} last_name={me.last_name!r} username={me.username!r}"
    )
    print(f"  id={me.id} premium={getattr(me, 'premium', False)}")
    print(f"  photo_attr={me.photo}")

    print("\nProfile-Photo-History (read-only):")
    count = 0
    async for photo in client.iter_profile_photos(me, limit=20):
        count += 1
        date = photo.date.isoformat() if photo.date else "?"
        video = bool(getattr(photo, "video_sizes", None))
        print(f"  [{count}] photo_id={photo.id} date={date} has_video={video}")
    if count == 0:
        print("  (no profile photos in history)")
    print(f"\nTotal photos found: {count}")

    await client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
