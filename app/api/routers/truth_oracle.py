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

import asyncio
import base64
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.client_ip import resolve_client_ip
from app.chain.cache import CHAIN_CACHE_TTL_SECONDS
from app.core.settings import get_settings
from app.lightning.demand_ledger import (
    ACCESS_GRANTED,
    CHALLENGE_MINTED,
    PAID_UNAVAILABLE,
    append_demand_event,
    requester_fingerprint,
)
from app.lightning.l402 import (
    L402Error,
    L402Verdict,
    build_challenge_header,
    mint_token,
    parse_authorization,
    verify,
)
from app.lightning.mint_limiter import MintLimiter
from app.lightning.receive_gate import create_invoice

router = APIRouter(prefix="/oracle", tags=["truth-oracle"])

# S-002 — process-local invoice-mint rate limiter. Built lazily from settings so a
# config change (caps) takes effect on next build; ``reset_mint_limiter`` is the
# test seam.
_mint_limiter: MintLimiter | None = None
ONCHAIN_FACTS_MAX_AGE_SECONDS = CHAIN_CACHE_TTL_SECONDS * 2
_timestamp_submit_slots = asyncio.Semaphore(2)
# Oracle invoices expire after 300 s (``LndRestClient.add_invoice`` default expiry).
_INVOICE_EXPIRY_MINUTES = 5


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
    base_scope = scope.split(":", 1)[0]
    if not _get_mint_limiter().allow(f"{ip}:{base_scope}", now=time.monotonic()):
        raise HTTPException(status_code=429, detail="mint rate limit exceeded")


def _require_oracle_enabled() -> Any:
    settings = get_settings()
    if not settings.lightning.l402_enabled:
        raise HTTPException(status_code=503, detail="truth oracle disabled")
    if not settings.lightning.l402_secret:
        raise HTTPException(status_code=503, detail="l402 secret not configured")
    return settings


def _valid_paid_token(request: Request, scope: str) -> L402Verdict | None:
    settings = _require_oracle_enabled()
    try:
        token, preimage = parse_authorization(request.headers.get("Authorization", ""))
    except L402Error:
        return None
    verdict = verify(token, preimage, secret=settings.lightning.l402_secret)
    return verdict if verdict.valid and verdict.scope == scope else None


def _valid_expired_replay_token(request: Request, scope: str) -> L402Verdict | None:
    """Validate an expired token fully; callers must separately prove a stored job."""
    settings = _require_oracle_enabled()
    try:
        token, preimage = parse_authorization(request.headers.get("Authorization", ""))
    except L402Error:
        return None
    verdict = verify(
        token,
        preimage,
        secret=settings.lightning.l402_secret,
        allow_expired=True,
    )
    return (
        verdict
        if verdict.valid and verdict.reason == "ok_expired" and verdict.scope == scope
        else None
    )


async def _issue_challenge(
    scope: str, *, requester_fp: str = "", telemetry_scope: str | None = None
) -> NoReturn:
    """Mint an invoice + token and raise a 402 challenge. Never returns.

    On a successful mint, logs a ``challenge_minted`` demand event (the interest
    signal for the G0 probe) — fail-soft, never blocks the challenge.
    """
    settings = get_settings()
    price = settings.lightning.l402_default_price_sat
    public_scope = telemetry_scope or scope
    inv = await create_invoice(value_sat=price, memo=f"kai-oracle:{public_scope}", dry_run=False)
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
        scope=public_scope,
        requester_fp=requester_fp,
        price_sat=int(price),
        payment_hash=payment_hash_hex,
    )
    raise HTTPException(
        status_code=402,
        detail="payment required",
        headers={"WWW-Authenticate": build_challenge_header(token, payment_request)},
    )


async def _require_paid(
    request: Request,
    scope: str,
    *,
    telemetry_scope: str | None = None,
    before_mint: Callable[[], Awaitable[None]] | None = None,
) -> L402Verdict:
    """Enforce L402 for ``scope``; raise 402 (with a fresh invoice) when unpaid.

    ``before_mint`` runs after the S-002 limiter and before the invoice, so
    route-specific pre-mint checks never become work an unpaid flood can force.
    """
    settings = _require_oracle_enabled()
    fp = requester_fingerprint(resolve_client_ip(request), secret=settings.lightning.l402_secret)
    verdict = _valid_paid_token(request, scope)
    if verdict is None:
        await _gate_mint(request, scope)  # S-002: rate-limit BEFORE minting
        if before_mint is not None:
            await before_mint()
        await _issue_challenge(scope, requester_fp=fp, telemetry_scope=telemetry_scope)
    # Paid + scope-matched → serve. Log the conversion (access_granted), fail-soft.
    append_demand_event(
        ACCESS_GRANTED, scope=telemetry_scope or scope, payment_hash=verdict.payment_hash
    )
    return verdict


