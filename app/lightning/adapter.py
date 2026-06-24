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
    synced_to_graph: bool = False  # lnd gossip-graph sync (routing readiness)
    block_height: int = 0
    num_peers: int = 0
    num_active_channels: int = 0
    num_pending_channels: int = 0
    identity_pubkey: str = ""
    alias: str = ""
    version: str = ""
    # Balances (Phase-1.5 observation, read-only). Fetched independent of the
    # Tor-slow getinfo, so liquidity/wallet numbers show even when info is stale.
    # ``balances_available`` is False if the (cheap) balance calls failed.
    balances_available: bool = False
    channel_local_sat: int = 0  # off-chain outbound liquidity
    channel_remote_sat: int = 0  # off-chain inbound liquidity
    wallet_confirmed_sat: int = 0  # on-chain confirmed
    wallet_total_sat: int = 0  # on-chain confirmed + unconfirmed
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def disabled(cls) -> LightningNodeStatus:
        return cls(state="disabled", reachable=False, reason="lightning disabled")

    @classmethod
    def unavailable(cls, reason: str) -> LightningNodeStatus:
        return cls(state="unavailable", reachable=False, reason=reason)


@dataclass(frozen=True)
class LightningChannel:
    """One open channel from KAI's read-only perspective (lnd ``listchannels``).

    ``local_sat`` is outbound liquidity (what KAI can SEND), ``remote_sat`` is
    inbound (what KAI can RECEIVE). ``channel_id`` prefers the numeric ``chan_id``
    and falls back to the ``channel_point`` (funding txid:index).
    """

    channel_id: str
    remote_pubkey: str
    capacity_sat: int
    local_sat: int  # outbound liquidity
    remote_sat: int  # inbound liquidity
    active: bool


@dataclass(frozen=True)
class LightningChannels:
    """Per-channel breakdown snapshot. ``state`` mirrors the node adapter:
    ``disabled`` (feature off), ``unavailable`` (enabled but unreachable), ``ok``."""

    state: str
    reachable: bool
    channels: list[LightningChannel] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def disabled(cls) -> LightningChannels:
        return cls(state="disabled", reachable=False, reason="lightning disabled")

    @classmethod
    def unavailable(cls, reason: str) -> LightningChannels:
        return cls(state="unavailable", reachable=False, reason=reason)


@dataclass(frozen=True)
class LightningFeeReport:
    """Routing-fee income summary (lnd ``feereport``), read-only.

    ``available`` is False when the feature is off or the node was unreachable —
    callers must not read the sums as "zero income" in that case. With no channels
    the sums are legitimately 0. Amounts are routing fees EARNED, in sats.
    """

    available: bool
    day_fee_sat: int = 0
    week_fee_sat: int = 0
    month_fee_sat: int = 0

    @classmethod
    def unavailable(cls) -> LightningFeeReport:
        return cls(available=False)


def _amt_sat(value: Any) -> int:
    """Parse an lnd sat amount: a plain int/str, or an Amount object
    ``{"sat": "123", "msat": "..."}``. Returns 0 on anything unparseable."""
    if isinstance(value, dict):
        value = value.get("sat")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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

    # Node reachable. Balances are cheap (no Tor/graph dependency) → fetch them
    # best-effort and INDEPENDENT of the (sometimes Tor-slow) getinfo call, so
    # liquidity/wallet numbers stay truthful even when getinfo is unavailable.
    bal: dict[str, Any] = {}
    try:
        cb = await client.channel_balance()
        wb = await client.wallet_balance()
        bal = {
            "balances_available": True,
            "channel_local_sat": _amt_sat(cb.get("local_balance")),
            "channel_remote_sat": _amt_sat(cb.get("remote_balance")),
            "wallet_confirmed_sat": _amt_sat(wb.get("confirmed_balance")),
            "wallet_total_sat": _amt_sat(wb.get("total_balance")),
        }
    except LightningUnavailableError:
        bal = {}  # balances best-effort — never flip liveness

    # getinfo is best-effort enrichment — never downgrade a reachable node to
    # "unavailable" just because the (sometimes slow) getinfo call failed.
    try:
        info = await client.get_info()
    except Exception as exc:  # noqa: BLE001 — getinfo failure must not flip liveness
        return LightningNodeStatus(
            state="ok",
            reachable=True,
            server_state=server_state,
            info_available=False,
            reason=f"getinfo unavailable: {exc}",
            **bal,
        )
    return LightningNodeStatus(
        state="ok",
        reachable=True,
        server_state=server_state,
        info_available=True,
        synced_to_chain=info.synced_to_chain,
        synced_to_graph=info.synced_to_graph,
        block_height=info.block_height,
        num_peers=info.num_peers,
        num_active_channels=info.num_active_channels,
        num_pending_channels=info.num_pending_channels,
        identity_pubkey=info.identity_pubkey,
        alias=info.alias,
        version=info.version,
        **bal,
    )


async def get_channels(cfg: LightningSettings | None = None) -> LightningChannels:
    """Return the per-channel breakdown, never raising (default-off / fail-closed).

    Read-only (``listchannels``); no write/send surface. ``disabled`` short-circuits
    without a network call. Channels are sorted active-first, then by capacity desc,
    for a stable display order.
    """
    cfg = cfg or get_settings().lightning
    if not cfg.enabled:
        return LightningChannels.disabled()
    try:
        client = _build_client(cfg)
        raw = await client.list_channels()
    except LightningUnavailableError as exc:
        return LightningChannels.unavailable(str(exc))
    except Exception as exc:  # noqa: BLE001 — adapter must never leak into the loop
        return LightningChannels.unavailable(f"unexpected: {exc}")

    items: list[LightningChannel] = []
    for ch in raw.get("channels", []) or []:
        if not isinstance(ch, dict):
            continue
        items.append(
            LightningChannel(
                channel_id=str(ch.get("chan_id") or ch.get("channel_point") or ""),
                remote_pubkey=str(ch.get("remote_pubkey", "")),
                capacity_sat=_amt_sat(ch.get("capacity")),
                local_sat=_amt_sat(ch.get("local_balance")),
                remote_sat=_amt_sat(ch.get("remote_balance")),
                active=bool(ch.get("active", False)),
            )
        )
    items.sort(key=lambda c: (not c.active, -c.capacity_sat))
    return LightningChannels(state="ok", reachable=True, channels=items)


async def get_fee_report(cfg: LightningSettings | None = None) -> LightningFeeReport:
    """Return the routing-fee income summary, never raising (default-off / fail-closed).

    Read-only (lnd ``feereport``); no write/send surface. ``disabled`` short-circuits
    without a network call. Any node/transport error → ``available=False`` so a
    failure is never misread as "zero routing income".
    """
    cfg = cfg or get_settings().lightning
    if not cfg.enabled:
        return LightningFeeReport.unavailable()
    try:
        client = _build_client(cfg)
        raw = await client.fee_report()
    except LightningUnavailableError:
        return LightningFeeReport.unavailable()
    except Exception:  # noqa: BLE001 — adapter must never leak into the loop
        return LightningFeeReport.unavailable()
    return LightningFeeReport(
        available=True,
        day_fee_sat=_amt_sat(raw.get("day_fee_sum")),
        week_fee_sat=_amt_sat(raw.get("week_fee_sum")),
        month_fee_sat=_amt_sat(raw.get("month_fee_sum")),
    )
