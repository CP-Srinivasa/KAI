"""RaspiBlitz info mirror — read-only system snapshot for the Node & Chain page.

Pulls the JSON emitted by ``kai_blitz_info.py`` ON the RaspiBlitz over a
forced-command ssh key (the key can execute exactly that one read-only script —
no shell, no pty; see the authorized_keys ``command=`` line on the node). The
endpoint is default-off (``APP_LN_BLITZ_INFO_ENABLED``), fail-soft (``available:
false`` + reason instead of 5xx) and cached in-process so the node is asked at
most once per minute regardless of dashboard polling.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.settings import get_settings

router = APIRouter()

_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, Any] = {"ts": 0.0, "payload": None}


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason, "data": None, "age_seconds": None}


async def _fetch_blitz_info() -> dict[str, Any]:
    ln = get_settings().lightning
    if not ln.blitz_info_enabled:
        return _unavailable("disabled")
    if not ln.blitz_info_ssh_key_path or not ln.blitz_info_ssh_target:
        return _unavailable("ssh target/key not configured")
    cmd = [
        "ssh",
        "-i",
        ln.blitz_info_ssh_key_path,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "StrictHostKeyChecking=accept-new",
        ln.blitz_info_ssh_target,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=ln.blitz_info_timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            return _unavailable(f"ssh timeout after {ln.blitz_info_timeout_seconds}s")
        if proc.returncode != 0:
            detail = (stderr or b"").decode(errors="replace").strip()[:200]
            return _unavailable(f"ssh exit {proc.returncode}: {detail}")
        data = json.loads(stdout.decode(errors="replace"))
        if not isinstance(data, dict):
            return _unavailable("non-object JSON from node")
        return {"available": True, "reason": "", "data": data, "age_seconds": 0}
    except FileNotFoundError:
        return _unavailable("ssh binary not found")
    except json.JSONDecodeError as exc:
        return _unavailable(f"bad JSON from node: {exc}")
    except Exception as exc:  # noqa: BLE001 — mirror is display-only, never 5xx
        return _unavailable(f"unexpected: {exc}")


async def _annotate_reconciled(payload: dict[str, Any]) -> None:
    """Belegte Force-Close-Altfaelle (D-287) mitzaehlen, damit sie keine Warnung ausloesen.

    Die Rohzahl ``pending_channels`` vom Node bleibt unveraendert. Scheitert die
    Abfrage, bleibt die Zahl 0 und die Warnung sichtbar (fail-safe).
    """
    from app.lightning.treasury import get_pending_channels_snapshot

    lnd = (payload.get("data") or {}).get("lnd")
    if not isinstance(lnd, dict):
        return
    snap = await get_pending_channels_snapshot()
    lnd["pending_channels_reconciled"] = (
        len(snap.reconciled_force_closes) if snap.state == "ok" else 0
    )


@router.get("/dashboard/api/node/blitz", tags=["dashboard"])
async def dashboard_node_blitz_api() -> JSONResponse:
    """Read-only RaspiBlitz mirror (default-off, fail-soft, 60s in-process cache)."""
    now = time.monotonic()
    cached = _cache["payload"]
    if cached is not None and (now - _cache["ts"]) < _CACHE_TTL_SECONDS:
        payload = dict(cached)
        payload["age_seconds"] = round(now - _cache["ts"], 1)
        return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})

    payload = await _fetch_blitz_info()
    # Only successful snapshots are cached: an outage should retry next poll,
    # not pin "unavailable" for a minute after the node comes back.
    if payload["available"]:
        await _annotate_reconciled(payload)
        _cache["ts"] = now
        _cache["payload"] = payload
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})
