"""Lightning (RaspiBlitz/lnd) integration settings.

Extracted from ``app.core.settings`` (god-file ratchet, D-234): the read-only
Lightning client configuration lives here; ``settings.py`` re-exports
``LightningSettings`` so existing imports keep working.

See KAI-mirror/kai_lightning_integration_plan_20260614.md for the full phased
plan, macaroon-permission matrix and threat model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LightningSettings(BaseSettings):
    """RaspiBlitz/lnd Lightning-node integration (KAI as read-only client first).

    Default-off, shadow-first, fail-closed — the trading loop is never blocked by
    Lightning availability. KAI is a *client* of the RaspiBlitz node; no KAI code
    runs on the node and only scope-minimal macaroons ever leave it (NEVER admin).

      - ``enabled=False`` (default): no Lightning surface is consulted anywhere.
      - ``enabled=True`` (Phase 1): read-only access via ``readonly.macaroon`` over
        the lnd REST API (getinfo/channelbalance/feereport). Pure observation.

    Invoice/pay capabilities live behind their own flags, least-privilege
    credentials and the capital gate. ``pay_enabled`` is the wired master
    kill-switch for every send path and defaults to False.
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_LN_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    # lnd REST endpoint on the RaspiBlitz node (LAN, later WireGuard overlay IP).
    host: str = Field(default="192.168.178.51")
    rest_port: int = Field(default=8080, ge=1, le=65535)
    # Read-only credential (Phase 1).  This legacy env name intentionally stays
    # stable, but it is no longer reused by ANY write path.  Value capabilities
    # below each get their own independently revocable macaroon (W0-P6).
    macaroon_path: str = Field(default="", repr=False)
    macaroon_hex: str = Field(default="", repr=False)
    # Invoice create/list. Recommended permissions: invoices:read + invoices:write.
    invoice_macaroon_path: str = Field(default="", repr=False)
    invoice_macaroon_hex: str = Field(default="", repr=False)
    # BOLT11 pay + keysend + TrackPaymentV2 crash reconciliation. Recommended
    # permissions: offchain:read + offchain:write (no invoice/onchain/channel).
    payment_macaroon_path: str = Field(default="", repr=False)
    payment_macaroon_hex: str = Field(default="", repr=False)
    # On-chain withdraw. Recommended permission: onchain:write only.
    onchain_macaroon_path: str = Field(default="", repr=False)
    onchain_macaroon_hex: str = Field(default="", repr=False)
    # Channel open/close. Kept separate because it can lock capital and incur
    # on-chain fees even when no payment leaves the operator's custody.
    channel_macaroon_path: str = Field(default="", repr=False)
    channel_macaroon_hex: str = Field(default="", repr=False)
    # Path to lnd tls.cert (used to verify the node's self-signed TLS).
    tls_cert_path: str = Field(default="")
    timeout_seconds: float = Field(default=10.0, gt=0)
    # Wired master kill-switch for every value-layer send; default-off.
    pay_enabled: bool = Field(default=False)
    # Receive-side capability (capital-free): mint inbound BOLT11 invoices. Decoupled
    # from ``pay_enabled`` so "Empfangen vor Senden" can be enabled WITHOUT un-gating
    # any spend path. Default OFF; flipped independently for the L402 demand probe.
    # Env ``APP_LN_RECEIVE_ENABLED``.
    receive_enabled: bool = Field(default=False)
    # L402 Truth-API (UC-3/UC-4): pay-per-call paywall over KAI's sovereign truth.
    # Default OFF; ``l402_secret`` signs the access tokens (HMAC) and MUST be set
    # before enabling. Env ``APP_LN_L402_ENABLED`` / ``APP_LN_L402_SECRET``.
    l402_enabled: bool = Field(default=False)
    l402_secret: str = Field(default="", repr=False)
    l402_default_price_sat: int = Field(default=10, ge=1)
    # S-002 receive-side DoS guard: cap L402 invoice MINTS per window. dry-run does
    # NOT protect the receive side (every unpaid request mints a real invoice), so
    # these caps MUST be in force before L402 is enabled. Per-key (ip:scope) and a
    # global budget per 60s window; <=0 disables that dimension.
    l402_mint_per_min: int = Field(default=5, ge=0)
    l402_mint_budget_per_min: int = Field(default=60, ge=0)
    # B-005 capital-confirm 2nd factor (HOTP) for irreversible value-layer POSTs.
    # ``hotp_seed_path`` empty (default) → no confirm is possible → no capital
    # execute can ever pass needs_confirm (safe-by-default). Operator provisions the
    # base32 seed (mode 600) only at G1.
    hotp_seed_path: str = Field(default="", repr=False)
    hotp_journal_path: str = Field(default="artifacts/ln_hotp_journal.jsonl")
    # Node-reputation telemetry capture cadence (read-only uptime/connectivity/
    # routing-income trend → its own shadow stream ``artifacts/ln_reputation.jsonl``;
    # no capital path). Only runs when ``enabled``. Env
    # ``APP_LN_REPUTATION_INTERVAL_SECONDS``. 900s (15min) — node health moves
    # slowly, so this is ample.
    reputation_interval_seconds: int = Field(default=900, gt=0)
    # Static Channel Backup monitor (read-only): the RaspiBlitz-side sync copies
    # channel.backup to ``scb_path``; KAI hashes that local copy and records the
    # last observed hash in ``scb_baseline_path``. A copy older than two hours is
    # operationally stale even when its hash is unchanged. Env:
    # ``APP_LN_SCB_PATH`` / ``..._BASELINE_PATH`` / ``..._MAX_AGE_SECONDS``.
    scb_path: str = Field(default="")
    scb_baseline_path: str = Field(default="artifacts/scb_baseline.json")
    scb_max_age_seconds: int = Field(default=7200, ge=60)
    # RaspiBlitz info mirror (dashboard "Node & Chain"): read-only system snapshot
    # (CPU/temp/mem/SSD + bitcoind/lnd) pulled over a FORCED-COMMAND ssh key that
    # can only run the info script on the node (no shell, no pty, no sudo surface).
    # Default OFF; fail-soft — the panel shows "n/v" when disabled/unreachable.
    # Env ``APP_LN_BLITZ_INFO_ENABLED`` / ``..._SSH_TARGET`` / ``..._SSH_KEY_PATH``.
    blitz_info_enabled: bool = Field(default=False)
    blitz_info_ssh_target: str = Field(default="admin@192.168.178.51")
    blitz_info_ssh_key_path: str = Field(default="")
    blitz_info_timeout_seconds: float = Field(default=25.0, gt=0)

    @model_validator(mode="after")
    def _require_tls_cert_when_enabled(self) -> LightningSettings:
        """Boot-time guardrail (mirrors ``validate_mode_guardrails`` in settings.py).

        An enabled Lightning client with an empty ``tls_cert_path`` makes the lnd REST
        client (``app.lightning.client``: ``verify = tls_cert_path or False``) silently
        DISABLE TLS verification — exposing the macaroon and all node traffic to a MITM
        on the LAN/overlay. Refuse to construct rather than fail open. A disabled client
        (``enabled=False``) never touches the node, so it is unaffected.
        """
        if self.enabled and not self.tls_cert_path.strip():
            raise ValueError(
                "APP_LN_ENABLED=true requires APP_LN_TLS_CERT_PATH (path to the lnd "
                "tls.cert). An empty cert path silently disables TLS verification in "
                "the lnd REST client — refusing to boot fail-open."
            )
        return self

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.rest_port}"

    def macaroon_credentials(
        self, scope: Literal["read", "invoice", "payment", "onchain", "channel"]
    ) -> tuple[str, str]:
        """Return ``(hex, path)`` for one least-privilege capability.

        There is deliberately no fallback from a write scope to the read
        credential.  A missing capability must fail closed instead of silently
        recreating the single-all-powerful-macaroon configuration W0-P6 removes.
        """
        if scope == "read":
            return self.macaroon_hex, self.macaroon_path
        if scope == "invoice":
            return self.invoice_macaroon_hex, self.invoice_macaroon_path
        if scope == "payment":
            return self.payment_macaroon_hex, self.payment_macaroon_path
        if scope == "onchain":
            return self.onchain_macaroon_hex, self.onchain_macaroon_path
        return self.channel_macaroon_hex, self.channel_macaroon_path


