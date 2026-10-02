"""Timer der KI-Kontrollstation (alle 10 min, ``kai-ai-control.timer``).

Je Lauf: Guthaben (hoechstens alle ``accounts_every_minutes``), Protokoll der KI-Schalter,
Telegram-Hinweise. Gegenueber KAI nur lesend -- kein Modellaufruf, keine Konfiguration.

    python -m app.observability.ai_control.tick            # wie die Unit
    python -m app.observability.ai_control.tick --dry-run  # druckt die Nachricht, sendet nichts,
                                                            # speichert keinen Hinweis-Zustand
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.observability.ai_control import accounts, alerts, protocol
from app.observability.ai_control.config import (
    AccountKeys,
    ControlPaths,
    ControlThresholds,
    LiteLLMModels,
    providers_configured,
)
from app.observability.ai_control.snapshot import build_snapshot, current_transport

#: Das aktive Release (Symlink), damit Release-Wechsel im Protokoll erscheinen.
RELEASE_LINK = Path("/home/kai/current")


def _release() -> dict[str, str] | None:
    try:
        if RELEASE_LINK.exists():
            return {"RELEASE": RELEASE_LINK.resolve().name[:8]}
    except OSError:
        return None
    return None


async def run_tick(
    *,
    now: datetime,
    paths: ControlPaths,
    send: Callable[[str], Awaitable[bool]],
    fetch_client: httpx.Client | None = None,
    transport: dict[str, Any] | None = None,
    persist_alerts: bool = True,
) -> dict[str, Any]:
    from app.ai.runtime import inference_settings

    th = ControlThresholds()
    bericht: dict[str, Any] = {"accounts_fetched": False, "protocol_changes": 0, "sent": False}
    vorher, stand = accounts.read_accounts(paths.accounts)
    if stand is None or now - stand >= timedelta(minutes=th.accounts_every_minutes):
        client = fetch_client or httpx.Client()
        try:
            neu = accounts.fetch_accounts(AccountKeys(), client=client, now=now)
        finally:
            if fetch_client is None:
                client.close()
        accounts.write_accounts(paths.accounts, accounts.merge_with_previous(neu, vorher), now)
        bericht["accounts_fetched"] = True
    bericht["protocol_changes"] = len(protocol.record(paths, now=now, extra=_release()))
    snap = build_snapshot(
        now=now,
        paths=paths,
        inference=inference_settings(None),
        transport=transport,
        thresholds=th,
        models=LiteLLMModels(),
        providers_configured=providers_configured(),
    )
    text, zustand = alerts.plan(
        snap["attention"], alerts.load_state(paths.alert_state), now=now, thresholds=th
    )
    if text:
        try:
            bericht["sent"] = bool(await send(text))
        except Exception:  # noqa: BLE001 -- der naechste Lauf versucht es erneut
            bericht["sent"] = False
        if not bericht["sent"]:
            # Zustand NICHT speichern: sonst gaelte die Meldung als zugestellt.
            return bericht
    if persist_alerts:
        # Ein Probelauf speichert nichts: sonst gaelte seine Meldung als zugestellt.
        alerts.save_state(paths.alert_state, zustand)
    return bericht


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.observability.ai_control.tick")
    ap.add_argument("--dry-run", action="store_true", help="Nachricht drucken statt senden")
    args = ap.parse_args(argv)

    async def senden(text: str) -> bool:
        if args.dry_run:
            print(text)
            return True
        from app.alerts.notify import send_operator_notification

        return await send_operator_notification(text)

    async def ablauf() -> dict[str, Any]:
        return await run_tick(
            now=datetime.now(UTC),
            paths=ControlPaths(),
            send=senden,
            transport=await current_transport(),
            persist_alerts=not args.dry_run,
        )

    print(asyncio.run(ablauf()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
