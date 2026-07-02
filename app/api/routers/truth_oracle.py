"""KAI Truth Oracle — L402 pay-per-call API over KAI's SOVEREIGN truth (UC-3/UC-4).

Pre-edge-safe: serves verifiable FACTS only (no prediction, no edge claim):
  * GET  /oracle/onchain-facts  (UC-4) — fee/mempool/block-height from KAI's OWN
    bitcoind node (L1 background cache; never blocks).
  * POST /oracle/timestamp      (UC-3) — anchor a caller-supplied SHA256 hash via
    OpenTimestamps (L3) and return the proof bytes (hex).

Each call is gated by L402 (``app.lightning.l402``): an unpaid request gets a
``402`` with a Lightning invoice + signed token; the caller pays, then retries
with ``Authorization: L402 <token>:<preimage>``. Default OFF
(``APP_LN_L402_ENABLED``); minting the invoice uses the gated value layer
(needs ``receive_enabled`` + a reachable node) — so the oracle only truly transacts
once the operator provisions the receive path. No capital risk to KAI (receive-side,
decoupled from the spend kill-switch via U1).
"""

from __future__ import annotations

import base64
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.client_ip import resolve_client_ip
from app.core.settings import get_settings
from app.lightning.demand_ledger import (
    ACCESS_GRANTED,
    CHALLENGE_MINTED,
    append_demand_event,
    requester_fingerprint,
)
from app.lightning.l402 import (
    L402Error,
    build_challenge_header,
    mint_token,
    parse_authorization,
    verify,
)
from app.lightning.mint_limiter import MintLimiter
from app.lightning.value_layer import create_invoice

router = APIRouter(prefix="/oracle", tags=["truth-oracle"])

# S-002 — process-local invoice-mint rate limiter. Built lazily from settings so a
# config change (caps) takes effect on next build; ``reset_mint_limiter`` is the
# test seam.
_mint_limiter: MintLimiter | None = None


def _get_mint_limiter() -> MintLimiter:
    global _mint_limiter
    if _mint_limiter is None:
        ln = get_settings().lightning
        _mint_limiter = MintLimiter(
            per_key_max=ln.l402_mint_per_min, global_max=ln.l402_mint_budget_per_min
        )
    return _mint_limiter


def reset_mint_limiter() -> None:
    """Test seam: drop the limiter so the next request rebuilds it from settings."""
    global _mint_limiter
    _mint_limiter = None


async def _gate_mint(request: Request, scope: str) -> None:
    """S-002: cap invoice mints BEFORE one is issued (per ip:scope + global budget).

    Raises 429 when the window cap is exhausted — so an unauthenticated flood cannot
    mint unbounded real invoices against the node (DoS/HTLC-flood guard).
    """
    ip = resolve_client_ip(request)  # real caller behind the proxy (not the tunnel IP)
    if not _get_mint_limiter().allow(f"{ip}:{scope}", now=time.monotonic()):
        raise HTTPException(status_code=429, detail="mint rate limit exceeded")


async def _issue_challenge(scope: str, *, requester_fp: str = "") -> None:
    """Mint an invoice + token and raise a 402 challenge. Never returns.

    On a successful mint, logs a ``challenge_minted`` demand event (the interest
    signal for the G0 probe) — fail-soft, never blocks the challenge.
    """
    settings = get_settings()
    price = settings.lightning.l402_default_price_sat
    inv = await create_invoice(value_sat=price, memo=f"kai-oracle:{scope}", dry_run=False)
    if inv.state != "executed":
        # Oracle enabled but the receive path isn't provisioned (receive_enabled off /
        # node unreachable) → honest 503, never a fake invoice. The receive gate is
        # decoupled from the spend kill-switch (U1), so minting needs receive_enabled.
        raise HTTPException(status_code=503, detail=f"oracle pay path unavailable: {inv.detail}")
    r_hash_b64 = str(inv.response.get("r_hash", ""))
    payment_request = str(inv.response.get("payment_request", ""))
    try:
        payment_hash_hex = base64.b64decode(r_hash_b64).hex()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="invalid invoice from node") from exc
    token = mint_token(payment_hash_hex, secret=settings.lightning.l402_secret, scope=scope)
    append_demand_event(
        CHALLENGE_MINTED,
        scope=scope,
        requester_fp=requester_fp,
        price_sat=int(price),
        payment_hash=payment_hash_hex,
    )
    raise HTTPException(
        status_code=402,
        detail="payment required",
        headers={"WWW-Authenticate": build_challenge_header(token, payment_request)},
    )


