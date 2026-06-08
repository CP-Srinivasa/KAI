from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import EntryMode, ExecutionMode
from app.core.errors import ConfigurationError
from app.core.re_entry_mode import ReEntryModeProfile
from app.core.schema_runtime import (
    validate_json_schema_payload as _validate_json_schema_payload,
)
from app.core.schema_runtime import (
    validate_runtime_config_payload as _validate_runtime_config_payload,
)


def _cost_model_paper_round_trip_pct() -> float:
    """Default for RiskSettings.round_trip_fee_pct, derived from the CostModel.

    Single source: the V1 cost-geometry gate must charge the SAME round-trip
    cost the paper engine charges (Sprint B). Imported lazily to avoid any
    import-order coupling between settings and execution. If the CostModel is
    unavailable for any reason, fall back to a small positive number rather than
    the legacy 1.2% worst-case (re-pinning 1.2 would re-introduce Gate/Engine
    drift). 0.2% mirrors the realistic paper default (10 bp/side round-trip).
    """
    try:
        from app.execution.cost_model import CostModel

        return CostModel().round_trip_fee_pct(venue="paper")
    except Exception:  # noqa: BLE001 — config default must never crash startup
        return 0.20


def _strip_secret(value: object) -> object:
    # SAT-C-006: trailing newline / BOM aus copy-paste killt sonst Signaturen
    # ohne klaren Fehler ("invalid_signature" sieht wie Angriff aus, ist aber Bug).
    if isinstance(value, str):
        return value.strip().lstrip("\ufeff")
    return value


def validate_json_schema_payload(
    payload: Mapping[str, object],
    *,
    schema_filename: str,
    label: str,
) -> dict[str, object]:
    """Compatibility wrapper that delegates to the canonical runtime validator."""

    return _validate_json_schema_payload(
        payload,
        schema_filename=schema_filename,
        label=label,
    )


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", env_file=".env", extra="ignore")

    url: str = Field(default="postgresql+asyncpg://postgres:postgres@localhost:5432/ai_analyst_bot")
    pool_size: int = Field(default=20)
    max_overflow: int = Field(default=50)
    echo: bool = Field(default=False)


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALERT_", env_file=".env", extra="ignore")

    telegram_enabled: bool = Field(default=False)
    telegram_token: str = Field(default="", repr=False)
    telegram_chat_id: str = Field(default="")
    email_enabled: bool = Field(default=False)
    email_host: str = Field(default="")
    email_port: int = Field(default=587)
    email_user: str = Field(default="")
    email_password: str = Field(default="", repr=False)
    email_from: str = Field(default="")
    email_to: str = Field(default="")
    dry_run: bool = Field(default=True)
    # Minimum priority score (1–10) required to trigger an alert.
    min_priority: int = Field(default=7)
    # D-227: Operator-tunable bullish directional-confidence gate
    # (app/alerts/eligibility.py). Default 0.8 = unchanged behaviour. Lower it
    # (env ``ALERT_MIN_DIRECTIONAL_CONFIDENCE_BULLISH``) only once the D-148
    # blocked-alert recall proxy shows acceptable would-have-precision for the
    # 0.7 bucket. Bearish stays hard-pinned at 0.95 (D-122), not exposed here.
    min_directional_confidence_bullish: float = Field(default=0.8, ge=0.0, le=1.0)
    # Digest mode: accumulate alerts and send as a batch instead of individually.
    digest_enabled: bool = Field(default=False)
    digest_interval_minutes: int = Field(default=60)
    # Re-Entry-Gate target date (TV-Pivot D-125). Operator-tunable via
    # ``ALERT_REENTRY_TARGET_DATE`` so the historical 2026-05-16 default no
    # longer rots silently as a backend module constant. KAI never invents a
    # new target: if the configured date is in the past (today's default) the
    # dashboard renders it ``expired``/historical; an empty/invalid value
    # fails safe to ``requires_re_evaluation`` rather than crashing.
    reentry_target_date: str = Field(default="2026-05-16")
    # D-125 / SAT-C-PROV-20260422-001 — HMAC secret for sealing
    # ``SignalProvenance.provenance_hash`` at alert/outcome write time. Empty
    # = hash stays None (source/version/signal_path_id still persist), which
    # is fail-open for the seal but still satisfies TV-Pivot-Bedingung 3 on
    # the three non-negotiable fields. Set in ``.env`` as
    # ``ALERT_PROVENANCE_SECRET`` to enable tamper-evident provenance.
    provenance_secret: str = Field(default="", repr=False)
    # V8.3 / SAT-C-V8-002 — zero-downtime rotation of ``provenance_secret``.
    # Mirrors the APP_API_KEY / APP_API_KEY_NEXT pattern from SENTR-F-008.
    # Writes always use ``provenance_secret`` (primary); verification checks
    # both when ``provenance_secret_next`` is set. Rollover flow:
    #   1. operator sets ALERT_PROVENANCE_SECRET_NEXT=<new>, redeploys —
    #      existing sealed rows remain verifiable, new rows still signed
    #      with old key.
    #   2. grace period: old rows continue to verify via primary, new rows
    #      written under primary.
    #   3. operator promotes ALERT_PROVENANCE_SECRET=<new>, sets
    #      ALERT_PROVENANCE_SECRET_NEXT=<old> — new rows signed with new,
    #      historical rows still verify via next.
    #   4. after the retention window of historical sealed rows, operator
    #      clears ALERT_PROVENANCE_SECRET_NEXT="". Old-key rows are then
    #      unverifiable by design — that is the rotation being complete.
    # Empty string = disabled (single-secret mode, no behaviour change).
    provenance_secret_next: str = Field(default="", repr=False)

    _strip_secrets = field_validator(
        "telegram_token",
        "email_password",
        "provenance_secret",
        "provenance_secret_next",
        mode="before",
    )(_strip_secret)


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = Field(default="gpt-4o")
    openai_timeout: int = Field(default=30)

    anthropic_api_key: str = Field(default="", repr=False)
    anthropic_model: str = Field(default="claude-3-7-sonnet-20250219")
    anthropic_timeout: int = Field(default=30)

    gemini_api_key: str = Field(default="", repr=False)
    gemini_model: str = Field(default="gemini-2.5-flash")
    gemini_timeout: int = Field(default=30)

    youtube_api_key: str = Field(default="", repr=False)
    newsdata_api_key: str = Field(default="", repr=False)
    x_bearer_token: str = Field(default="", repr=False)

    xai_api_key: str = Field(default="", repr=False)
    xai_model: str = Field(default="grok-4")
    xai_timeout: int = Field(default=30)
    xai_fallback_enabled: bool = Field(default=False)

    _strip_secrets = field_validator(
        "openai_api_key",
        "anthropic_api_key",
        "gemini_api_key",
        "youtube_api_key",
        "newsdata_api_key",
        "x_bearer_token",
        "xai_api_key",
        mode="before",
    )(_strip_secret)


class SourceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SOURCE_", env_file=".env", extra="ignore")

    fetch_timeout: int = Field(default=15)
    max_retries: int = Field(default=3)
    user_agent: str = Field(default="ai-analyst-bot/0.1")


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK_", env_file=".env", extra="ignore")

    # Capital limits
    initial_equity: float = Field(default=10000.0)
    max_risk_per_trade_pct: float = Field(default=0.25)  # max 0.25% per trade
    max_daily_loss_pct: float = Field(default=1.0)  # max 1% daily loss
    max_total_drawdown_pct: float = Field(default=5.0)  # max 5% drawdown
    max_open_positions: int = Field(default=3)
    max_leverage: float = Field(default=1.0)
    # DS-20260528-V2: minimum order notional (USD). Sizing uses remaining cash
    # as equity, so a nearly-deployed portfolio yields dust orders (~1e-16 units)
    # that fill but take no real position. Orders below this notional are rejected.
    min_notional_usd: float = Field(default=10.0)
    # DS-20260529-V2: hard upper cap on a single position's notional, as % of
    # equity. A tight stop (small ATR → huge units) can bind 50-70% of equity and
    # trip the 25% diversification asset-cap → whole order rejected → loop deadlock.
    # 20% sits strictly below the 25% diversification cap (buffer + headroom for
    # ~5 positions at max_open_positions=6). <= 0 disables the cap.
    # env: RISK_MAX_POSITION_SIZE_PCT
    max_position_size_pct: float = Field(default=20.0)

    # Safety gates (must remain True)
    require_stop_loss: bool = Field(default=True)
    allow_averaging_down: bool = Field(default=False)
    allow_martingale: bool = Field(default=False)
    kill_switch_enabled: bool = Field(default=True)

    # Signal quality gates
    min_signal_confidence: float = Field(default=0.75)
    min_signal_confluence_count: int = Field(default=2)

    # Bayesian Confidence Engine (additiv, Schatten-Modus per Default)
    # - enabled=True   → Engine läuft, Felder werden auf SignalCandidate gehängt
    # - shadow_only=True → Engine-Werte nur loggen/persistieren, nie filtern
    # - shadow_only=False + enabled=True → harte Gates aktiv (siehe min/max)
    bayes_confidence_enabled: bool = Field(default=False)
    bayes_confidence_shadow_only: bool = Field(default=True)
    min_bayes_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    max_bayes_uncertainty: float = Field(default=1.0, ge=0.0, le=1.0)

    # ATR Geometrie
    atr_multiplier: float = Field(default=2.0)
    tp_atr_multiplier: float = Field(default=4.0)

    # Regime Filter
    regime_filter_enabled: bool = Field(default=True)
    regime_sma_period: int = Field(default=200)

    # Cooldown after loss/error (minutes)
    cooldown_after_loss_minutes: int = Field(default=30)
    cooldown_after_error_minutes: int = Field(default=10)

    # NEO-V1 (2026-06-01): cost-aware SL geometry gate. Reject orders whose stop
    # distance cannot clear the round-trip transaction cost
    # (|entry-SL|/entry < min_sl_cost_multiple x round_trip_fee_pct).
    #
    # Sprint B (CostModel, single source): round_trip_fee_pct is NO LONGER a
    # hand-set 1.2% worst-case. Its default is DERIVED from the CostModel paper
    # venue (10 bp/side -> 0.2% round-trip) so the gate, the paper engine and the
    # backtest all charge the SAME cost — no Gate/Engine drift. With the
    # realistic cost the gate becomes nearly inert (threshold 1.5*0.2% = 0.3%),
    # which is the intended outcome: the bleed is NOT primarily fee-driven.
    #
    # RISK_ROUND_TRIP_FEE_PCT is DEPRECATED: it survives only as a thin Operator
    # override. If unset, the CostModel-derived value wins. Do NOT re-pin it to
    # 1.2 — that would re-introduce the drift this sprint removed.
    # env: RISK_MIN_SL_COST_MULTIPLE, RISK_ROUND_TRIP_FEE_PCT (override only).
    # OPERATOR-SIGN-OFF PARAMETER: min_sl_cost_multiple 1.5 => min SL ~0.3%.
    min_sl_cost_multiple: float = Field(default=1.5, ge=0.0)
    round_trip_fee_pct: float = Field(
        default_factory=lambda: _cost_model_paper_round_trip_pct(),
        gt=0.0,
    )

    # NEO-V2 (2026-06-01): per-symbol post-stop cooldown. After a stop-out the
    # same symbol may not be re-entered for this window (minutes). Source for the
    # last stop is the paper-execution audit (position_closed reason=stop) — no
    # new persistence. env: RISK_POST_STOP_COOLDOWN_MIN.
    # OPERATOR-SIGN-OFF PARAMETER: 180 (3h) recommended. 0 disables.
    post_stop_cooldown_min: int = Field(default=180, ge=0)

    # Sprint E (Goal 2026-06-01 §5): churn-killer. Generalises the post-stop
    # cooldown into a full re-entry throttle, driven entirely from the existing
    # paper-execution audit (no new persistence). Only risk-INCREASING entries
    # are gated — exits/SL/TP/reductions are never blocked (hard invariant,
    # enforced by wiring the gate only into the entry path). All sub-gates fail
    # OPEN and each `<= 0` value disables its own sub-gate.
    #
    # Real-data motivation: MATIC 4.25 re-entries/day, LINK 3.0, ETH 2.71 — the
    # same loser re-entered minute-by-minute.
    #
    # OPERATOR-SIGN-OFF PARAMETERS (env: RISK_CHURN_*). Defaults are conservative
    # (block obvious churn, do not throttle a healthy book):
    #
    # churn_cooldown_min — per-symbol min wait after ANY risk-reducing close
    #   (stop/take/reversal), not only stop. Default 60 (1h). This is the §1/§2
    #   base window. Sensitivity: too high starves legit re-entries on trending
    #   names; too low re-opens the churn door. 0 disables (post_stop_cooldown_min
    #   then remains the only cooldown).
    churn_cooldown_min: int = Field(default=60, ge=0)
    # churn_loss_streak_threshold — N consecutive losing closes of a symbol that
    #   trigger the backoff. Default 3 (matches observed MATIC/LINK loss runs).
    #   Sensitivity: 2 is aggressive (one bad pair of trades extends the lockout),
    #   4+ rarely fires. 0 disables the backoff.
    churn_loss_streak_threshold: int = Field(default=3, ge=0)
    # churn_loss_streak_multiplier — window stretch once the streak threshold is
    #   hit. Default 2.0 (3 losses -> 2h lockout at the 60-min base). Sensitivity:
    #   linear on the lockout duration; <=1.0 makes the backoff inert.
    churn_loss_streak_multiplier: float = Field(default=2.0, ge=1.0)
    # churn_max_trades_per_symbol_per_hour — hard cap on ENTRY fills per symbol
    #   per rolling hour. Default 2. Observed churn was 3-4/day per symbol but
    #   clustered; 2/hour blocks the minute-by-minute pattern while leaving room
    #   for a legitimate scale-in. Sensitivity: 1 is very tight (no averaging-in
    #   ever), 3+ permits the observed churn. 0 disables.
    churn_max_trades_per_symbol_per_hour: int = Field(default=2, ge=0)
    # churn_max_notional_turnover_per_hour — global cap on summed ENTRY notional
    #   (USD) across all symbols per rolling hour. Default 0.0 (DISABLED) because
    #   the right value is equity-dependent and must be chosen by the operator;
    #   a wrong global cap can silently starve the whole book. Recommended start
    #   when enabled: ~1.5x initial_equity (e.g. 15000 at 10k equity), i.e. allow
    #   ~1.5 full-book turnovers/hour. Sensitivity: this is the bluntest gate —
    #   it blocks ALL new entries regardless of symbol once tripped. Treat as an
    #   emergency brake, not a routine throttle. 0.0 disables.
    churn_max_notional_turnover_per_hour: float = Field(default=0.0, ge=0.0)
    # churn_probe_trades_per_hour — tighter per-symbol entry cap that applies when
    #   entry_mode is PROBE (Goal Sprint A throttle hook). Default 1. Only used
    #   when > 0 AND the active entry_mode is PROBE; otherwise the normal
    #   churn_max_trades_per_symbol_per_hour applies. 0 = no PROBE-specific
    #   tightening (fall back to the normal cap).
    churn_probe_trades_per_hour: int = Field(default=1, ge=0)

    # Sprint 2026-06-02 — reward/risk + risk-budget gates (Gate 10 in
    # app/risk/engine.py). Root cause: a premium channel signal (US/USDT, 10x,
    # stop 4.2% / leveraged risk 42%, T1 reward/risk ~0.11) was only ever stopped
    # by the max_open_positions cap — there was NO reward/risk or per-signal risk
    # ceiling. These gates close that gap.
    #
    # ALL default to the DISABLED sentinel (<= 0 / None). Turning them on changes
    # productive gating globally and — like the diversification cap incident —
    # a wrong threshold can starve the book. They are therefore OPERATOR-SIGN-OFF
    # parameters, default-OFF, with recommended values documented in .env.example.
    # Evaluation is fail-closed: an ENABLED gate with missing geometry rejects.
    #
    # Recommended starting values once signed off (env RISK_*):
    #   RISK_MIN_RR=0.5  RISK_MIN_AVG_RR=0.8  RISK_MAX_SIGNAL_RISK_PCT=8.0
    #   RISK_MAX_LEVERAGED_RISK_PCT=35.0  RISK_MIN_NET_EDGE_BPS=0.0
    #   RISK_MIN_TARGET_DISTANCE_PCT=0.3
    min_rr: float = Field(default=0.0, ge=0.0)
    min_avg_rr: float = Field(default=0.0, ge=0.0)
    # max_signal_risk_pct: UN-leveraged stop distance |entry-SL|/entry*100.
    max_signal_risk_pct: float = Field(default=0.0, ge=0.0)
    # max_leveraged_risk_pct: DENOMINATOR-SAFE definition = stop_distance_pct *
    # leverage (the signal-geometry "Risk 42%" a 10x channel reports). This is
    # NOT account-equity-at-risk — do not interpret 35 as 35% of equity.
    max_leveraged_risk_pct: float = Field(default=0.0, ge=0.0)
    min_net_edge_bps: float | None = Field(default=None)
    min_target_distance_pct: float = Field(default=0.0, ge=0.0)
    # Staged rollout for the reward/risk gates. off|audit|enforce. Default
    # "audit": a set threshold is OBSERVED (would_reject + risk_gate_audit.jsonl)
    # before it can ever block — the safe path against silent book-starvation.
    # env: RISK_GATES_MODE.
    gates_mode: str = Field(default="audit")

    @field_validator("gates_mode")
    @classmethod
    def _validate_gates_mode(cls, v: str) -> str:
        norm = (v or "").strip().lower()
        if norm not in {"off", "audit", "enforce"}:
            raise ValueError("RISK_GATES_MODE must be one of: off, audit, enforce")
        return norm


class ExecutionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXECUTION_", env_file=".env", extra="ignore")

    # Safety: always paper by default
    mode: ExecutionMode = Field(default=ExecutionMode.PAPER)
    live_enabled: bool = Field(default=False)
    dry_run: bool = Field(default=True)
    approval_required: bool = Field(default=True)

    # Entry-Safety-Mode (Goal 2026-06-01). Governs whether the autonomous
    # TradingLoop may OPEN new positions; exits/risk-reductions are never gated.
    # NOTE: the goal spec names this `trading.entry_mode`; it lives under
    # `execution.` (env EXECUTION_ENTRY_MODE) because every paper-execution gate
    # already lives here — avoids a one-field top-level group and config drift.
    # Default PAPER preserves legacy loop behavior (never live_normal). The Pi
    # is set to DISABLED until the cost-adjusted edge gate is passed.
    entry_mode: EntryMode = Field(default=EntryMode.PAPER)

    # Phase-B Shadow-Candidate-Ledger (env EXECUTION_SHADOW_DIAGNOSTICS). When
    # entry_mode=disabled, the autonomous loop normally short-circuits at the
    # entry-mode gate before any analysis. With this flag ON, it instead runs the
    # READ-ONLY pipeline (market-data + signal-gen + ATR geometry), records a
    # hypothetical shadow candidate (no fill/position/order), then returns
    # ENTRY_MODE_BLOCKED. Default OFF preserves the cheap early-return. Only has
    # an effect while entry_mode=disabled (no effect when entries are allowed —
    # those already produce real audit + outcomes).
    shadow_diagnostics: bool = Field(default=False)

    # NEO-P-002-r3 (env EXECUTION_SHADOW_REAL_GENERATOR). Fail-safe Default OFF.
    # When ON, the shadow-real feed driver replays REAL analyzed documents
    # (DocumentRepository.get_recent_analyzed) through the existing
    # run_trading_loop_once(analysis_result=...) seam so the real SignalGenerator
    # produces source=autonomous_generator shadow candidates — read-only, no
    # execution (the loop's entry_mode-disabled shadow path is unchanged). OFF
    # preserves status quo (only the loop_control_* canary feeds the shadow path).
    shadow_real_generator: bool = Field(default=False)

    # Paper trading
    paper_initial_equity: float = Field(default=10000.0)
    paper_fee_pct: float = Field(default=0.1)  # 0.1% fee
    paper_slippage_pct: float = Field(default=0.05)  # 0.05% slippage

    # Priority-Tier-Gate (D-182): only fill paper cycles when the underlying
    # AnalysisResult.recommended_priority is >= this threshold. Default 1
    # preserves pre-gate behavior (every priority passes). Set to 10 to
    # restrict paper execution to the high-conviction tier (D-149 evidence:
    # P>=10 hit-rate 72.73% on n=55, CI95 [59.77, 82.72] vs P7-P9 29.03%
    # on n=186). Analyses with priority=None are blocked when threshold>1.
    paper_min_priority: int = Field(default=1, ge=1, le=10)

    # Order parameters
    order_ttl_seconds: int = Field(default=300)
    max_order_retries: int = Field(default=3)
    execution_timeout_seconds: int = Field(default=30)

    # Position-monitor scheduler — periodic SL/TP check on open paper
    # positions.  Without this the loop only opens positions and never
    # closes them, leaving realized_pnl at 0.0.
    position_monitor_enabled: bool = Field(default=True)
    position_monitor_interval_seconds: int = Field(default=60, ge=10)

    # Operator-Signal-Bridge: turns accepted signal envelopes (from dashboard
    # paste or telegram-bot handoff) into real paper-engine fills, honoring
    # the operator's entry/SL/TP 1:1. Fail-closed: disabled by default.
    operator_signal_bridge_enabled: bool = Field(default=False)
    operator_signal_source_allowlist: str = Field(default="dashboard")  # CSV
    operator_signal_ttl_hours: int = Field(default=24, ge=1, le=168)
    operator_signal_entry_tolerance_pct: float = Field(default=0.5, ge=0.0, le=5.0)

    # Approval-Mode (Vorschlag B, B-6): per-signal manual Fill/Ignore via Telegram
    # buttons. Fail-closed: disabled by default. When enabled, parsed signals
    # from auto-ingest workers (e.g. telegram_channel) are NOT auto-routed to
    # the bridge — instead a new envelope is re-emitted with source
    # `<orig>_approved` only after the operator clicks [Fill].
    operator_signal_approval_enabled: bool = Field(default=False)
    operator_signal_approval_ttl_minutes: int = Field(default=60, ge=1, le=1440)
    # 2026-05-14 P1 #9: HMAC secret for callback_data signing. Default ""
    # → legacy unsigned tokens (migration runway). Set to 32+ random bytes
    # (base64 ok) to enable strict-mode: only signed callback_data with
    # valid HMAC + non-expired TTL is accepted. Pre-existing in-flight
    # buttons become invalid the moment strict-mode flips on — that's
    # the security-target, NOT a bug.
    operator_signal_approval_hmac_secret: str = Field(default="", repr=False)

    # Premium-Auto-Fill (2026-05-12 Sprint B per Operator-Auftrag Sektion 4):
    # Wenn aktiviert, schreibt der Worker nach jedem accepted Premium-Signal
    # SOFORT einen auto-approved Envelope (source-Suffix `_approved`,
    # approved_by="auto-fill"). Damit greift der etablierte Approval→Bridge-
    # Pfad ohne dass der Operator klicken muss. Operator-Klick bleibt als
    # manueller Override möglich, ist aber nicht mehr Voraussetzung.
    #
    # Sicherheitsleitplanken (paper-mode-only):
    # - Risk-Gates der Bridge greifen unverändert (kill_switch, max_positions,
    #   daily_loss, sizing). Ein vollgelaufenes max_open_positions blockt
    #   Auto-Fill genauso wie manuelles Fill.
    # - Operator-Auftrag-Sektion 14 Fail-Closed gilt: ein nicht parsbares
    #   Signal landet nicht als Envelope, also auch nicht als Auto-Fill.
    # - Approval-Audit-Trail wird weiterhin geschrieben (approved_by="auto-fill"
    #   ist sichtbar, idempotency_key prevents double-emit).
    # - Live-Mode blockt sich durch eigene Phase-0-Gates (HOTP, server-SL).
    #   Auto-Fill ist explizit eine Paper-Lockerung — nie für live aktivieren.
    # ADR: docs/adr/0004-premium-signal-auto-fill.md
    operator_signal_premium_auto_fill_enabled: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_mode_guardrails(self) -> "ExecutionSettings":
        if self.live_enabled and self.mode is not ExecutionMode.LIVE:
            raise ValueError("EXECUTION_LIVE_ENABLED=true requires EXECUTION_MODE=live.")
        if self.mode is ExecutionMode.LIVE:
            if not self.live_enabled:
                raise ValueError("EXECUTION_MODE=live requires EXECUTION_LIVE_ENABLED=true.")
            if self.dry_run:
                raise ValueError("EXECUTION_MODE=live requires EXECUTION_DRY_RUN=false.")
            if not self.approval_required:
                raise ValueError("EXECUTION_MODE=live requires EXECUTION_APPROVAL_REQUIRED=true.")
            if not self.operator_signal_approval_hmac_secret.strip():
                raise ValueError(
                    "EXECUTION_MODE=live requires EXECUTION_OPERATOR_SIGNAL_APPROVAL_HMAC_SECRET."
                )
        # Entry-Safety-Mode consistency: a live entry mode must not be configured
        # while the execution venue is still paper/non-live. Fail-closed so a
        # stray EXECUTION_ENTRY_MODE=live_* cannot silently imply live trading.
        if self.entry_mode.is_live and self.mode is not ExecutionMode.LIVE:
            raise ValueError(
                f"EXECUTION_ENTRY_MODE={self.entry_mode.value} requires EXECUTION_MODE=live "
                "(live entry cadence cannot run on a non-live execution venue)."
            )
        return self


class PremiumSettings(BaseSettings):
    """Premium Telegram execution policy.

    These flags describe the premium pipeline's intended paper/live posture.
    They do not bypass ``execution.entry_mode``; a disabled entry mode remains a
    global safety stop and the bridge reports it as ENTRY_DISABLED.

    Defense-in-depth: ``paper_execution_enabled`` defaults to False so premium
    paper-fills require an explicit opt-in (``PREMIUM_PAPER_EXECUTION_ENABLED=true``)
    *in addition to* entry_mode allowing entries — flipping entry_mode on must not
    silently auto-enrol the premium channel.
    """

    model_config = SettingsConfigDict(env_prefix="PREMIUM_", env_file=".env", extra="ignore")

    paper_execution_enabled: bool = Field(default=False)
    live_execution_enabled: bool = Field(default=False)
    require_manual_approval_for_live: bool = Field(default=True)
    require_manual_approval_for_paper: bool = Field(default=False)

    # Live triple-flag arming token (Goal 2026-06-05 Premium-Fastlane §4). The
    # premium-fastlane LIVE path stays hard-blocked unless ALL THREE hold:
    #   premium_fastlane.live_enabled=True
    #   premium.live_execution_enabled=True
    #   premium.live_canary_explicit_ack == LIVE_CANARY_ACK_SENTINEL
    # The sentinel is an explicit human acknowledgement of real-capital risk;
    # an empty/incorrect value keeps live execution refused. Default empty →
    # live can never auto-arm from a flag flip alone.
    live_canary_explicit_ack: str = Field(default="", repr=False)


# Explicit human-typed acknowledgement required to arm premium-fastlane LIVE.
# Kept as a module constant so tests + the runtime gate share one source of
# truth and a typo cannot silently weaken the guard.
LIVE_CANARY_ACK_SENTINEL = "I_UNDERSTAND_REAL_CAPITAL_RISK"