class LightningBootError(RuntimeError):
    """An ENABLED Lightning client is misconfigured in a way that would fail OPEN
    (missing/unreadable/expired TLS cert). Raised at startup to abort the boot."""


def validate_lightning_boot(cfg: LightningSettings) -> None:
    """Fail-closed startup guardrail for an ENABLED Lightning client.

    The model-validator already refuses an EMPTY ``tls_cert_path``. This runs ONCE at
    real application startup (not on every settings construction — unit tests may pass
    placeholder cert paths) and additionally proves the configured cert is actually
    USABLE, so an enabled client can never silently ride a broken trust anchor:

      * the file exists and is readable (not a stale/typo'd path);
      * it parses as a PEM X.509 certificate (not truncated/garbage);
      * it is not expired (an expired ``tls.cert`` makes every node call fail with an
        opaque TLS error deep in the request path — here it aborts boot with the
        precise reason instead).

    A disabled client (``enabled=False``) never touches the node → no-op.
    """
    if not cfg.enabled:
        return
    cert_path = Path(cfg.tls_cert_path.strip())
    try:
        raw = cert_path.read_bytes()
    except FileNotFoundError as exc:
        raise LightningBootError(
            f"APP_LN_TLS_CERT_PATH does not exist: {cert_path} — refusing to boot fail-open"
        ) from exc
    except OSError as exc:
        raise LightningBootError(
            f"APP_LN_TLS_CERT_PATH is unreadable ({cert_path}): {exc} — refusing to boot"
        ) from exc
    if not raw.strip():
        raise LightningBootError(f"APP_LN_TLS_CERT_PATH is an empty file: {cert_path}")
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(raw)
    except Exception as exc:  # noqa: BLE001 — any parse failure is a hard boot-blocker
        raise LightningBootError(
            f"APP_LN_TLS_CERT_PATH is not a valid PEM X.509 certificate ({cert_path}): {exc}"
        ) from exc
    if cert.not_valid_after_utc < datetime.now(UTC):
        raise LightningBootError(
            f"lnd TLS cert expired at {cert.not_valid_after_utc.isoformat()} "
            f"({cert_path}) — refusing to boot with an expired trust anchor"
        )