async def _require_paid(request: Request, scope: str) -> None:
    """Enforce L402 for ``scope``; raise 402 (with a fresh invoice) when unpaid."""
    settings = get_settings()
    if not settings.lightning.l402_enabled:
        raise HTTPException(status_code=503, detail="truth oracle disabled")
    if not settings.lightning.l402_secret:
        raise HTTPException(status_code=503, detail="l402 secret not configured")
    fp = requester_fingerprint(resolve_client_ip(request), secret=settings.lightning.l402_secret)
    try:
        token, preimage = parse_authorization(request.headers.get("Authorization", ""))
    except L402Error:
        await _gate_mint(request, scope)  # S-002: rate-limit BEFORE minting
        await _issue_challenge(scope, requester_fp=fp)
        return  # unreachable (challenge raises)
    v = verify(token, preimage, secret=settings.lightning.l402_secret)
    if not v.valid or v.scope != scope:
        await _gate_mint(request, scope)  # S-002: rate-limit BEFORE minting
        await _issue_challenge(scope, requester_fp=fp)
        return  # unreachable (challenge raises)
    # Paid + scope-matched → serve. Log the conversion (access_granted), fail-soft.
    append_demand_event(ACCESS_GRANTED, scope=scope, payment_hash=v.payment_hash)


@router.get("/onchain-facts")
async def onchain_facts(request: Request) -> dict[str, Any]:
    """UC-4: verifiable on-chain facts from KAI's own node (L402-paid)."""
    await _require_paid(request, "onchain-facts")
    from app.chain.cache import get_cached_chain_status

    status, age = await get_cached_chain_status()
    return {
        "source": "kai_sovereign_bitcoind",
        "chain": status.chain,
        "block_height": status.blocks,
        "synced": status.synced,
        "fee_sat_vb": status.fee_sat_vb,
        "mempool_tx": status.mempool_tx,
        "as_of_age_seconds": age,
    }


@router.get("/fee-series")
async def fee_series(request: Request) -> dict[str, Any]:
    """UC-5: sovereign fee/mempool time series from KAI's own L1 stream (L402-paid).

    Verifiable FACTS only — raw observations + deterministic min/median/max, never a
    forecast. Source is the L1 fee-shadow stream the chain scheduler already writes.
    """
    await _require_paid(request, "fee-series")
    from app.chain.fee_series import build_fee_series
    from app.signals.l2_features import read_onchain_fee_shadow

    records = read_onchain_fee_shadow("artifacts/onchain_fee_shadow.jsonl")
    return build_fee_series(records)


class TimestampRequest(BaseModel):
    sha256_hex: str = Field(..., min_length=64, max_length=64)


@router.post("/timestamp")
async def timestamp(request: Request, body: TimestampRequest) -> dict[str, Any]:
    """UC-3: anchor a caller hash via OpenTimestamps (L3), return the proof (L402-paid)."""
    await _require_paid(request, "timestamp")
    digest = body.sha256_hex.strip().lower()
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        raise HTTPException(status_code=422, detail="sha256_hex must be 32-byte hex")
    import tempfile
    from pathlib import Path

    from app.integrity.anchor import AnchorUnavailableError, OpenTimestampsStamper

    try:
        proof_path = OpenTimestampsStamper().stamp(digest, Path(tempfile.mkdtemp()))
    except AnchorUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"anchoring unavailable: {exc}") from exc
    proof_bytes = Path(proof_path).read_bytes()
    return {
        "sha256_hex": digest,
        "ots_proof_hex": proof_bytes.hex(),
        "note": "verify/upgrade with `ots upgrade` once the calendar aggregation is mined",
    }