class PremiumFastlaneSettings(BaseSettings):
    """30-day Premium-Telegram Fastlane (Goal 2026-06-05).

    Purpose: during a controlled 30-day test window, authentic premium-channel
    signals are routed *immediately* into PAPER/TESTNET/DEMO execution so real
    forward-data is generated — instead of being killed pre-trade by quality,
    priority, forward-precision, approval, source-allowlist or the global
    ``entry_mode=disabled`` kill-switch.

    Hard invariants (never relaxed by this block):
    - LIVE stays protected by the triple-flag gate (see PremiumSettings).
    - Minimum guards always apply: schema-valid, entry/SL/targets/side/symbol
      present, duplicate suppression, quantity>0, notional in [min,max],
      SL/TP geometry, resolvable scale (else requires_scale_review).
    - The bypasses below ONLY apply to authentic premium-telegram sources in a
      non-live route. The classic bridge path for every other source is
      unchanged.

    Fail-closed: ``enabled`` defaults to **False**. The runtime (.env / Pi)
    opts in explicitly via ``PREMIUM_FASTLANE_ENABLED=true``.

    Fail-closed bypasses (Issue #181, 2026-06-08): every gate bypass below now
    defaults to **False**. Enabling the fastlane no longer auto-relaxes any gate;
    each relaxation is an explicit per-bypass opt-in. In particular the dangerous
    ``bypass_entry_mode_for_paper`` no longer silently neuters the global
    ``EXECUTION_ENTRY_MODE=disabled`` kill-switch (#179 incident) — under
    ``disabled`` it is honoured ONLY together with the explicit second
    acknowledgement ``allow_entry_mode_disabled_override`` (a two-flag arm that
    mirrors the live triple-flag pattern; see ``fastlane_entry_mode_override``
    in app/execution/premium_fastlane.py).
    """

    model_config = SettingsConfigDict(
        env_prefix="PREMIUM_FASTLANE_", env_file=".env", extra="ignore"
    )

    enabled: bool = Field(default=False)
    duration_days: int = Field(default=30, ge=1, le=365)
    # ISO-8601 start date. Empty → treat the window as open-ended-from-now
    # (active as long as ``enabled``); set it to pin a concrete 30-day end.
    start_date: str = Field(default="")
    mode: str = Field(default="paper_testnet_demo")

    # LIVE arming (one of the three required flags; see PremiumSettings).
    live_enabled: bool = Field(default=False)

    # ── Gate bypasses (only ever applied to authentic premium / non-live) ──
    # ALL default False (fail-closed, Issue #181). Each is an explicit per-bypass
    # opt-in; enabling the fastlane alone relaxes nothing.
    bypass_manual_approval: bool = Field(default=False)
    bypass_source_allowlist: bool = Field(default=False)
    bypass_entry_mode_for_paper: bool = Field(default=False)
    bypass_risk_quality_gates: bool = Field(default=False)
    bypass_source_quality_gates: bool = Field(default=False)
    bypass_priority_tier_gates: bool = Field(default=False)
    bypass_forward_precision_gates: bool = Field(default=False)

    # ── Explicit entry-mode override (Issue #181 §7) ──
    # Second, independent acknowledgement required before the fastlane may
    # downgrade a GLOBAL ``EXECUTION_ENTRY_MODE=disabled`` to an observed note for
    # the premium paper route. Even with ``bypass_entry_mode_for_paper=True`` the
    # kill-switch stays in force unless this is ALSO explicitly armed. Default
    # False → ``disabled`` means disabled for the premium path too.
    allow_entry_mode_disabled_override: bool = Field(default=False)

    # ── Minimum required guards (never bypassed) ──
    require_schema_valid: bool = Field(default=True)
    require_entry: bool = Field(default=True)
    require_sl: bool = Field(default=True)
    require_targets: bool = Field(default=True)
    require_leverage: bool = Field(default=True)

    # ── Leverage policy ──
    default_leverage: float = Field(default=10.0, gt=0.0)
    max_leverage: float = Field(default=10.0, gt=0.0)

    # ── Notional / sizing policy (USDT) ──
    default_notional_usdt: float = Field(default=100.0, gt=0.0)
    min_notional_usdt: float = Field(default=10.0, gt=0.0)
    max_notional_usdt: float = Field(default=250.0, gt=0.0)
    max_open_positions: int = Field(default=50, ge=1)
    max_per_symbol_open_positions: int = Field(default=1, ge=1)
    paper_equity_usdt: float = Field(default=10000.0, gt=0.0)

    # ── Backfill policy (Goal 2026-06-05 §8) ──
    # A post-deploy backfill may re-create a retrospective paper/pending record
    # for a premium signal that has aged past the live TTL. Default True for the
    # 30-day window so missed signals can be reprocessed; live is never affected
    # (the bridge is paper). Set False to make the backfill honour the live TTL.
    backfill_ignore_ttl_for_paper: bool = Field(default=True)

    # ── Order / bracket policy ──
    duplicate_window_minutes: int = Field(default=180, ge=1)
    order_mode: str = Field(default="bracket_limit")
    attach_sl_tp: bool = Field(default=True)
    use_reduce_only_tps: bool = Field(default=True)
    use_oco_if_available: bool = Field(default=True)
    fallback_to_local_tp_sl_monitor: bool = Field(default=True)

    # ── Routing ──
    routing_priority: str = Field(default="paper,testnet,demo,simulated_exchange")
    simulated_exchange_fallback: bool = Field(default=True)
    allowed_exchanges: str = Field(default="bybit,okx,binance_futures,bitget,kucoin,huobi,bingx")

    @property
    def routing_priority_list(self) -> list[str]:
        return [x.strip().lower() for x in self.routing_priority.split(",") if x.strip()]

    @property
    def allowed_exchange_list(self) -> list[str]:
        return [x.strip().lower() for x in self.allowed_exchanges.split(",") if x.strip()]

    @property
    def is_live_route_allowed(self) -> bool:
        """``live_enabled`` is necessary but NOT sufficient — the full triple
        flag is checked at the call-site against PremiumSettings."""
        return self.live_enabled

    @model_validator(mode="after")
    def _clamp_notional_bounds(self) -> "PremiumFastlaneSettings":
        if self.min_notional_usdt > self.max_notional_usdt:
            raise ValueError(
                "PREMIUM_FASTLANE_MIN_NOTIONAL_USDT must be <= PREMIUM_FASTLANE_MAX_NOTIONAL_USDT."
            )
        if self.default_leverage > self.max_leverage:
            # A default above the cap would always clamp — surface the misconfig.
            raise ValueError(
                "PREMIUM_FASTLANE_DEFAULT_LEVERAGE must be <= PREMIUM_FASTLANE_MAX_LEVERAGE."
            )
        return self


class OperatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPERATOR_", env_file=".env", extra="ignore")

    telegram_polling_enabled: bool = Field(default=False)
    telegram_dry_run: bool = Field(default=True)
    telegram_poll_interval_seconds: float = Field(default=1.0, gt=0.0)
    telegram_long_poll_timeout_seconds: int = Field(default=20, ge=1)
    telegram_bot_token: str = Field(default="", repr=False)
    admin_chat_ids: str = Field(default="")  # Comma-separated chat IDs
    command_audit_log: str = Field(default="artifacts/operator_commands.jsonl")
    signal_handoff_log: str = Field(default="artifacts/telegram_signal_handoff.jsonl")
    signal_exchange_outbox_log: str = Field(default="artifacts/telegram_exchange_outbox.jsonl")
    signal_append_decision_enabled: bool = Field(default=False)
    signal_auto_run_enabled: bool = Field(default=False)
    signal_auto_run_mode: str = Field(default="paper")
    signal_auto_run_provider: str = Field(default="coingecko")
    signal_forward_to_exchange_enabled: bool = Field(default=False)
    signal_exchange_relay_endpoint: str = Field(default="")
    signal_exchange_relay_api_key: str = Field(default="", repr=False)
    signal_exchange_relay_timeout_seconds: int = Field(default=10, ge=1)
    signal_exchange_relay_max_attempts: int = Field(default=3, ge=1)
    signal_exchange_sent_log: str = Field(default="artifacts/telegram_exchange_sent.jsonl")
    signal_exchange_dead_letter_log: str = Field(
        default="artifacts/telegram_exchange_dead_letter.jsonl"
    )
    telegram_dashboard_url: str = Field(default="")

    @property
    def admin_chat_id_list(self) -> list[int]:
        if not self.admin_chat_ids:
            return []
        return [int(x.strip()) for x in self.admin_chat_ids.split(",") if x.strip()]

    _strip_secrets = field_validator(
        "telegram_bot_token", "signal_exchange_relay_api_key", mode="before"
    )(_strip_secret)

    @model_validator(mode="after")
    def validate_signal_handoff_mode(self) -> "OperatorSettings":
        normalized_mode = self.signal_auto_run_mode.strip().lower()
        if normalized_mode not in {"paper", "shadow"}:
            raise ValueError("OPERATOR_SIGNAL_AUTO_RUN_MODE must be one of: paper, shadow.")
        self.signal_auto_run_mode = normalized_mode
        return self


