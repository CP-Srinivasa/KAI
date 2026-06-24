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


def _assert_send_allowed(
    action: str,
    *,
    cfg: LightningSettings,
    dry_run: bool,
    confirm: bool,
    irreversible: bool,
    plan: dict[str, Any],
) -> ValueLayerResult | None:
    """B-002 — the SINGLE chokepoint every value-layer write must pass BEFORE the
    node is touched. Returns a terminal ``ValueLayerResult`` (disabled/planned) to
    short-circuit, or ``None`` when the action is cleared to execute.

    Centralising the three gates here (instead of copy-pasting per method) means a
    new write method cannot silently forget one — and the reflection test
    (test_ln_value_layer_send_gate) structurally enforces that every public write
    routes through this function:

      * ``pay_enabled`` master kill-switch (``APP_LN_PAY_ENABLED``) → ``disabled``;
      * ``dry_run`` default → ``planned`` (plan only, no node write);
      * ``irreversible`` actions (on-chain spend / channel ops) additionally need an
        explicit ``confirm=True`` → else ``planned``.
    """
    if not cfg.pay_enabled:
        return ValueLayerResult(action, "disabled", "pay_enabled is False", plan)
    if dry_run:
        return ValueLayerResult(action, "planned", "dry_run", plan)
    if irreversible and not confirm:
        return ValueLayerResult(action, "planned", "confirm=False", plan)
    return None


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
    blocked = _assert_send_allowed(
        "create_invoice", cfg=cfg, dry_run=dry_run, confirm=True, irreversible=False, plan=plan
    )
    if blocked is not None:
        return blocked
    if value_sat <= 0:
        return ValueLayerResult("create_invoice", "error", "value_sat must be > 0", plan)
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
    blocked = _assert_send_allowed(
        "open_channel", cfg=cfg, dry_run=dry_run, confirm=confirm, irreversible=True, plan=plan
    )
    if blocked is not None:
        return blocked
    if not node_pubkey_hex or local_funding_sat <= 0:
        return ValueLayerResult(
            "open_channel", "error", "node_pubkey_hex + positive sats required", plan
        )
    try:
        resp = await _build_client(cfg).open_channel(
            node_pubkey_hex=node_pubkey_hex,
            local_funding_sat=local_funding_sat,
            sat_per_vbyte=sat_per_vbyte,
        )
    except LightningUnavailableError as exc:
        return ValueLayerResult("open_channel", "error", str(exc), plan)
    return ValueLayerResult("open_channel", "executed", "", plan, resp)
