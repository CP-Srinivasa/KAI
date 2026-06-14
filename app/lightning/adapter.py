"""KAI-facing Lightning adapter (fail-closed, default-off).

The adapter is the only surface the rest of KAI imports. It wraps the read-only
:class:`LndRestClient` and guarantees two invariants:

  * **default-off** — when ``settings.lightning.enabled`` is False it returns a
    ``disabled`` status without any network call.
  * **fail-closed** — any node/transport/auth error is caught and surfaced as an
    ``unavailable`` status. It NEVER raises into the trading loop.

Phase 1 is observation only. Invoice/pay surfaces are intentionally absent here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.settings import LightningSettings, get_settings
from app.lightning.client import LightningUnavailableError, LndRestClient


@dataclass(frozen=True)
class LightningNodeStatus:
    """Snapshot of the Lightning node from KAI's perspective.

    ``state`` is one of: ``disabled`` (feature off), ``unavailable`` (enabled but
    the node could not be reached), ``ok`` (reachable). ``reason`` carries the
    error detail for the unavailable/degraded case.

    Liveness is established via the cheap ``/v1/state`` probe; ``getinfo`` is
    best-effort enrichment. If ``getinfo`` fails (it can be slow on a Tor node
    right after a restart) while the node is reachable, ``state`` stays ``ok``,
    ``reachable`` is True, and ``info_available`` is False with the detail in
    ``reason`` — the node is up, we just lack the detail fields.
    """

    state: str
    reachable: bool
    server_state: str = ""
    info_available: bool = False
    synced_to_chain: bool = False
    block_height: int = 0
    num_peers: int = 0
    num_active_channels: int = 0
    identity_pubkey: str = ""
    alias: str = ""
    version: str = ""
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def disabled(cls) -> LightningNodeStatus:
        return cls(state="disabled", reachable=False, reason="lightning disabled")

    @classmethod
    def unavailable(cls, reason: str) -> LightningNodeStatus:
        return cls(state="unavailable", reachable=False, reason=reason)


def _build_client(cfg: LightningSettings) -> LndRestClient:
    return LndRestClient(
        base_url=cfg.base_url,
        macaroon_hex=cfg.macaroon_hex,
        macaroon_path=cfg.macaroon_path,
        tls_cert_path=cfg.tls_cert_path,
        timeout=cfg.timeout_seconds,
    )


async def get_node_status(cfg: LightningSettings | None = None) -> LightningNodeStatus:
    """Return the current node status, never raising.

    Args:
        cfg: optional settings override (tests). Defaults to the cached app
             settings' ``lightning`` section.
    """
    cfg = cfg or get_settings().lightning
    if not cfg.enabled:
        return LightningNodeStatus.disabled()
    try:
        client = _build_client(cfg)
        server_state = await client.get_state()
    except LightningUnavailableError as exc:
        return LightningNodeStatus.unavailable(str(exc))
    except Exception as exc:  # noqa: BLE001 — adapter must never leak into the loop
        return LightningNodeStatus.unavailable(f"unexpected: {exc}")

    # Node is reachable. getinfo is best-effort enrichment — never downgrade a
    # reachable node to "unavailable" just because the (sometimes slow) getinfo
    # call failed.
    try:
        info = await client.get_info()
    except Exception as exc:  # noqa: BLE001 — getinfo failure must not flip liveness
        return LightningNodeStatus(
            state="ok",
            reachable=True,
            server_state=server_state,
            info_available=False,
            reason=f"getinfo unavailable: {exc}",
        )
    return LightningNodeStatus(
        state="ok",
        reachable=True,
        server_state=server_state,
        info_available=True,
        synced_to_chain=info.synced_to_chain,
        block_height=info.block_height,
        num_peers=info.num_peers,
        num_active_channels=info.num_active_channels,
        identity_pubkey=info.identity_pubkey,
        alias=info.alias,
        version=info.version,
    )