class ExchangeSettings(BaseSettings):
    """Exchange adapter configuration.

    Set API keys via .env:
        EXCHANGE_BINANCE_API_KEY=...
        EXCHANGE_BINANCE_SECRET=...
        EXCHANGE_BYBIT_API_KEY=...
        EXCHANGE_BYBIT_SECRET=...

    Safety defaults: dry_run=True, testnet=True.
    """

    model_config = SettingsConfigDict(env_prefix="EXCHANGE_", env_file=".env", extra="ignore")

    # Global flags
    dry_run: bool = Field(default=True)
    testnet: bool = Field(default=True)
    default_exchange: str = Field(default="binance")  # binance | bybit
    whitelist: list[str] = Field(default_factory=list)  # allowed symbols

    # Binance
    binance_api_key: str = Field(default="", repr=False)
    binance_secret: str = Field(default="", repr=False)

    # Bybit
    bybit_api_key: str = Field(default="", repr=False)
    bybit_secret: str = Field(default="", repr=False)
    bybit_category: str = Field(default="spot")  # spot | linear | inverse

    # Timeouts
    timeout_seconds: float = Field(default=15.0, gt=0.0)

    _strip_secrets = field_validator(
        "binance_api_key", "binance_secret", "bybit_api_key", "bybit_secret", mode="before"
    )(_strip_secret)


class TradingViewSettings(BaseSettings):
    """TradingView integration settings — TV-1 webhook ingest only.

    All defaults fail-closed: webhook is unmounted (404) unless both
    enabled=true AND a non-empty secret are configured.
    """

    model_config = SettingsConfigDict(env_prefix="TRADINGVIEW_", env_file=".env", extra="ignore")

    webhook_enabled: bool = Field(default=False)
    webhook_secret: str = Field(default="", repr=False)
    webhook_audit_log: str = Field(default="artifacts/tradingview_webhook_audit.jsonl")
    webhook_replay_cache_size: int = Field(default=2048, ge=1)
    webhook_replay_window_seconds: float = Field(default=300.0, gt=0.0)
    # V8.1 (SAT-C-V8-001): second replay layer keyed on an operator-provided
    # `event_id` field in the alert body. Pass-through when the payload has no
    # event_id — bytes-layer cache remains the sole guard in that case.
    webhook_event_id_cache_size: int = Field(default=4096, ge=1)
    webhook_event_id_window_seconds: float = Field(default=1800.0, gt=0.0)
    # D-189 / NEO-F-META-20260424-026: SQLite-persist the payload-hash and
    # event-id replay caches so they survive uvicorn/systemd restarts. The
    # in-memory LRU keeps the fast path; SQLite hydrates on startup, so a
    # restart within the replay window no longer reopens the replay door.
    # Default off — operator opts in after setting webhook_replay_cache_db_path
    # somewhere writable (artifacts/ is fine on Windows AND Pi).
    webhook_replay_cache_persistent: bool = Field(default=False)
    webhook_replay_cache_db_path: str = Field(default="artifacts/tradingview_replay_cache.db")
    # D-193 / NEO-F-META-20260424-023: brute-force guard on the webhook
    # auth pipeline (HMAC + shared-token). Independent bucket from the
    # API-Key rate-limiter in auth.py so webhook bursts don't affect the
    # operator dashboard and vice versa. Threshold=0 disables the guard.
    webhook_rate_limit_threshold: int = Field(default=10, ge=0)
    webhook_rate_limit_window_seconds: float = Field(default=300.0, gt=0.0)
    # TV-2.1: shared-token fallback for TradingView's native webhook which
    # cannot produce body-HMACs. Modes: hmac (default, strongest) |
    # shared_token (no body integrity, deprecated) |
    # hmac_or_token (accept either, deprecated) |
    # hmac_strict_event_id (V8-f: shared_token mandates body event_id + ts).
    webhook_auth_mode: str = Field(default="hmac")
    webhook_shared_token: str = Field(default="", repr=False)
    # V8-f kill-switch: when true, shared_token / hmac_or_token / strict are
    # all rejected at the door. Lets the operator hard-disable token-auth
    # without rotating the env or reaching for ENABLED=false.
    webhook_shared_token_disabled: bool = Field(default=False)
    # V8-f strict mode: maximum allowed clock skew (seconds) between body ts
    # and server now. Outside the window -> rejected as clock_skew.
    webhook_strict_ts_skew_seconds: int = Field(default=300, ge=30, le=3600)
    # TV-3: when true, accepted payloads are normalized to a
    # TradingViewSignalEvent and appended to the pending-signals JSONL.
    # Default false (fail-closed). No auto-execution — events wait for
    # operator approval. Normalizer failures leave audit intact.
    webhook_signal_routing_enabled: bool = Field(default=False)
    webhook_pending_signals_log: str = Field(default="artifacts/tradingview_pending_signals.jsonl")
    # TV-3.1: append-only operator decision log (promote / reject) and
    # promoted-candidate sink. Re-deciding an event is rejected by the CLI.
    pending_decisions_log: str = Field(default="artifacts/tradingview_pending_decisions.jsonl")
    promoted_signals_log: str = Field(default="artifacts/tradingview_promoted_signals.jsonl")
    # TV-4 prep: measurement-only consumer. When disabled (default),
    # the consumer is a no-op — no file is written, no state changes.
    # When enabled, each promoted row is appended once (by decision_id)
    # to the signal-audit JSONL. No trading-loop side effects.
    promoted_consumer_enabled: bool = Field(default=False)
    promoted_signal_audit_log: str = Field(default="artifacts/tradingview_signal_audit.jsonl")
    # D-156c: periodic bridge from pending TV events into alert_audit so
    # the auto-annotator can score them for the TV-4 Quality-Bar. Default
    # off — operator opts in explicitly once the bridge is trusted.
    bridge_scheduler_enabled: bool = Field(default=False)
    bridge_scheduler_interval_seconds: int = Field(default=300, ge=30)
    bridge_scheduler_include_smoke: bool = Field(default=False)
    # SENTR-F-004: HMAC tamper-detection on tradingview_pending_signals.jsonl.
    # When set, the router signs each appended row and the bridge verifies
    # the signature before promoting the event into alert_audit.jsonl.
    # Empty = feature disabled (legacy single-trust-boundary mode).
    # Rows without _sig are counted as skipped_unsigned when the secret is
    # active — tampered (bad _sig) rows are counted as skipped_tampered.
    bridge_hmac_secret: str = Field(default="", repr=False)

    _strip_secrets = field_validator(
        "webhook_secret", "webhook_shared_token", "bridge_hmac_secret", mode="before"
    )(_strip_secret)

    @model_validator(mode="after")
    def validate_auth_mode(self) -> "TradingViewSettings":
        normalized = self.webhook_auth_mode.strip().lower()
        token_modes = {"shared_token", "hmac_or_token", "hmac_strict_event_id"}
        if normalized not in {"hmac"} | token_modes:
            raise ValueError(
                "TRADINGVIEW_WEBHOOK_AUTH_MODE must be one of "
                "hmac, shared_token, hmac_or_token, hmac_strict_event_id."
            )
        if normalized in token_modes and not self.webhook_shared_token:
            raise ValueError(
                "TRADINGVIEW_WEBHOOK_SHARED_TOKEN must be set when "
                "TRADINGVIEW_WEBHOOK_AUTH_MODE uses a token-based mode."
            )
        self.webhook_auth_mode = normalized
        return self


class BinanceMarketDataSettings(BaseSettings):
    """TV-2 OHLCV adapter — Binance public REST (no auth).

    Gated by BINANCE_ENABLED. Used only as a supplementary market-data
    provider when explicitly enabled; CoinGecko remains the default.
    """

    model_config = SettingsConfigDict(env_prefix="BINANCE_", env_file=".env", extra="ignore")

    enabled: bool = Field(default=False)
    base_url: str = Field(default="https://api.binance.com")
    timeout_seconds: int = Field(default=10, ge=1)
    max_retries: int = Field(default=3, ge=1)
    freshness_threshold_seconds: float = Field(default=120.0, gt=0.0)


