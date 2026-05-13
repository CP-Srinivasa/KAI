"""D-191 Re-Entry-Gate — hard invariants for the 2026-05-16 cutover.

Background
----------
The 30-day TradingView pivot (D-125) re-opens execution gating on
2026-05-16. By that date a small set of guard-rails MUST be wired in:
provenance secret present, replay cache persistent + on an absolute
path, listener heartbeat fresh, observability complete (B-002).

This module defines the *capability switches* that, when ``enabled=true``,
force ``AppSettings`` to refuse to start unless every selected invariant
holds. It is intentionally a separate ``BaseSettings`` block so the
operator can stage rollout via the ``RE_ENTRY_MODE_*`` env-prefix
without touching any other settings group.

Defaults
--------
``enabled`` defaults to **False** so today's laptop boot remains
unchanged. Each ``enforce_*`` flag defaults to **True** so that the
moment the operator flips ``RE_ENTRY_MODE_ENABLED=1`` *all* invariants
trip together — except ``enforce_observability_complete``, which
defaults to **False** because B-002 (LLM-failure-rate, latency p95)
is still ``not_implemented``. Forcing it on by default would brick
boot before the telemetry exists; the operator opts in once it does.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ReEntryModeProfile(BaseSettings):
    """Capability-gate profile for the D-191 re-entry cutover."""

    model_config = SettingsConfigDict(
        env_prefix="RE_ENTRY_MODE_",
        env_file=".env",
        extra="ignore",
    )

    # Master switch. Default off — production boot today is unaffected.
    enabled: bool = Field(default=False)

    # S-001: ALERT_PROVENANCE_SECRET must be set (HMAC-Seal on signals).
    enforce_provenance_secret: bool = Field(default=True)

    # S-002a: TRADINGVIEW_WEBHOOK_REPLAY_CACHE_PERSISTENT must be true.
    enforce_replay_cache_persistent: bool = Field(default=True)

    # S-002b: TRADINGVIEW_WEBHOOK_REPLAY_CACHE_DB_PATH must be absolute.
    enforce_replay_cache_absolute_path: bool = Field(default=True)

    # S-003: Telegram channel ingest heartbeat path must be configured
    # (the worker writes a heartbeat file at startup + periodically;
    # the watchdog reads it via canonical_read).
    enforce_watchdog_heartbeat: bool = Field(default=True)

    # B-002: complete observability surface (LLM-failure-rate, latency p95).
    # Default *False* until the telemetry actually exists. Flip to True
    # once /status no longer returns "not_implemented" for those fields.
    enforce_observability_complete: bool = Field(default=False)
