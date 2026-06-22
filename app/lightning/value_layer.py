"""Lightning value layer (L4) — invoice creation + channel opening, HARD-GATED.

This is the only write path to KAI's funded lnd node. Every entry is gated, and
nothing moves real value unless the operator deliberately flips MULTIPLE gates:

  * ``pay_enabled`` (``APP_LN_PAY_ENABLED``, default False) — the master
    kill-switch. While False, NOTHING here touches the node.
  * ``dry_run`` (default True) — even with pay_enabled, the default is to return
    the PLAN only (no lnd write). The caller must pass ``dry_run=False``.
  * ``confirm`` (channel open only) — opening a channel SPENDS on-chain and is
    IRREVERSIBLE, so it additionally requires an explicit ``confirm=True``.

Invoice creation is receive-side (no spend) but still gated as L4. Enabling the
write surface also requires a SCOPE-MINIMAL macaroon on the node (invoices /
channel-open) — NEVER the readonly macaroon, NEVER admin. Default state is fully
inert: read-only Phase-1 behaviour is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.lightning_settings import LightningSettings
from app.lightning.adapter import _build_client
from app.lightning.client import LightningUnavailableError


@dataclass(frozen=True)
class ValueLayerResult:
    """Outcome of a gated value-layer action. ``state`` is the honest disposition."""

    action: str  # "create_invoice" | "open_channel"
    state: str  # "disabled" | "planned" | "executed" | "error"
    detail: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "state": self.state,
            "detail": self.detail,
            "plan": self.plan,
            "response": self.response,
        }


def _settings(cfg: LightningSettings | None) -> LightningSettings:
    if cfg is not None:
        return cfg
    from app.core.settings import get_settings

    return get_settings().lightning


async def create_invoice(
    *,
    value_sat: int,
    memo: str = "",
    dry_run: bool = True,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """Create a BOLT11 invoice (receive-side, no spend) — gated + dry-run-default."""
    cfg = _settings(cfg)
    plan = {"value_sat": int(value_sat), "memo": memo}
    if not cfg.pay_enabled:
        return ValueLayerResult("create_invoice", "disabled", "pay_enabled is False", plan)
    if value_sat <= 0:
        return ValueLayerResult("create_invoice", "error", "value_sat must be > 0", plan)
    if dry_run:
        return ValueLayerResult("create_invoice", "planned", "dry_run", plan)
    try:
        resp = await _build_client(cfg).add_invoice(value_sat=value_sat, memo=memo)
    except LightningUnavailableError as exc:
        return ValueLayerResult("create_invoice", "error", str(exc), plan)
    return ValueLayerResult("create_invoice", "executed", "", plan, resp)


async def open_channel(
    *,
    node_pubkey_hex: str,
    local_funding_sat: int,
    sat_per_vbyte: int = 0,
    confirm: bool = False,
    dry_run: bool = True,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """Open a channel — SPENDS on-chain, irreversible. Maximally gated.

    Requires ALL of: ``pay_enabled`` True, ``dry_run`` False, and explicit
    ``confirm`` True. Any missing gate returns ``planned``/``disabled`` WITHOUT
    touching the node.
    """
    cfg = _settings(cfg)
    plan = {
        "node_pubkey_hex": node_pubkey_hex,
        "local_funding_sat": int(local_funding_sat),
        "sat_per_vbyte": int(sat_per_vbyte),
    }
    if not cfg.pay_enabled:
        return ValueLayerResult("open_channel", "disabled", "pay_enabled is False", plan)
    if not node_pubkey_hex or local_funding_sat <= 0:
        return ValueLayerResult(
            "open_channel", "error", "node_pubkey_hex + positive sats required", plan
        )
    if dry_run or not confirm:
        reason = "dry_run" if dry_run else "confirm=False"
        return ValueLayerResult("open_channel", "planned", reason, plan)
    try:
        resp = await _build_client(cfg).open_channel(
            node_pubkey_hex=node_pubkey_hex,
            local_funding_sat=local_funding_sat,
            sat_per_vbyte=sat_per_vbyte,
        )
    except LightningUnavailableError as exc:
        return ValueLayerResult("open_channel", "error", str(exc), plan)
    return ValueLayerResult("open_channel", "executed", "", plan, resp)