class TelegramChannelIngestSettings(BaseSettings):
    """Vorschlag B — premium-channel MTProto auto-ingest (Telethon).

    Fail-closed: disabled by default. When enabled, the worker connects
    via MTProto, resolves the target channel by title or explicit chat_id,
    subscribes to new messages, and emits parsed signals as envelope-JSONL
    records. No execution happens unless the bridge allowlist explicitly
    includes ``telegram_premium_channel`` (see B-5).
    """

    model_config = SettingsConfigDict(
        env_prefix="INGESTION_TELEGRAM_CHANNEL_", env_file=".env", extra="ignore"
    )

    enabled: bool = Field(default=False)
    # api_id/api_hash from https://my.telegram.org/apps. Required once to
    # create the session file; afterwards the session stores the auth.
    api_id: int = Field(default=0)
    api_hash: str = Field(default="", repr=False)
    # Path to the Telethon session file (persists auth state across runs).
    session_path: str = Field(default="artifacts/telegram_channel.session")
    # Resolution: prefer explicit chat_id when known, else match by title.
    # The premium channel has no @handle — title-match is the fallback.
    target_chat_id: int = Field(default=0)
    target_title: str = Field(default="")
    # 2026-05-14 (P0 #6): das alte `dry_run` Feld wurde ersatzlos entfernt
    # weil es im Worker NIE abgefragt wurde — ein Schein-Schalter (Operator
    # konnte ihn auf =true setzen ohne dass irgendwas geschah). Schein-Flags
    # kosten Operator-Vertrauen. Re-implementierung ist 30min wenn ein
    # konkreter Diagnose-Trigger auftaucht; aktuell fehlt der Trigger.
    # Source-tag written into every emitted envelope. Must match the value
    # added to EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST in B-5.
    source_tag: str = Field(default="telegram_premium_channel")
    # Diagnostic log for observed channel messages (parsed + unparsed both).
    raw_log_path: str = Field(default="artifacts/telegram_channel_raw.jsonl")
    # Checkpoint file: persists last_message_id per chat across restarts so
    # the worker can detect and replay messages missed during downtime.
    # Complements Telethon's catch_up=True (which only handles updates
    # delivered while the session was online and reconnected within
    # Telegram's update-state retention window — typically a few hours).
    checkpoint_path: str = Field(default="artifacts/telegram_channel_checkpoint.json")
    # D-191 / S-003: Liveness heartbeat. The worker touches this file at
    # startup and every ~60 s while the run-loop is alive — independent of
    # whether the channel actually emits messages. canonical_read uses it
    # as a third candidate next to the PID file and the Telethon session
    # file. ``heartbeat_stale_seconds`` is the threshold the watchdog uses
    # for the heartbeat-only sub-status.
    heartbeat_path: str = Field(default="artifacts/telegram_listener_heartbeat")
    heartbeat_stale_seconds: int = Field(default=1800, ge=1)
    # F4 (2026-05-05): opt-in diagnostic observer. When True, the worker
    # registers a SECOND NewMessage handler WITHOUT the chats=entity filter
    # to log chat_id + msg_id of every message Telethon delivers. Lets the
    # operator verify whether updates reach the process at all (Hypothesis B/D
    # from V19) versus the entity-filter dropping them. Strictly diagnostic:
    # never calls _record_message_observed (would inflate the F3 reactivity
    # counter) and never logs message text (PII / unrelated channels). Logged
    # at DEBUG level — set INGESTION_TELEGRAM_CHANNEL_VERBOSE_OBSERVER=true
    # AND a logger config that surfaces DEBUG to actually see the output.
    # Intended for 24-48 h diagnostic windows, not production-default.
    verbose_observer: bool = Field(default=False)
    # 2026-05-31 (poll-backstop): active poll interval (seconds).
    # run_until_disconnected is push-only; Telethon can silently stop
    # delivering updates without raising (heartbeat keeps ticking →
    # process looks alive while the channel goes dark — this lost a
    # NIGHT/USDT premium signal on 2026-05-31). The worker polls the
    # channel via the checkpoint+replay path every N seconds so a dead
    # push stream can no longer cause silent signal loss. Idempotent
    # (emit dedups). 0 disables (push-only legacy). Default 90 s —
    # cheap (one iter_messages RPC) and recovers a missed signal within
    # ~1.5 min.
    poll_backstop_seconds: int = Field(default=90, ge=0)


class LearningSettings(BaseSettings):
    """Adaptive-Learning Pipeline configuration.

    Contract:
      - ``adaptive_learning_enabled`` is the master gate. False (default) ⇒
        ``build_bayes_signal_kwargs`` injects no Active*-Loaders, no
        ReasoningJournal — SignalGenerator runs in raw-Bayes mode like
        before. The full Approval/Snapshot pipeline still works (operator
        can write/diff snapshots), but the trading loop stays unchanged.
      - True ⇒ Loaders read the YAML snapshots in ``snapshot_dir`` and
        emit reasoning steps to ``reasoning_journal_path``. Snapshot
        missing ⇒ Identity-Loader (still no behavior change at runtime),
        snapshot present ⇒ active calibrator/threshold applied.

    The opt-in flag exists so a fresh boot of the trading loop is always
    behavior-preserving — operator must consciously flip the switch
    after a calibration approval has actually been signed off.
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_LEARNING_",
        env_file=".env",
        extra="ignore",
    )

    adaptive_learning_enabled: bool = Field(default=False)
    snapshot_dir: Path = Field(default=Path("config/learning"))
    reasoning_journal_path: Path = Field(default=Path("artifacts/structured_reasoning.jsonl"))


class DiversificationSettings(BaseSettings):
    """Asset-diversification / concentration guard configuration.

    Default-off, shadow-first — mirrors the Bayes/regime rollout discipline.

      - ``enabled=False`` (default): the guard is not consulted anywhere; the
        trading loop behaves exactly as before. The read-only report/CLI/API
        surfaces still work (they build the universe on demand).
      - ``enabled=True`` + ``shadow_only=True``: the loop *stamps* every cycle
        audit with the diversification recommendation but never blocks — pure
        observation, reversible at any time.
      - ``enabled=True`` + ``shadow_only=False``: enforce mode — a ``reject``
        recommendation (single-asset / BTC-ETH short-term cap breach) blocks the
        cycle with status ``diversification_rejected``.

    ``universe_scan_enabled`` is a separate opt-in read by the paper cron to
    broaden the hardcoded BTC/ETH scan into a diversified candidate set. It does
    not change in-loop behaviour and is safe in paper mode.
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_DIVERSIFICATION_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    shadow_only: bool = Field(default=True)
    universe_scan_enabled: bool = Field(default=False)
    # How many diversified candidates the universe scan emits per cron tick.
    universe_scan_limit: int = Field(default=6, ge=1, le=25)

    @property
    def mode(self) -> str:
        """Effective guard mode: 'enforce' only when enabled and not shadow-only."""
        if self.enabled and not self.shadow_only:
            return "enforce"
        return "shadow"