@router.get("/onchain-facts")
async def onchain_facts(request: Request) -> dict[str, Any]:
    """UC-4: verifiable on-chain facts from KAI's own node (L402-paid)."""
    from app.chain.cache import get_cached_chain_status

    _require_oracle_enabled()
    status, age = await get_cached_chain_status()
    state = str(getattr(status, "state", "unknown"))
    blocks = getattr(status, "blocks", 0)
    headers = getattr(status, "headers", 0)
    best_block_hash = getattr(status, "best_block_hash", "")
    chain = getattr(status, "chain", "")
    age_seconds = (
        float(age) if isinstance(age, (int, float)) and not isinstance(age, bool) else None
    )
    ready = (
        state == "ok"
        and getattr(status, "reachable", False) is True
        and getattr(status, "synced", False) is True
        and isinstance(chain, str)
        and bool(chain)
        and isinstance(blocks, int)
        and not isinstance(blocks, bool)
        and blocks > 0
        and isinstance(headers, int)
        and not isinstance(headers, bool)
        # Exact equality on purpose: the adapter only reports ``synced`` when
        # ``blocks == headers`` (``app/chain/adapter.py``), so any header lead is
        # already not-synced — a tolerance here would be dead code.
        and headers == blocks
        and isinstance(best_block_hash, str)
        and len(best_block_hash) == 64
        and all(char in "0123456789abcdef" for char in best_block_hash.lower())
        and age_seconds is not None
        and math.isfinite(age_seconds)
        and 0.0 <= age_seconds <= ONCHAIN_FACTS_MAX_AGE_SECONDS
    )
    if not ready:
        if (
            age_seconds is not None
            and math.isfinite(age_seconds)
            and age_seconds > ONCHAIN_FACTS_MAX_AGE_SECONDS
        ):
            public_state = "stale"
        elif state in {"pending", "disabled", "unavailable"}:
            public_state = state
        else:
            public_state = "not_ready"
        paid = _valid_paid_token(request, "onchain-facts")
        if paid is not None:
            append_demand_event(
                PAID_UNAVAILABLE,
                scope="onchain-facts",
                payment_hash=paid.payment_hash,
            )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "onchain_facts_unavailable",
                "state": public_state,
                "retriable": True,
            },
            headers={"Retry-After": "5"},
        )
    assert age_seconds is not None  # included in the readiness contract above

    # Readiness precedes L402 minting: callers are never asked to pay for a fact
    # already known to be unavailable. A paid token is stateless and remains
    # reusable for the same scope when the cache becomes healthy again — but
    # only within its L402 TTL (``app.lightning.l402._DEFAULT_TTL_S``, 3600 s).
    # An outage longer than the remaining TTL leaves a paid call undelivered;
    # ``PAID_UNAVAILABLE`` above keeps that visible in the demand ledger.
    await _require_paid(request, "onchain-facts")
    observed_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return {
        "source": "kai_sovereign_bitcoind",
        "chain": chain,
        "block_height": blocks,
        "headers": headers,
        "best_block_hash": best_block_hash.lower(),
        "synced": status.synced,
        "fee_sat_vb": status.fee_sat_vb,
        "mempool_tx": status.mempool_tx,
        "observed_at_utc": observed_at.isoformat(),
        "as_of_age_seconds": age_seconds,
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


@router.get("/verdicts")
async def verdicts(request: Request, limit: int = 50) -> dict[str, Any]:
    """UC-2 (fact flavour): the auditable falsification VERDICTS KAI has published.

    Pre-edge-safe: serves the platform's TRUTH product — "we tested hypothesis H
    under pre-registered criteria and it passed/failed" — never a prediction or an
    edge/alpha claim. Each row carries its SHA-256 attestation hash + prereg link so
    any buyer can re-verify the claim was not altered after the fact, and the
    tamper-evident attestation ledger's integrity is reported alongside. L402-paid.
    """
    await _require_paid(request, "verdicts")
    from app.research.verdict_report import list_verdict_reports
    from app.truth.ledger import verify_ledger

    rows = [
        {
            **row,
            "proof_bundle": f"/oracle/verdicts/proof?attestation_hash={row['attestation_hash']}",
        }
        for row in list_verdict_reports()
    ]
    n = max(0, min(int(limit), 500))  # bound the response; -ve/huge limits clamp
    integrity = verify_ledger()
    return {
        "source": "kai_falsification_platform",
        "count": len(rows),
        "verdicts": rows[:n],
        "attestation_ledger": {"chain_ok": integrity["ok"], "records": integrity["records"]},
        "verify": (
            "recompute SHA-256 over each report's canonical (sorted-keys, compact) payload "
            "JSON and compare to attestation_hash (app.truth.attestation.verify_attestation)"
        ),
    }


@router.get("/verdicts/proof")
async def verdict_proof(request: Request, attestation_hash: str) -> dict[str, Any]:
    """Das vollstaendige Beweispaket zu einem Verdict (D-288, Befund 4).

    Bericht, Ledger-Segment bis zum verankerten Tip, ``.ots`` und KAIs
    Bitcoin-Pruefbeleg — genug, damit ein Kaeufer ohne KAI nachrechnet
    (``scripts/verify_truth_bundle.py``, optional gegen mempool.space).
    Dieselbe L402-Freischaltung wie ``/verdicts``.
    """
    await _require_paid(request, "verdicts")
    if len(attestation_hash) != 64 or any(c not in "0123456789abcdef" for c in attestation_hash):
        raise HTTPException(status_code=422, detail="attestation_hash must be 64 lowercase hex")
    from app.truth.proof_bundle import build_verdict_bundle

    bundle = build_verdict_bundle(
        attestation_hash, proofs_dir=Path(get_settings().integrity.proofs_dir)
    )
    if bundle is None:
        raise HTTPException(
            status_code=404, detail="no attested and anchored verdict for this hash (yet)"
        )
    return bundle


class TimestampRequest(BaseModel):
    sha256_hex: str = Field(..., min_length=64, max_length=64)


@router.post("/timestamp")
async def timestamp(request: Request, body: TimestampRequest) -> dict[str, Any]:
    """UC-3: anchor a caller hash via OpenTimestamps (L3), return the proof (L402-paid)."""
    digest = body.sha256_hex.strip().lower()
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        raise HTTPException(status_code=422, detail="sha256_hex must be 32-byte hex")

    from app.integrity.timestamp_jobs import (
        TimestampJobCapacityError,
        TimestampJobConflictError,
        TimestampJobStore,
        TimestampJobUnavailableError,
    )

    # The signed L402 scope binds this payment to exactly one canonical digest.
    scope = f"timestamp:{digest}"
    settings = get_settings()
    if not settings.integrity.enabled or settings.integrity.stamper != "opentimestamps":
        raise HTTPException(
            status_code=503,
            detail={"code": "timestamp_service_disabled", "retriable": False},
        )
    jobs_root = Path(settings.integrity.timestamp_jobs_dir)
    store = TimestampJobStore(jobs_root)
    auth = _valid_paid_token(request, scope)
    if auth is None:
        replay = _valid_expired_replay_token(request, scope)
        if replay is not None and await asyncio.to_thread(
            store.has_binding, payment_hash=replay.payment_hash, digest=digest
        ):
            auth = replay
    if auth is None:
        # Capacity is checked after the S-002 limiter and before minting, so KAI
        # never invoices for work it already knows it cannot durably accept and
        # an unpaid flood cannot force store scans. Challenges reserve nothing,
        # so keep headroom for every invoice that can still be paid.
        reserve = settings.lightning.l402_mint_budget_per_min * _INVOICE_EXPIRY_MINUTES

        async def _capacity_before_mint() -> None:
            if not await asyncio.to_thread(store.has_capacity, reserve=reserve):
                raise HTTPException(
                    status_code=503,
                    detail={"code": "timestamp_capacity_exhausted", "retriable": False},
                )

        auth = await _require_paid(
            request, scope, telemetry_scope="timestamp", before_mint=_capacity_before_mint
        )

    try:
        async with _timestamp_submit_slots:
            record, proof_bytes = await asyncio.to_thread(
                store.submit,
                payment_hash=auth.payment_hash,
                digest=digest,
            )
    except TimestampJobConflictError as exc:
        raise HTTPException(
            status_code=409, detail="payment already bound to another digest"
        ) from exc
    except TimestampJobCapacityError as exc:
        # Paid but undeliverable: keep it visible in the demand ledger.
        append_demand_event(PAID_UNAVAILABLE, scope="timestamp", payment_hash=auth.payment_hash)
        raise HTTPException(
            status_code=503,
            detail={"code": "timestamp_capacity_exhausted", "retriable": False},
        ) from exc
    except (TimestampJobUnavailableError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "timestamp_pending_retry", "retriable": True},
            headers={"Retry-After": "5"},
        ) from exc
    append_demand_event(ACCESS_GRANTED, scope="timestamp", payment_hash=auth.payment_hash)
    return {
        "sha256_hex": digest,
        "ots_proof_hex": proof_bytes.hex(),
        "proof_sha256": record["proof_sha256"],
        "status": record["state"],
        "note": (
            "Bitcoin-confirmed OpenTimestamps proof"
            if record["state"] == "bitcoin_confirmed"
            else "calendar commitment only; verify/upgrade after Bitcoin confirmation"
        ),
    }