class KytSettings(BaseSettings):
    """KYT (Know Your Transaction) transaction-risk prevention configuration.

    Default-off, shadow-first — mirrors the diversification/Bayes rollout
    discipline so the execution path is unchanged until the operator opts in.

      - ``enabled=False`` (default): no gate is consulted anywhere; the trading
        loop behaves exactly as before. Read-only API/CLI surfaces still work.
      - ``enabled=True`` + ``shadow_only=True``: every transaction is assessed +
        audited + alerted, but never blocked — pure observation.
      - ``enabled=True`` + ``shadow_only=False``: enforce mode — a hold/block/
        manual_review decision refuses execution.

    ``provider`` selects the screening backend: ``local_lists`` (operator-curated
    rule lists, no network) or ``null`` (no external intelligence → unknown).
    External blockchain-analytics providers plug in behind the same Protocol.
    ``fail_mode=conservative`` makes provider failure HOLD un-screenable
    address/counterparty transactions and WARN exchange orders.
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_KYT_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    shadow_only: bool = Field(default=True)
    behavioral_enabled: bool = Field(default=True)
    provider: str = Field(default="local_lists")
    fail_mode: str = Field(default="conservative")
    retention_days: int = Field(default=180, ge=1)

    @property
    def mode(self) -> str:
        """Effective gate mode: 'enforce' only when enabled and not shadow-only."""
        if self.enabled and not self.shadow_only:
            return "enforce"
        return "shadow"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    monitor_dir: str = Field(default="monitor")
    # Bearer token for API auth. Empty = auth disabled (dev only). Set in production.
    api_key: str = Field(default="", repr=False)
    # SENTR-F-008: zero-downtime rotation. When set, requests with Bearer
    # <api_key_next> are also accepted. Rollover flow:
    #   1. operator sets APP_API_KEY_NEXT=<new>, redeploys — both keys valid.
    #   2. clients migrate to the new key at their own pace.
    #   3. operator promotes APP_API_KEY=<new>, clears APP_API_KEY_NEXT — single
    #      key again, old key is dead.
    # Empty string = disabled (single-key mode, no behaviour change).
    api_key_next: str = Field(default="", repr=False)
    # Cloudflare Access — emails allowed to pass via Cf-Access-Authenticated-User-Email
    # header. Comma-separated string ("a@x.de,b@y.de"). Empty = CF-Access trust disabled.
    # Accepts both APP_CF_ACCESS_ALLOWED_EMAILS (prefixed) and bare CF_ACCESS_ALLOWED_EMAILS.
    cf_access_allowed_emails: str = Field(
        default="",
        validation_alias=AliasChoices(
            "APP_CF_ACCESS_ALLOWED_EMAILS",
            "CF_ACCESS_ALLOWED_EMAILS",
        ),
    )
    # --- NEO-P-001 (B): Bind-address validator ---
    # The uvicorn --host value the operator expects the server to bind to.
    # Read by scripts/server_start.sh as the primary source; the legacy
    # KAI_BIND_LAN=1 flag still works as a backwards-compatible override.
    # In production environments, a non-loopback bind (0.0.0.0, ::, *) is
    # rejected unless APP_ALLOW_NON_LOOPBACK_BIND=1 is set explicitly —
    # forces operators to make the exposure decision consciously instead
    # of inheriting it silently from a migration (e.g. Docker, reverse
    # proxy change, Pi deployment). See validate_bind_host_against_env().
    api_bind_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("APP_API_BIND_HOST", "API_BIND_HOST"),
    )
    # Opt-out for the bind-address validator. Set to True only when a
    # downstream layer (reverse proxy firewall, container network policy)
    # provides equivalent loopback-scope protection.
    allow_non_loopback_bind: bool = Field(
        default=False,
        validation_alias=AliasChoices("APP_ALLOW_NON_LOOPBACK_BIND"),
    )
    # CORS allowed origins. Comma-separated list. Override in production.
    # Example: APP_CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
    cors_allowed_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8000"]
    )
    # Market data provider used by TradingLoop and operator surfaces.
    # Supported values: see app/market_data/service.py create_market_data_adapter.
    # Default `fallback` chains bybit→binance_futures→okx→bitmex→coingecko→mock
    # so premium Bybit-/OKX-/BitMEX-Futures-Channel symbols (incl. young/exotic
    # tokens CoinGecko does not list) resolve on the venue that actually quotes
    # them. The 2026-05-08 incident (ON/GIGGLE positions stuck "without value")
    # and the 2026-05-14 re-drift (BAS/ASTER closed as ostensibly "tot") both
    # traced back to coingecko-only reads; see memory-pin
    # `kai_market_data_provider_symmetry`. `.env` may override via
    # `APP_MARKET_DATA_PROVIDER=<provider>` — that override remains the runtime
    # knob. Setting `coingecko` here would silently regress to the unsafe
    # default if `.env` ever loses the line again.
    market_data_provider: str = Field(default="fallback")
    # Cross-exchange weighted-median price VALIDATION (Issue #169, default OFF).
    # When True, the aggregation hook in
    # ``app/market_data/cross_exchange_aggregator.py`` may run per-venue quotes
    # through ``validate_cross_exchange`` as a read-only diagnostic. This is a
    # validation/observability layer ONLY — it never opens, sizes, or blocks an
    # order, and it does not touch ``entry_mode``. Fail-closed default False so
    # live behaviour is unchanged until an operator opts in
    # (``APP_CROSS_EXCHANGE_VALIDATION_ENABLED=true``).
    cross_exchange_validation_enabled: bool = Field(default=False)
    # Optional CoinGecko Pro/Lite API key. When set, the adapter switches to
    # the pro-api.coingecko.com endpoint and sends the key via the
    # x-cg-pro-api-key header. Leave empty for free-tier.
    # Accepts both APP_COINGECKO_API_KEY (app-prefixed) and the bare
    # COINGECKO_API_KEY form already used by existing .env files.
    coingecko_api_key: str = Field(
        default="",
        repr=False,
        validation_alias=AliasChoices(
            "APP_COINGECKO_API_KEY",
            "COINGECKO_API_KEY",
        ),
    )
    # --- Pipeline Automation ---
    # Analysis provider for automated pipeline runs (openai, anthropic, gemini, internal).
    # Set to "" to disable LLM analysis in the scheduler (rule-based only).
    pipeline_provider: str = Field(default="openai")
    # Polling interval for the RSS scheduler in minutes.
    pipeline_interval_minutes: int = Field(default=15, ge=1)

    # --- Security Headers (SENTR-F-007) ---
    # Defense-in-depth for direct-path setups where the Cloudflare edge does
    # not terminate TLS or add security headers. Default enabled — disabling
    # only makes sense if a downstream reverse proxy already injects them.
    security_headers_enabled: bool = Field(default=True)
    # HSTS max-age. Default one year; set to 0 to disable the header.
    security_headers_hsts_max_age: int = Field(default=31_536_000, ge=0)
    # When True, the CSP is emitted as Content-Security-Policy-Report-Only
    # instead of enforcing. Used for a safe rollout before flipping to
    # enforce mode.
    security_headers_csp_report_only: bool = Field(default=False)
    # Additional script-src origins (space-separated) allowlisted on top of
    # the default 'self'. Useful if a future CDN or widget is added without
    # touching middleware code.
    security_headers_extra_csp_script_src: str = Field(default="")
    # When True, the CSP allows the TradingView embedded-chart widget
    # (script/frame/connect/img from *.tradingview.com plus inline scripts
    # the widget injects). Default True so the Märkte page works out of the
    # box; set False for hardened deployments that disabled the widget.
    security_headers_allow_tradingview: bool = Field(default=True)

    # --- Auth brute-force guard (SENTR-F-003) ---
    # In-memory sliding-window counter per client IP. Once the threshold is
    # reached within the window, further auth attempts return 429 with
    # Retry-After until the oldest failure ages out. Set threshold to 0 to
    # disable the guard entirely (e.g., for integration smokes that hammer
    # /dashboard/* intentionally).
    auth_rate_limit_threshold: int = Field(default=5, ge=0)
    auth_rate_limit_window_seconds: float = Field(default=300.0, gt=0.0)

    # --- Request Governance (Sprint 44) ---
    # Maximum request body size in bytes. Requests exceeding this limit are
    # rejected with HTTP 413 before reaching route handlers. Default: 64 KiB.
    max_request_body_bytes: int = Field(default=65_536, ge=1)
    # Guarded-endpoint rate-limit: maximum requests per sliding window per subject.
    # Window duration is APP_RATE_LIMIT_WINDOW_SECONDS.
    rate_limit_per_window: int = Field(default=5, ge=1)
    # Sliding-window duration in seconds for guarded-endpoint rate-limiting.
    rate_limit_window_seconds: float = Field(default=30.0, gt=0.0)
    # Idempotency replay window in seconds.  Responses cached for this duration.
    # Default: 300 s (5 min). A value of 0 disables idempotency caching.
    idempotency_window_seconds: float = Field(default=300.0, ge=0.0)

    db: DBSettings = Field(default_factory=DBSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    sources: SourceSettings = Field(default_factory=SourceSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    premium: PremiumSettings = Field(default_factory=PremiumSettings)
    premium_fastlane: PremiumFastlaneSettings = Field(default_factory=PremiumFastlaneSettings)
    operator: OperatorSettings = Field(default_factory=OperatorSettings)
    tradingview: TradingViewSettings = Field(default_factory=TradingViewSettings)
    binance: BinanceMarketDataSettings = Field(default_factory=BinanceMarketDataSettings)
    telegram_channel_ingest: TelegramChannelIngestSettings = Field(
        default_factory=TelegramChannelIngestSettings
    )
    learning: LearningSettings = Field(default_factory=LearningSettings)
    # Asset-diversification / concentration guard. Default-off, shadow-first.
    diversification: DiversificationSettings = Field(default_factory=DiversificationSettings)
    # KYT transaction-risk prevention. Default-off, shadow-first.
    kyt: KytSettings = Field(default_factory=KytSettings)
    # D-191 re-entry capability gate. Default disabled — see ReEntryModeProfile.
    re_entry_mode: ReEntryModeProfile = Field(default_factory=ReEntryModeProfile)

    _strip_secrets = field_validator("api_key", "api_key_next", "coingecko_api_key", mode="before")(
        _strip_secret
    )

    @model_validator(mode="after")
    def validate_bind_host_against_env(self) -> "AppSettings":
        """NEO-P-001 (B): reject non-loopback bind in production envs.

        A 0.0.0.0 / :: / * bind directly exposes the API to whatever
        network the host sits on — which is fine locally, catastrophic
        in production where the Cloudflare tunnel is the single intended
        ingress. Opt-out via APP_ALLOW_NON_LOOPBACK_BIND=1 for container
        setups that rely on a downstream firewall.
        """
        prod_envs = {"production", "prod", "live"}
        loopback = {"127.0.0.1", "localhost", "::1"}
        host = (self.api_bind_host or "").strip().lower()
        if (
            self.env.lower() in prod_envs
            and host not in loopback
            and not self.allow_non_loopback_bind
        ):
            raise ConfigurationError(
                f"APP_API_BIND_HOST='{self.api_bind_host}' is not loopback but "
                f"APP_ENV='{self.env}'. A non-loopback bind exposes the API "
                "beyond the Cloudflare tunnel. Either set APP_API_BIND_HOST=127.0.0.1 "
                "or — if a downstream firewall protects the host — set "
                "APP_ALLOW_NON_LOOPBACK_BIND=1 explicitly."
            )
        return self

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> "AppSettings":
        validate_runtime_config_payload(self.to_runtime_config_payload())
        return self

    @model_validator(mode="after")
    def _enforce_re_entry_invariants(self) -> "AppSettings":
        """D-191 re-entry-gate enforcement.

        When ``re_entry_mode.enabled`` is False (today's default) this is a
        no-op and the laptop boot is unchanged. When True, every selected
        ``enforce_*`` invariant must hold or boot fails with
        ``ConfigurationError`` — fail-closed, never fail-open.
        """
        gate = self.re_entry_mode
        if not gate.enabled:
            return self

        violations: list[str] = []

        # S-001: provenance HMAC seal secret must be present.
        if gate.enforce_provenance_secret and not ((self.alerts.provenance_secret or "").strip()):
            violations.append(
                "RE_ENTRY_MODE_ENFORCE_PROVENANCE_SECRET=1 but "
                "ALERT_PROVENANCE_SECRET is empty (S-001)."
            )

        # S-002a: TradingView webhook replay cache must be persistent.
        if (
            gate.enforce_replay_cache_persistent
            and not self.tradingview.webhook_replay_cache_persistent
        ):
            violations.append(
                "RE_ENTRY_MODE_ENFORCE_REPLAY_CACHE_PERSISTENT=1 but "
                "TRADINGVIEW_WEBHOOK_REPLAY_CACHE_PERSISTENT=false — a "
                "restart within the replay window would re-open the door (S-002)."
            )

        # S-002b: replay-cache DB path must be absolute (working-directory
        # safety: relative paths break under systemd / Pi rootless setups).
        if gate.enforce_replay_cache_absolute_path:
            db_path = (self.tradingview.webhook_replay_cache_db_path or "").strip()
            if not db_path or not Path(db_path).is_absolute():
                violations.append(
                    "RE_ENTRY_MODE_ENFORCE_REPLAY_CACHE_ABSOLUTE_PATH=1 but "
                    f"TRADINGVIEW_WEBHOOK_REPLAY_CACHE_DB_PATH='{db_path}' "
                    "is not absolute (S-002)."
                )

        # S-003: telegram listener heartbeat path must be configured.
        if gate.enforce_watchdog_heartbeat:
            hb = (self.telegram_channel_ingest.heartbeat_path or "").strip()
            if not hb:
                violations.append(
                    "RE_ENTRY_MODE_ENFORCE_WATCHDOG_HEARTBEAT=1 but "
                    "INGESTION_TELEGRAM_CHANNEL_HEARTBEAT_PATH is empty (S-003)."
                )

        # B-002: complete observability surface — capability flag.
        # Telemetry for LLM-failure-rate / latency p95 is not implemented
        # yet (see /status). We model that as a hard-coded capability
        # flag here so flipping the enforce switch deliberately fails
        # boot until the implementation lands.
        if gate.enforce_observability_complete:
            observability_complete = False  # B-002 not yet implemented.
            if not observability_complete:
                violations.append(
                    "RE_ENTRY_MODE_ENFORCE_OBSERVABILITY_COMPLETE=1 but "
                    "B-002 (LLM-failure-rate, latency p95) is not yet "
                    "implemented — /status still returns 'not_implemented' "
                    "for those fields."
                )

        if violations:
            raise ConfigurationError(
                "Re-entry invariants violated:\n  - " + "\n  - ".join(violations)
            )
        return self

    def to_runtime_config_payload(self) -> dict[str, object]:
        return build_runtime_config_payload(self)


def build_runtime_config_payload(settings: AppSettings) -> dict[str, object]:
    """Project the current AppSettings instance into the bundled config contract."""

    primary_model = settings.providers.openai_model or "gpt-4o"
    fallback_model = (
        settings.providers.anthropic_model or settings.providers.gemini_model or primary_model
    )
    return {
        "system_runtime": {
            "app_name": "KAI",
            "environment": settings.env,
            "mode": settings.execution.mode.value,
            "timezone_internal": "UTC",
            "timezone_display": "UTC",
            "log_level": settings.log_level,
            "debug": settings.env.lower() in {"development", "dev", "local"},
            "dry_run": settings.execution.dry_run,
            "safe_mode": True,
            "maintenance_mode": False,
            "service_version": "0.1.0",
        },
        "llm_agent": {
            "primary_model": primary_model,
            "fallback_model": fallback_model,
            "max_tokens": 2048,
            "temperature": 0.0,
            "timeout_seconds": settings.providers.openai_timeout,
            "retry_count": settings.sources.max_retries,
            "max_tool_calls": 4,
            "max_plan_steps": 8,
            "reasoning_budget": 0,
            "response_schema_version": "1.0",
            "prompt_version": "v1",
            "planner_model": primary_model,
            "executor_model": primary_model,
            "validator_model": fallback_model,
            "reflection_enabled": False,
            "self_critique_enabled": False,
        },
        "market_data": {
            "enabled_data_sources": ["rss"],
            "symbols_whitelist": [],
            "exchanges_whitelist": [],
            "timeframes": ["1h", "4h", "1d"],
            "data_freshness_threshold_seconds": 3600,
            "candle_gap_tolerance": 1,
            "max_news_age_minutes": 240,
            "sentiment_source_weights": {"rule_based": 1.0},
            "macro_source_weights": {"calendar": 1.0},
            "orderbook_depth_required": 0.0,
            "stale_data_fail_policy": "fail_closed",
            "min_source_count_for_decision": 1,
        },
        "risk": {
            "initial_equity_reference": settings.risk.initial_equity,
            "max_risk_per_trade_pct": settings.risk.max_risk_per_trade_pct,
            "max_daily_loss_pct": settings.risk.max_daily_loss_pct,
            "max_total_drawdown_pct": settings.risk.max_total_drawdown_pct,
            "max_open_positions": settings.risk.max_open_positions,
            "max_sector_or_theme_exposure_pct": 25.0,
            "max_correlated_exposure_pct": 50.0,
            "max_leverage": settings.risk.max_leverage,
            "require_stop_loss": settings.risk.require_stop_loss,
            "allow_averaging_down": settings.risk.allow_averaging_down,
            "allow_martingale": settings.risk.allow_martingale,
            "allow_unbounded_loss": False,
            "slippage_limit_bps": 25,
            "fee_buffer_bps": 10,
            "min_liquidity_threshold": 1000000.0,
            "kill_switch_enabled": settings.risk.kill_switch_enabled,
            "max_position_holding_time": "24h",
            "cooldown_after_loss_minutes": settings.risk.cooldown_after_loss_minutes,
            "cooldown_after_error_minutes": settings.risk.cooldown_after_error_minutes,
        },
        "strategy_decision": {
            "min_signal_confidence": settings.risk.min_signal_confidence,
            "min_signal_confluence_count": settings.risk.min_signal_confluence_count,
            "regime_filter_enabled": True,
            "volatility_filter_enabled": True,
            "liquidity_filter_enabled": True,
            "news_risk_filter_enabled": True,
            "macro_event_filter_enabled": True,
            "invalidation_rule_required": True,
            "thesis_required": True,
            "contradiction_check_required": True,
            "scenario_analysis_required": True,
        },
        "execution": {
            "order_type_policy": "market_or_limit",
            "order_ttl_seconds": settings.execution.order_ttl_seconds,
            "max_order_retries": settings.execution.max_order_retries,
            "idempotency_key_required": True,
            "execution_timeout_seconds": settings.execution.execution_timeout_seconds,
            "partial_fill_policy": "cancel_remaining",
            "reconciliation_interval_seconds": 60,
            "exchange_heartbeat_timeout_seconds": 30,
            "broker_failover_policy": "fail_closed",
            "live_execution_enabled": settings.execution.live_enabled,
            "approval_required_for_live_actions": settings.execution.approval_required,
        },
        "memory_learning": {
            "memory_enabled": True,
            "episodic_memory_ttl_days": 30,
            "strategy_journal_enabled": True,
            "operator_feedback_enabled": True,
            "self_improvement_enabled": False,
            "self_modification_in_production": False,
            "learning_review_required": True,
            "knowledge_source_trust_ranking": {
                "operator": 1.0,
                "validated_model": 0.7,
            },
            "model_eval_threshold": 0.8,
            "drift_detection_enabled": True,
            "rollback_required_for_learning_updates": True,
            "memory_compaction_policy": "append_only",
            "memory_retention_policy": "audit_first",
        },
        "security": {
            "secret_backend": "environment",
            "allowed_hosts": ["localhost", "127.0.0.1"],
            "webhook_signature_required": True,
            "api_auth_required": True,
            "RBAC_enabled": True,
            "audit_log_immutable": True,
            "encryption_at_rest_required": True,
            "encryption_in_transit_required": True,
            "prompt_injection_filter_enabled": True,
            "sandbox_required_for_code_execution": True,
            "dependency_scan_required": True,
            "secret_scan_required": True,
        },
        "messaging_ux": {
            "telegram_enabled": settings.alerts.telegram_enabled,
            "telegram_admin_chat_ids": settings.operator.admin_chat_id_list,
            "alert_severity_threshold": "warning",
            "summary_schedule": "manual",
            "voice_interface_enabled": False,
            "avatar_interface_enabled": False,
            "operator_approval_required_for_critical_actions": True,
        },
    }


def validate_runtime_config_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Validate a runtime config payload against the bundled KAI config schema."""

    return _validate_runtime_config_payload(payload)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Process-cached application settings.

    AUDIT follow-up: previously this constructed a fresh ``AppSettings()`` on
    EVERY call, which re-reads and re-parses the ``.env`` file each time. Callers
    in hot loops (e.g. the hold-metrics / directional-eligibility path hit by the
    dashboard quality poll) therefore re-parsed ``.env`` per item and wedged the
    event loop. Settings are immutable for the process lifetime — env/.env changes
    take effect on restart (the normal systemd flow) — so caching is correct and
    eliminates the whole "get_settings() in a loop" performance landmine.

    Tests that mutate the environment per case clear this cache via the autouse
    ``_reset_settings_cache`` fixture (tests/conftest.py); call
    ``get_settings.cache_clear()`` explicitly if you change env mid-test.
    """
    return AppSettings()
