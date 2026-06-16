// Zentraler API-Client für das KAI-Dashboard.
// - Same-origin im Prod-Build (unter /dashboard), im Dev via Vite-Proxy auf :8000.
// - Authentifizierung läuft über Cloudflare Access vor dem Tunnel — der
//   Browser sendet KEINEN Bearer-Token mehr. CF setzt den Identity-Header
//   (Cf-Access-Authenticated-User-Email), den die Server-Middleware prüft.
// - Einheitliches Error-Objekt, damit Pages konsistent reagieren können.

import { toNum, toNumOr } from "@/lib/num";

export type ApiErrorKind =
  | "network"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "server"
  | "bad_response";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number;
  readonly path: string;

  constructor(kind: ApiErrorKind, status: number, path: string, message: string) {
    super(message);
    this.kind = kind;
    this.status = status;
    this.path = path;
  }
}

function buildHeaders(extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (extra) Object.assign(headers, extra as Record<string, string>);
  return headers;
}

async function parseOrThrow<T>(res: Response, path: string): Promise<T> {
  if (res.ok) {
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      try {
        return (await res.json()) as T;
      } catch {
        throw new ApiError("bad_response", res.status, path, "JSON parse error");
      }
    }
    // Text fallback for endpoints that may return text
    return (await res.text()) as unknown as T;
  }

  let detail = res.statusText;
  try {
    const body = await res.json();
    if (body && typeof body === "object" && "detail" in body) {
      detail = String((body as { detail: unknown }).detail);
    }
  } catch {
    // keep statusText
  }

  if (res.status === 401) throw new ApiError("unauthorized", 401, path, detail);
  if (res.status === 403) throw new ApiError("forbidden", 403, path, detail);
  if (res.status === 404) throw new ApiError("not_found", 404, path, detail);
  if (res.status >= 500) throw new ApiError("server", res.status, path, detail);
  throw new ApiError("bad_response", res.status, path, detail);
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    const res = await fetch(path, {
      ...init,
      method: "GET",
      headers: buildHeaders(init?.headers),
    });
    return await parseOrThrow<T>(res, path);
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError("network", 0, path, (e as Error).message || "network error");
  }
}

export async function apiPost<T>(
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  try {
    const res = await fetch(path, {
      ...init,
      method: "POST",
      headers: buildHeaders({
        "Content-Type": "application/json",
        ...(init?.headers as Record<string, string> | undefined),
      }),
      body: body != null ? JSON.stringify(body) : undefined,
    });
    return await parseOrThrow<T>(res, path);
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError("network", 0, path, (e as Error).message || "network error");
  }
}

// -----------------------------------------------------------------------------
// Typed endpoint shims. Keep these minimal; pages import specific ones they use.
// -----------------------------------------------------------------------------

export type HealthResponse = { status: string; version: string };

export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiGet<HealthResponse>("/health", { signal });
}

export type TimerHealthInactiveEntry = {
  unit: string;
  state: string;
  // FS-2 (#198) taxonomy: recurring_required | one_shot_expected_inactive | disabled_by_design
  category?: string | null;
  // ok | expected_inactive | critical
  severity?: string | null;
  last_trigger: string | null;
};

export type TimerHealthResponse = {
  state: "ok" | "has_inactive" | "stale" | "no_data" | "corrupt" | "critical";
  // FS-2: overall severity + taxonomy counts (expected_inactive vs failed).
  severity?: string;
  checked_at: string | null;
  stale_minutes: number | null;
  total: number;
  active: number;
  critical_count?: number;
  expected_inactive_count?: number;
  inactive: TimerHealthInactiveEntry[];
};

export function fetchTimerHealth(signal?: AbortSignal): Promise<TimerHealthResponse> {
  return apiGet<TimerHealthResponse>("/health/timers", { signal });
}

export type EntryRuntime = {
  entry_mode: string | null;
  entry_mode_label: string;
  autonomous_loop_open?: boolean;
  open_routes?: { route: string; alias_used: string | null }[];
  contradictions?: string[];
  error?: string;
};

export type ShadowAttribution = {
  real_candidates_24h: number;
  probe_candidates_24h: number;
};

export type DashboardQuality = {
  dashboard_truth_contract_version?: number;
  /** S6 (#157 scope gap): live entry-mode truth incl. D-233 limited modes. */
  runtime?: EntryRuntime;
  /** S6: real-vs-canary attribution of shadow candidates (24h). */
  shadow_attribution?: ShadowAttribution;
  metric_contract?: Record<
    string,
    {
      value: unknown;
      unit: string;
      semantic_type: string;
      scope: string;
      window_hours: number | null;
      since: string | null;
      until: string | null;
      generated_at: string;
      source_artifact: string;
      source_artifact_updated_at: string | null;
      stale_status: string;
      sample_size: number | null;
      confidence_interval: Record<string, number | null> | null;
      is_decision_relevant: boolean;
      is_read_only: boolean;
      quality_status: string;
      warning: string | null;
      explanation: string | null;
    }
  >;
  reentry?: {
    target_date: string;
    today: string;
    status: "active" | "expired" | "unverified" | string;
    days_delta: number | null;
    warning: string | null;
  };
  precision_pct: number | null;
  false_positive_pct: number | null;
  resolved_count: number;
  directional_count: number;
  hits: number;
  misses: number;
  active_precision_pct: number | null;
  active_resolved_count: number;
  active_hits: number;
  active_misses: number;
  legacy_resolved_count: number;
  legacy_unknown_cutoff: string | null;
  /** @deprecated D-149: Pearson auf P7-P10-Band ist nicht aussagekraeftig.
   *  Verwende priority_tier_lift_pct stattdessen. */
  priority_corr: number | null;
  priority_tier_lift_pct: number | null;
  priority_tier_high_conviction_threshold: number | null;
  priority_tier_high_conviction_resolved: number | null;
  priority_tier_high_conviction_hit_rate_pct: number | null;
  priority_tier_high_conviction_ci_low_pct: number | null;
  priority_tier_high_conviction_ci_high_pct: number | null;
  priority_tier_standard_resolved: number | null;
  priority_tier_standard_hit_rate_pct: number | null;
  priority_tier_standard_ci_low_pct: number | null;
  priority_tier_standard_ci_high_pct: number | null;
  forward_precision_pct: number | null;
  forward_resolved: number;
  forward_hits: number;
  forward_miss: number;
  paper_fills: number;
  paper_fills_with_pnl: number;
  paper_realized_pnl_usd: number;
  paper_quarantined_pnl_usd?: number;
  paper_quarantined_closes?: number;
  paper_positions_closed: number;
  paper_positions_partial_closed?: number;
  paper_evidence?: {
    scope: string;
    since: string | null;
    until: string;
    window_hours: number;
    fills_total: number;
    fills_recent_24h: number;
    closed_total: number;
    closed_recent_24h: number;
    realized_pnl_total_usd: number;
    realized_pnl_recent_24h_usd: number;
    expectancy_usd: number | null;
    win_rate_pct: number | null;
    avg_win_usd: number | null;
    avg_loss_usd: number | null;
    fees_slippage_included: string;
    source_artifact: string;
    source_artifact_updated_at: string | null;
    stale_status: string;
    quality_status: string;
    warning: string | null;
  };
  audit_v1_disqualified?: boolean;
  audit_provenance?: {
    cut_off_commit?: string;
    cut_off_ts?: string;
    n_closes_v1?: number;
    realized_pnl_v1?: number;
    error?: string;
  } | null;
  paper_cycles: number;
  real_price_cycles: number;
  gate_status: string | null;
  blocking_reasons: string[];
  actionable_rate_pct: number | null;
  high_priority_hit_rate_pct: number | null;
  low_priority_hit_rate_pct: number | null;
  loop_status_counts: Record<string, number>;
  // V-DB4a 2026-05-08: Per-source active precision (Pfad-A Quality).
  per_source_active_precision?: {
    min_resolved: number;
    min_wilson_low_pct: number;
    sources_passing: string[];
    by_source: Record<
      string,
      {
        resolved: number;
        hits: number;
        misses: number;
        hit_rate_pct: number | null;
        ci_low_pct: number | null;
        ci_high_pct: number | null;
        n_threshold_met: boolean;
        wilson_low_threshold_met: boolean;
        passes_gate: boolean;
      }
    >;
  };
  // V-DB4e 2026-05-08: Per-source rolling stability windows.
  per_source_stability?: {
    window_days: number;
    window_count: number;
    min_resolved_per_window: number;
    min_wilson_low_pct: number;
    anchor_at?: string;
    by_source: Record<
      string,
      {
        stable: boolean;
        windows: Array<{
          window_start: string;
          window_end: string;
          resolved: number;
          hits: number;
          misses: number;
          hit_rate_pct: number | null;
          ci_low_pct: number | null;
          n_threshold_met: boolean;
          wilson_low_threshold_met: boolean;
          passes_window: boolean;
          fail_reason?: string | null;
        }>;
      }
    >;
  };
  source_reliability?: {
    status: "ok" | "missing" | "unreadable" | "invalid" | string;
    generated_at: string | null;
    window_days: number | null;
    thresholds?: Record<string, number>;
    quality_status?: string;
    health_warning?: string | null;
    trusted_count?: number;
    source_count: number;
    tier_counts: Record<string, number>;
    top_sources: Array<{
      source_name: string;
      hits: number;
      miss: number;
      n: number;
      point_estimate_pct: number | null;
      wilson_lower_95_pct: number | null;
      tier: string;
      priority_modifier: number;
    }>;
    unknown_bucket: {
      source_name: string;
      hits: number;
      miss: number;
      n: number;
      point_estimate_pct: number | null;
      wilson_lower_95_pct: number | null;
      tier: string;
      priority_modifier: number;
    } | null;
  };
  recent_alerts: Array<{
    doc_id: string;
    sentiment: string;
    priority: number | null;
    assets: string[];
    dispatched_at: string;
    outcome: string;
  }>;
  generated_at: string;
};

export function fetchDashboardQuality(signal?: AbortSignal): Promise<DashboardQuality> {
  return apiGet<DashboardQuality>("/dashboard/api/quality", { signal });
}

// --- Die 5 „n" (Dali 2026-06-13) -------------------------------------------
// SSOT-Disambiguierung der fünf verschiedenen „resolved/n"-Zähler. Nur das
// Gate-n (resolved_real) zählt fürs #167-Edge-Gate; die anderen vier messen
// andere Pipelines. Backend: /dashboard/api/n-overview.
export type NStatusTone = "pos" | "warn" | "muted" | "neg";

export type NOverviewEntry = {
  key: string;
  label: string;
  value: number | null;
  source: string;
  measures: string;
  status_tag?: string | null;
  status_tone?: NStatusTone;
};

export type NOverviewGate = {
  key: string;
  label: string;
  value: number | null;
  threshold: number;
  ratio_pct: number | null;
  sufficient: boolean;
  source: string;
  measures: string;
  watch_hint: string;
  // gate 1 only:
  filter?: string;
  // ev_gate only:
  verdict?: string | null;
  ev_after_costs_bps?: number | null;
};

export type NOverview = {
  gate: NOverviewGate;
  ev_gate: NOverviewGate;
  others: NOverviewEntry[];
  trap_note: string;
};

export function fetchNOverview(signal?: AbortSignal): Promise<NOverview> {
  return apiGet<NOverview>("/dashboard/api/n-overview", { signal });
}

// Lightning Phase-1 (read-only, default-off). Spiegelt LightningNodeStatus.
export type LightningStatus = {
  state: "disabled" | "unavailable" | "ok";
  reachable: boolean;
  server_state: string;
  info_available: boolean;
  synced_to_chain: boolean;
  block_height: number;
  num_peers: number;
  num_active_channels: number;
  identity_pubkey: string;
  alias: string;
  version: string;
  reason: string;
  extra: Record<string, unknown>;
  generated_at: string;
};

export function fetchLightningStatus(signal?: AbortSignal): Promise<LightningStatus> {
  return apiGet<LightningStatus>("/dashboard/api/lightning", { signal });
}

// L1 — souveräne On-Chain-Wahrheit aus KAIs eigener bitcoind (read-only, default-off).
export type ChainStatus = {
  state: "disabled" | "unavailable" | "ok";
  reachable: boolean;
  chain: string;
  blocks: number;
  headers: number;
  synced: boolean;
  fee_sat_vb: number | null;
  mempool_tx: number;
  reason: string;
  extra: Record<string, unknown>;
  generated_at: string;
};

export function fetchChainStatus(signal?: AbortSignal): Promise<ChainStatus> {
  return apiGet<ChainStatus>("/dashboard/api/chain", { signal });
}

export type ProvenanceMetrics = {
  source: string;
  resolved: number;
  hits: number;
  misses: number;
  hit_rate_pct: number | null;
  ci_low_pct: number | null;
  ci_high_pct: number | null;
  ci_width_pct: number | null;
  sample_sufficient: boolean;
  inconclusive: number;
};

export type DashboardProvenance = {
  generated_at: string;
  overall: ProvenanceMetrics;
  // V3: same ratio without the legacy `unknown` bucket — represents the
  // currently-tagged signal flow precision. Optional for backward-compat
  // with cached payloads from before V3.
  overall_active?: ProvenanceMetrics;
  by_source: ProvenanceMetrics[];
  tradingview_pipeline: {
    pending_events: number;
    smoke_test_events: number;
    real_events: number;
    unique_signal_path_ids: number;
  };
  verdict: string;
  notes: string[];
  min_sample_for_judgment: number;
};

export function fetchDashboardProvenance(
  signal?: AbortSignal,
): Promise<DashboardProvenance> {
  return apiGet<DashboardProvenance>("/dashboard/api/provenance", { signal });
}

// REGIME-R1 (2026-05-09): per-asset regime classification, hourly cron.
// Backend: app/regime/ — six classes (trend_up/down, breakout_up/down,
// chop_quiet/volatile) + three vol classes (vol_low/normal/high). Read-only
// observer phase; TradingLoop is NOT yet gated by regime.
export type RegimeClass =
  | "trend_up"
  | "trend_down"
  | "breakout_up"
  | "breakout_down"
  | "chop_quiet"
  | "chop_volatile"
  | "unknown";

export type VolClass = "vol_low" | "vol_normal" | "vol_high";

export type RegimeSnapshot = {
  asset: string;
  timestamp: string;
  regime: RegimeClass;
  vol_class: VolClass;
  confidence: number;
  adx?: number;
  plus_di?: number;
  minus_di?: number;
  rv_24h?: number;
  atr_zscore?: number;
  pending_regime?: RegimeClass;
  pending_consecutive?: number;
};

export type DashboardRegime = {
  generated_at: string;
  semantic_status?: "read_only" | string;
  is_read_only?: boolean;
  is_decision_relevant?: boolean;
  warning?: string | null;
  by_asset: Record<string, RegimeSnapshot>;
  by_asset_metadata?: Record<
    string,
    {
      source_artifact: string;
      source_artifact_updated_at: string | null;
      snapshot_timestamp: string;
      snapshot_age_hours: number | null;
      stale_status: string;
      is_read_only: boolean;
      is_decision_relevant: boolean;
      quality_status: string;
      warning: string | null;
    }
  >;
};

export function fetchDashboardRegime(
  signal?: AbortSignal,
): Promise<DashboardRegime> {
  return apiGet<DashboardRegime>("/dashboard/api/regime", { signal });
}

// D-184: Priority-tier gate (D-182) operator visibility.
export type PriorityGateSummary = {
  report_type: string;
  threshold: number;
  gate_active: boolean;
  window_hours: number;
  total_cycles: number;
  priority_rejected: number;
  other_rejected: number;
  completed: number;
  rejected_total?: number;
  rejected_pct?: number;
  filled_total?: number;
  top_reject_reason?: string | null;
  threshold_effect?: string;
  // Loop-heartbeat truth: backend (dashboard.py) distinguishes "gate blocking
  // actively" from "loop possibly down". 0 filled must NEVER read as healthy
  // when the loop cannot be proven alive.
  heartbeat_status?: "active" | "active_blocking" | "stale" | "unknown" | string;
  heartbeat_warning?: string | null;
  loop_audit_present?: boolean;
  loop_audit_freshness?: string;
  priority_quality?: {
    high_priority_lift_pct?: number | null;
    high_priority_resolved?: number | null;
    standard_resolved?: number | null;
    current_quality_verdict: string;
    warning?: string | null;
  };
  window_start_utc: string;
  audit_path: string;
};

export function fetchPriorityGate(
  signal?: AbortSignal,
): Promise<PriorityGateSummary> {
  return apiGet<PriorityGateSummary>("/dashboard/api/priority-gate", { signal });
}

// ---------------- Operator surfaces ----------------

export type OperatorStatus = {
  report_type: string;
  execution_enabled: boolean;
  write_back_allowed: boolean;
  status: string;
  // Backend may serialize these as Decimal strings; fetchOperatorStatus coerces
  // them to numbers (or null when absent/unparseable) before they reach a consumer.
  position_count?: number | null;
  cash_usd?: number | null;
  total_equity_usd?: number | null;
  realized_pnl_usd?: number | null;
};

// Same Decimal-as-string defense as the portfolio snapshot: these money fields
// are already honestly typed `number | string`; coerce so consumers get numbers.
function normalizeOperatorStatus(raw: OperatorStatus): OperatorStatus {
  return {
    ...raw,
    position_count: raw.position_count === undefined ? undefined : toNum(raw.position_count),
    cash_usd: raw.cash_usd === undefined ? undefined : toNum(raw.cash_usd),
    total_equity_usd: raw.total_equity_usd === undefined ? undefined : toNum(raw.total_equity_usd),
    realized_pnl_usd:
      raw.realized_pnl_usd === undefined ? undefined : toNum(raw.realized_pnl_usd),
  };
}

export async function fetchOperatorStatus(signal?: AbortSignal): Promise<OperatorStatus> {
  return normalizeOperatorStatus(await apiGet<OperatorStatus>("/operator/status", { signal }));
}

export function fetchOperatorReadiness(signal?: AbortSignal): Promise<OperatorStatus> {
  return apiGet<OperatorStatus>("/operator/readiness", { signal });
}

export function fetchOperatorDecisionPack(signal?: AbortSignal): Promise<OperatorStatus> {
  return apiGet<OperatorStatus>("/operator/decision-pack", { signal });
}

export type AlertOutcome = "hit" | "miss" | "inconclusive";

export type AlertAuditEntry = {
  document_id: string;
  channel: string;
  message_id: string | null;
  is_digest: boolean;
  dispatched_at: string;
  outcome?: AlertOutcome;
  resolved_at?: string;
  resolved_after_seconds?: number;
  // 2026-05-10 DALI-A2: zusätzliche Felder die Backend bereits liefert,
  // aber bisher nicht im Frontend-Type abgebildet waren. Erlaubt sprechende
  // "Was"-Spalte (Sentiment/Assets/Source) statt Document-Hash-Murmel.
  // Alle optional — Backend liefert teils null/missing bei alten Records.
  sentiment_label?: string | null;
  affected_assets?: string[];
  priority?: number | null;
  source_name?: string | null;
  directional_block_reason?: string | null;
};

export type AlertAuditSummary = {
  report_type: string;
  execution_enabled: boolean;
  write_back_allowed: boolean;
  total_alerts: number;
  total_resolved?: number;
  alerts: AlertAuditEntry[];
};

export function fetchAlertAudit(signal?: AbortSignal): Promise<AlertAuditSummary> {
  return apiGet<AlertAuditSummary>("/operator/alert-audit", { signal });
}

export type PaperPositionTpTier = {
  price: number;
  qty_share: number;
};

export type PaperPosition = {
  symbol: string;
  quantity: number;
  avg_entry_price: number;
  stop_loss: number | null;
  take_profit: number | null;
  market_price: number | null;
  market_value_usd: number | null;
  unrealized_pnl_usd: number | null;
  // C-Fix 2026-06-13: display-only Mark-to-Market fallback. Wenn der Provider
  // keinen Live-Kurs liefert, traegt display_value_usd den Einstandswert
  // (quantity × avg_entry_price); mark_basis = "live" | "entry_fallback".
  // market_value_usd/unrealized_pnl_usd bleiben null (gate-safe).
  display_value_usd?: number | null;
  mark_basis?: string | null;
  // Sprint A (2026-05-12) Premium-Signal-Pipeline: erweiterte Felder so dass
  // Portfolio.tsx Side/Leverage/Source/Status/Tiers ohne Backend-Crosswalk
  // anzeigen kann. Alle optional weil pre-Sprint-A audit-records sie nicht
  // tragen (Backend gibt null/Default zurück).
  position_side?: "long" | "short" | null;
  leverage?: number | null;
  source?: string | null;
  opened_at?: string | null;
  correlation_id?: string | null;
  realized_pnl_usd?: number | null;
  take_profit_tiers?: PaperPositionTpTier[] | null;
  initial_quantity?: number | null;
  provider?: string | null;
  market_data_is_stale?: boolean | null;
  market_data_available?: boolean | null;
  market_data_error?: string | null;
};

export type PortfolioSnapshot = {
  report_type: string;
  generated_at: string;
  source: string;
  audit_path: string;
  cash_usd: number;
  realized_pnl_usd: number;
  total_market_value_usd: number;
  total_equity_usd: number;
  position_count: number;
  positions: PaperPosition[];
};

// ── Premium-Signal Operator-Actions (Sprint E 2026-05-12) ───────────────────

export type PendingEnvelopeRow = {
  envelope_id: string;
  timestamp_utc: string | null;
  source: string | null;
  symbol: string | null;
  direction: string | null;
  entry_value: number | null;
  stop_loss: number | null;
  targets: number[] | null;
  leverage: number | null;
  current_bridge_stage: string | null;
};

export type PendingEnvelopesResponse = {
  count: number;
  terminal_stages: string[];
  envelopes: PendingEnvelopeRow[];
};

export function fetchPendingEnvelopes(limit = 50, signal?: AbortSignal): Promise<PendingEnvelopesResponse> {
  return apiGet<PendingEnvelopesResponse>(`/api/premium-signals/pending-envelopes?limit=${limit}`, { signal });
}

export function postManualFill(envelopeId: string, idempotencyKey?: string): Promise<Record<string, unknown>> {
  return apiPost("/api/premium-signals/manual-fill", {
    envelope_id: envelopeId,
    idempotency_key: idempotencyKey,
  });
}

export function postReprocess(envelopeId?: string, idempotencyKey?: string): Promise<Record<string, unknown>> {
  return apiPost("/api/premium-signals/reprocess", {
    envelope_id: envelopeId,
    idempotency_key: idempotencyKey,
  });
}

export function postReconcileCompletion(
  symbol: string,
  touchPrice?: number,
  idempotencyKey?: string,
): Promise<Record<string, unknown>> {
  return apiPost("/api/premium-signals/reconcile-target-completion", {
    symbol,
    touch_price: touchPrice,
    idempotency_key: idempotencyKey,
  });
}

export function postPositionRepair(
  symbol: string,
  action: "close" | "adjust",
  options?: { new_stop_loss?: number; new_take_profit?: number; idempotency_key?: string },
): Promise<Record<string, unknown>> {
  return apiPost("/api/premium-signals/position-repair", {
    symbol,
    action,
    new_stop_loss: options?.new_stop_loss,
    new_take_profit: options?.new_take_profit,
    idempotency_key: options?.idempotency_key,
  });
}

// ── Premium-Signal Trail (2026-05-20 /goal) ──────────────────────────────────

export type PremiumSignalTrailStage = {
  name: string;
  ok: boolean;
  label: string;
  ts?: string | null;
  reason?: string | null;
  detail?: Record<string, unknown>;
};

export type PremiumSignalTrailBridgeHistoryEntry = {
  ts: string | null;
  stage: string | null;
  audit_reason: string | null;
  lifecycle_state?: string | null;
  order_id?: string | null;
  fill_price?: number | null;
  quantity?: number | null;
};

export type PremiumSignalTargetStatus = {
  target_number: number;
  target_price: number;
  status: "hit" | "missed" | "pending" | "skipped" | "unknown";
  hit_at?: string | null;
};

// 2026-05-28 /goal: operatorzentrierte Auswertungs-Schicht (Backend:
// app/observability/premium_signal_analytics.py). Optional, damit ältere
// API-Antworten / unvollständige Records die UI nicht brechen.
export type PremiumSignalAnalytics = {
  signal_type: "internal" | "external";
  source_name: string | null;
  invested_capital: number | null;
  available_capital_at_entry: number | null;
  invested_capital_pct: number | null;
  capital_base_note: string | null;
  actual_entry_price: number | null;
  planned_entry_value: number | null;
  entry_status:
    | "entered_on_time"
    | "waited_for_entry"
    | "entered_late"
    | "missed_entry"
    | "unknown";
  entry_delay_seconds: number | null;
  entry_delay_label: string;
  trade_result_status:
    | "win"
    | "loss"
    | "break_even"
    | "open"
    | "cancelled"
    | "unknown";
  final_pnl_usd: number | null;
  final_pnl_pct: number | null;
  final_pnl_source: "engine" | "fills" | null;
  targets: PremiumSignalTargetStatus[];
  source_quality_status: "good" | "medium" | "weak" | "unknown";
  source_quality_reason: string;
  analysis_hints: string[];
};

export type PremiumSignalTrailEntry = {
  envelope_id: string;
  source_uid: string | null;
  source_platform: string | null;
  symbol: string;
  received_at: string | null;
  direction: string | null;
  side: string | null;
  entry_value: number | null;
  stop_loss: number | null;
  targets: number[];
  leverage: number | null;
  scale_factor: number | null;
  scale_unknown: boolean;
  stages: PremiumSignalTrailStage[];
  overall: string;
  is_open: boolean;
  realized_pnl_usd: number | null;
  next_action_hint: string;
  approved_envelope_id: string | null;
  bridge_history: PremiumSignalTrailBridgeHistoryEntry[];
  paper_order_id: string | null;
  paper_position_state: string | null;
  paper_close_reason: string | null;
  quantity: number | null;
  analytics?: PremiumSignalAnalytics | null;
};

export type PremiumSignalOrphanCompletion = {
  timestamp_utc: string | null;
  symbol: string;
  touch_price: number | null;
  reason: string | null;
  source_envelope_id: string | null;
  raw_text: string | null;
};

export type PremiumSignalTrailResponse = {
  count: number;
  limit: number;
  trail: PremiumSignalTrailEntry[];
  orphan_completions?: PremiumSignalOrphanCompletion[];
};

export function fetchPremiumSignalTrail(
  limit = 20,
  signal?: AbortSignal,
): Promise<PremiumSignalTrailResponse> {
  return apiGet<PremiumSignalTrailResponse>(
    `/api/premium-signals/trail?limit=${limit}`,
    { signal },
  );
}

// ── Premium Runtime Truth (2026-06-04 DALI Premium-Truth-Sprint) ─────────────
// GET /api/premium-signals/runtime — read-only Safety-Switch-Wahrheit. Erklärt,
// warum Premium-Signale geparst/approved werden können, aber keine Position
// öffnen (entry_mode, Bridge, Source-Allowlist, Paper/Live-Flags).
export type PremiumFastlaneStatus = {
  enabled: boolean;
  active: boolean;
  window_reason: string | null;
  mode: string;
  route: string;
  duration_days: number;
  start_date: string | null;
  end_date: string | null;
  days_remaining: number | null;
  bypassed_gates: string[];
  live_armed: boolean;
  live_protected: boolean;
  overrides_classic_block: boolean;
  default_notional_usdt: number;
  min_notional_usdt: number;
  max_notional_usdt: number;
  max_leverage: number;
  max_open_positions: number;
  paper_equity_usdt: number;
  observe_only_metrics: string[];
};

export type PremiumRuntimeResponse = {
  entry_mode: string;
  entry_mode_allows_risk_increasing_entry: boolean;
  entry_mode_blocks_premium_paper: boolean;
  can_open_paper_positions: boolean;
  classic_can_open_paper_positions?: boolean;
  blocking_reasons: string[];
  premium_paper_execution_enabled: boolean;
  premium_live_execution_enabled: boolean;
  premium_require_manual_approval_for_paper: boolean;
  premium_require_manual_approval_for_live: boolean;
  operator_signal_bridge_enabled: boolean;
  operator_signal_source_allowlist: string[];
  premium_auto_fill_enabled: boolean;
  live_execution_enabled: boolean;
  execution_mode: string;
  premium_fastlane?: PremiumFastlaneStatus;
  warning: string | null;
};

export function fetchPremiumRuntime(
  signal?: AbortSignal,
): Promise<PremiumRuntimeResponse> {
  return apiGet<PremiumRuntimeResponse>("/api/premium-signals/runtime", {
    signal,
  });
}

// Decimal-as-string defense: the backend serializes money as JSON strings, so
// coerce every numeric field once here before any consumer does arithmetic on
// it. See lib/num.ts.
function normalizePaperPosition(p: PaperPosition): PaperPosition {
  return {
    ...p,
    quantity: toNumOr(p.quantity),
    avg_entry_price: toNumOr(p.avg_entry_price),
    stop_loss: toNum(p.stop_loss),
    take_profit: toNum(p.take_profit),
    market_price: toNum(p.market_price),
    market_value_usd: toNum(p.market_value_usd),
    unrealized_pnl_usd: toNum(p.unrealized_pnl_usd),
    display_value_usd: p.display_value_usd === undefined ? undefined : toNum(p.display_value_usd),
    realized_pnl_usd: p.realized_pnl_usd === undefined ? undefined : toNum(p.realized_pnl_usd),
    leverage: p.leverage === undefined ? undefined : toNum(p.leverage),
    initial_quantity: p.initial_quantity === undefined ? undefined : toNum(p.initial_quantity),
  };
}

export async function fetchPortfolioSnapshot(signal?: AbortSignal): Promise<PortfolioSnapshot> {
  const raw = await apiGet<PortfolioSnapshot>("/operator/portfolio-snapshot", { signal });
  return {
    ...raw,
    cash_usd: toNumOr(raw.cash_usd),
    realized_pnl_usd: toNumOr(raw.realized_pnl_usd),
    total_market_value_usd: toNumOr(raw.total_market_value_usd),
    total_equity_usd: toNumOr(raw.total_equity_usd),
    position_count: toNumOr(raw.position_count),
    positions: (raw.positions ?? []).map(normalizePaperPosition),
  };
}

export type ExposureSummary = {
  report_type: string;
  priced_position_count: number;
  stale_position_count: number;
  unavailable_price_count: number;
  gross_exposure_usd: number;
  net_exposure_usd: number;
  largest_position_symbol: string | null;
  largest_position_weight_pct: number | null;
  mark_to_market_status: string;
  execution_enabled: boolean;
  write_back_allowed: boolean;
  generated_at: string;
  available: boolean;
  error: string | null;
};

export function fetchExposureSummary(signal?: AbortSignal): Promise<ExposureSummary> {
  return apiGet<ExposureSummary>("/operator/exposure-summary", { signal });
}

// 2026-05-25 Forensik-Patch: realized-by-asset entkoppelt von Live-Mode.
export type RealizedByAssetEntry = {
  symbol: string;
  realized_pnl_usd: number;
  closed_trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  fees_usd_total: number;
  partial_closes: number;
  full_closes: number;
  last_close_utc: string | null;
};

export type RealizedByAssetResponse = {
  as_of_utc: string;
  audit_path: string;
  // 2026-06-04: source-attributed view (RC-3). Backend liefert source_prefix
  // wenn ?source_prefix= gesetzt ist; source_filter ist der UI-Fallback-Name.
  source_prefix?: string | null;
  source_filter?: string | null;
  audit_file_exists: boolean;
  audit_last_event_utc: string | null;
  by_asset: RealizedByAssetEntry[];
  totals: {
    realized_pnl_usd: number;
    closed_trades: number;
    assets_count: number;
    fees_usd_total: number;
    partial_close_events: number;
    full_close_events: number;
  };
  top_performer: RealizedByAssetEntry | null;
  worst_performer: RealizedByAssetEntry | null;
  available: boolean;
  error: string | null;
  invalid_lines: [number, string][];
};

export function fetchRealizedByAsset(
  signal?: AbortSignal,
  sourceFilter?: string,
): Promise<RealizedByAssetResponse> {
  const q = sourceFilter ? `?source_filter=${encodeURIComponent(sourceFilter)}` : "";
  return apiGet<RealizedByAssetResponse>(`/operator/portfolio/realized-by-asset${q}`, { signal });
}

export type PaperPipelineStatusResponse = {
  as_of_utc: string;
  audit_files: Record<string, {
    path: string;
    exists: boolean;
    age_seconds: number | null;
    last_order_created_utc?: string | null;
    last_order_filled_utc?: string | null;
    last_position_close_utc?: string | null;
  }>;
  replay_health: {
    available: boolean;
    error: string | null;
    cash_usd?: number;
    open_positions?: string[];
    open_positions_count?: number;
    skipped_events?: { line: number; reason: string }[];
  };
  cron_recent_1000: {
    total_status_rows: number;
    priority_rejected: number;
    completed: number;
    priority_rejected_share_pct: number | null;
  };
  block_reasons_24h: Record<string, number>;
  block_total_24h: number;
  realized_summary: {
    total_realized_pnl_usd: number;
    closed_trades: number;
    assets_count: number;
    last_close_utc: string | null;
  };
  freeze_indicators: {
    paper_audit_stale_seconds: number | null;
    no_fills_since_seconds: number | null;
    all_cron_priority_rejected: boolean;
  };
};

export function fetchPaperPipelineStatus(
  signal?: AbortSignal,
): Promise<PaperPipelineStatusResponse> {
  return apiGet<PaperPipelineStatusResponse>("/operator/paper-pipeline-status", { signal });
}

export type TradingLoopStatus = {
  report_type: string;
  mode: string;
  run_once_allowed: boolean;
  run_once_block_reason: string | null;
  total_cycles: number;
  last_cycle_id: string | null;
  last_cycle_status: string | null;
  last_cycle_symbol: string | null;
  last_cycle_completed_at: string | null;
  audit_path: string;
  auto_loop_enabled: boolean;
  execution_enabled: boolean;
  write_back_allowed: boolean;
};

export function fetchTradingLoopStatus(signal?: AbortSignal): Promise<TradingLoopStatus> {
  return apiGet<TradingLoopStatus>("/operator/trading-loop/status", { signal });
}

// --- Asset diversification / concentration overview ---
export type ConcentrationBucket = {
  dimension: string;
  key: string;
  exposure_usd: number;
  weight_pct: number;
  limit_pct: number | null;
  over_limit: boolean;
};

export type DiversificationAssetRow = {
  symbol: string;
  base: string;
  exposure_usd: number | null;
  weight_pct: number | null;
  exposure_basis: string;
  asset_horizon: string;
  position_horizon: string;
  sector: string;
  narrative: string;
  correlation_group: string;
  risk_tier: string;
  liquidity_tier: string;
  is_reserve: boolean;
  evaluable: boolean;
  source: string;
};

export type DiversificationCandidate = {
  symbol: string;
  base: string;
  structural_score: number;
  adjusted_score: number;
  horizon: string;
  sector: string;
  correlation_group: string;
  included: boolean;
  reasons: string[];
};

export type DiversificationOverview = {
  report_type: string;
  generated_at?: string;
  guard_enabled?: boolean;
  guard_mode?: string;
  universe_scan_enabled?: boolean;
  portfolio?: {
    source: string;
    available: boolean;
    error: string | null;
    cash_usd: number;
    total_equity_usd: number;
    position_count: number;
  };
  concentration?: {
    short_term_gross_usd: number;
    reserve_gross_usd: number;
    total_gross_usd: number;
    priced_position_count: number;
    unpriced_position_count: number;
    btc_eth_short_term_pct: number | null;
    horizon_split_pct: Record<string, number>;
    buckets: ConcentrationBucket[];
    warnings: string[];
    evaluable: boolean;
  };
  asset_distribution?: DiversificationAssetRow[];
  by_source?: { source: string; exposure_usd: number; weight_pct: number | null }[];
  candidates?: DiversificationCandidate[];
  cluster_warnings?: string[];
  universe_size?: number;
  available?: boolean;
  error?: string;
};

export function fetchDiversificationOverview(
  signal?: AbortSignal,
): Promise<DiversificationOverview> {
  return apiGet<DiversificationOverview>("/api/diversification/overview", { signal });
}

export type TradingCycle = {
  cycle_id: string;
  started_at: string;
  completed_at: string | null;
  symbol: string;
  status: string;
  market_data_fetched: boolean;
  signal_generated: boolean;
  risk_approved: boolean;
  order_created: boolean;
  fill_simulated: boolean;
  decision_id: string | null;
  risk_check_id: string | null;
  order_id: string | null;
  notes: string[];
};

export type RecentCyclesSummary = {
  report_type: string;
  total_cycles: number;
  status_counts: Record<string, number>;
  recent_cycles: TradingCycle[];
};

export function fetchRecentCycles(
  lastN: number = 20,
  signal?: AbortSignal,
): Promise<RecentCyclesSummary> {
  return apiGet<RecentCyclesSummary>(`/operator/trading-loop/recent-cycles?last_n=${lastN}`, {
    signal,
  });
}


// 2026-05-12 DALI-arcade-T5: Run-Once Operator-Trigger.
// Endpoint POST /operator/trading-loop/run-once erwartet Idempotency-Key
// als HTTP-Header (Pattern [A-Za-z0-9._:-]{1,128}) - UUID v4 matcht.
// Body: symbol (default "BTC/USDT"), mode (paper), provider (mock).
// Response: dict aus run_trading_loop_once + idempotency_replayed-Flag.
// 409 nur bei Idempotency-Key + abweichendem Payload (Konflikt). Gleicher
// Key + gleicher Payload -> 200 mit idempotency_replayed=true (Backend-Replay).
export type RunOnceRequest = {
  idempotency_key: string;
  symbol?: string;
  mode?: string;
  provider?: string;
};

export type RunOnceResponse = {
  idempotency_replayed: boolean;
  // weitere Felder kommen direkt aus mcp_server.run_trading_loop_once -
  // wir typisieren defensiv (alle optional), damit das UI tolerant bleibt.
  cycle_id?: string;
  status?: string;
  symbol?: string;
  mode?: string;
  [key: string]: unknown;
};

export async function postRunOnce(req: RunOnceRequest): Promise<RunOnceResponse> {
  const { idempotency_key, symbol, mode, provider } = req;
  const body: Record<string, string> = {};
  if (symbol && symbol.trim()) body.symbol = symbol.trim();
  if (mode) body.mode = mode;
  if (provider) body.provider = provider;
  return apiPost<RunOnceResponse>("/operator/trading-loop/run-once", body, {
    headers: { "Idempotency-Key": idempotency_key },
  });
}

export type AlertTestResponse = {
  dispatched: number;
  results: Array<{
    channel: string;
    success: boolean;
    message_id: string | null;
    error: string | null;
  }>;
};

export function postAlertTest(): Promise<AlertTestResponse> {
  return apiPost<AlertTestResponse>("/alerts/test");
}

// ---------------- Agents (SENTR / Watchdog / Architect) ----------------

export type AgentStatus = "live" | "prepared" | "unavailable";

export type AgentSummary = {
  slug: string;
  name: string;
  agent_id: string | null;
  role: string;
  modes: string[];
  permissions: string[];
  status: AgentStatus;
  last_seen: string | null;
  findings_count: number;
  runs_count: number;
};

export type AgentFinding = {
  ts?: string;
  timestamp?: string;
  severity?: string;
  title?: string;
  detail?: string;
  [k: string]: unknown;
};

export type AgentRun = {
  ts?: string;
  timestamp?: string;
  mode?: string;
  result?: string;
  duration_ms?: number;
  [k: string]: unknown;
};

export type AgentDetail = AgentSummary & {
  recent_findings: AgentFinding[];
  recent_runs: AgentRun[];
};

export type AgentListResponse = {
  agents: AgentSummary[];
  generated_at: string;
};

export function fetchAgents(signal?: AbortSignal): Promise<AgentListResponse> {
  return apiGet<AgentListResponse>("/operator/agents", { signal });
}

export function fetchAgentDetail(slug: string, signal?: AbortSignal): Promise<AgentDetail> {
  return apiGet<AgentDetail>(`/operator/agents/${encodeURIComponent(slug)}`, { signal });
}

export type AgentCommandResponse = {
  id: string;
  ts: string;
  agent: string;
  mode: string;
  note: string | null;
  status: string;
};

export function postAgentCommand(
  slug: string,
  mode: string,
  note?: string,
): Promise<AgentCommandResponse> {
  return apiPost<AgentCommandResponse>(
    `/operator/agents/${encodeURIComponent(slug)}/commands`,
    { mode, note: note ?? null },
  );
}

// ---------------- Agent-Conversation (Dashboard ↔ Telegram ↔ Agent) ----------------

export type AgentEventSource = "dashboard" | "telegram" | "agent";
export type AgentEventRole = "operator" | "agent";

export type AgentEvent = {
  id: string;
  ts: string;
  agent: string;
  source: AgentEventSource;
  role: AgentEventRole;
  kind: string; // "message" | "command" | "finding" | "report"
  content: string;
  meta: Record<string, unknown>;
};

export type AgentMessagesResponse = {
  agent: string;
  events: AgentEvent[];
  count: number;
  generated_at: string;
};

export function fetchAgentMessages(
  slug: string,
  opts: { tail?: number; since?: string } = {},
  signal?: AbortSignal,
): Promise<AgentMessagesResponse> {
  const q = new URLSearchParams();
  if (opts.tail != null) q.set("tail", String(opts.tail));
  if (opts.since) q.set("since", opts.since);
  const qs = q.toString();
  return apiGet<AgentMessagesResponse>(
    `/operator/agents/${encodeURIComponent(slug)}/messages${qs ? "?" + qs : ""}`,
    { signal },
  );
}

export function postAgentMessage(
  slug: string,
  content: string,
): Promise<AgentEvent> {
  return apiPost<AgentEvent>(
    `/operator/agents/${encodeURIComponent(slug)}/messages`,
    { content, source: "dashboard" },
  );
}

// ---------------- Signals Paste (Dashboard ↔ Envelope-Pipeline) ----------------

export type SignalPasteStatus =
  | "accepted"
  | "duplicate"
  | "rejected"
  | "needs_completion";
export type SignalPasteStage =
  | "accepted"
  | "idempotency_gate"
  | "parse"
  | "schema_validation"
  | "execution_gate"
  | "completion_gate";

export type SignalCompletionFields = {
  exchange_scope?: string[];
  stop_loss?: number;
  targets?: number[];
  leverage?: number;
  source?: string;
};

export type SignalPasteResponse = {
  status: SignalPasteStatus;
  stage: SignalPasteStage;
  message_type: string | null;
  envelope_id: string | null;
  idempotency_key: string | null;
  errors: string[];
  missing_fields: string[];
  parsed_preview: Record<string, unknown> | null;
};

export function postSignalPaste(
  text: string,
  extra?: {
    operator_user_id?: string;
    trace_id?: string;
    completion_fields?: SignalCompletionFields;
  },
): Promise<SignalPasteResponse> {
  return apiPost<SignalPasteResponse>("/signals/paste", { text, ...extra });
}

export type SignalSummary = {
  signal_id: string | null;
  origin_signal_id?: string | null;
  source_uid?: string | null;
  symbol: string | null;
  direction: string | null;
  side: string | null;
  exchange_scope: string[];
  market_type: string | null;
  entry_type: string | null;
  entry_value: number | null;
  targets: number[];
  stop_loss: number | null;
  leverage: number | null;
  signal_status: string | null;
  signal_timestamp: string | null;
};

export type EnvelopeRecord = {
  timestamp_utc: string | null;
  event: string | null;
  source: string | null;
  raw_source?: string | null;
  normalized_source?: string | null;
  execution_source?: string | null;
  origin_signal_id?: string | null;
  approval_state?: "approved" | "awaiting_approval" | "none" | string | null;
  premium_state?: string | null;
  premium_state_label?: string | null;
  premium_state_tone?: "pos" | "warn" | "neg" | "neutral" | string | null;
  bridge_stage?: string | null;
  bridge_reason?: string | null;
  stage: string | null;
  status: string | null;
  message_type: string | null;
  envelope_id: string | null;
  idempotency_key: string | null;
  errors: string[];
  signal: SignalSummary | null;
  raw_text_preview: string | null;
  // Dedupe (2026-06-08): raw + approved collapsed into one business signal.
  dedup_key?: string | null;
  double_sourced?: boolean;
  has_raw_event?: boolean;
  has_approved_event?: boolean;
  merged_event_count?: number;
};

export type EnvelopeRecentResponse = {
  count: number;
  records: EnvelopeRecord[];
  deduped_from?: number | null;
};

export function fetchRecentEnvelopes(
  limit: number = 50,
  signal?: AbortSignal,
): Promise<EnvelopeRecentResponse> {
  return apiGet<EnvelopeRecentResponse>(
    `/signals/envelope/recent?limit=${limit}`,
    { signal },
  );
}

/* ---------------------------------------------------------------------------
 * Auto-Annotate Cohort Report (DALI-P-102 / V5 Followup)
 * Feeds the AutoAnnotateCohortDrawer; mirrors GET /alerts/auto-annotate-report.
 * ------------------------------------------------------------------------- */

export type CohortCounters = {
  total: number;
  hit: number;
  miss: number;
  inconclusive: number;
  resolved: number;
  hit_rate_pct: number | null;
  inconclusive_pct: number | null;
};

export type LatestPerDocCohort = CohortCounters & {
  raw_rows: number;
  unique_document_ids: number;
  duplicate_rows_removed: number;
};

export type FreshDispatchCohort = CohortCounters & {
  missing_audit: number;
};

export type CohortBundle = {
  fresh_auto: CohortCounters;
  backfill: CohortCounters;
  reeval: CohortCounters;
  other: CohortCounters;
  latest_per_doc: LatestPerDocCohort;
  fresh_dispatch: FreshDispatchCohort;
};

export type CohortReportWindow = {
  since: string | null;
  until: string | null;
  timestamp_basis: "annotated_at" | "dispatched_at";
};

export type AutoAnnotateCohortReport = {
  window: CohortReportWindow;
  raw_rows: number;
  invalid_timestamp: number;
  cohorts: CohortBundle;
  generated_at: string;
};

export type CohortRangePreset = "24h" | "7d" | "30d";

export function fetchAutoAnnotateCohortReport(
  range: CohortRangePreset,
  dispatchedWindow: boolean = false,
  signal?: AbortSignal,
): Promise<AutoAnnotateCohortReport> {
  const now = new Date();
  const sinceMs = {
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
  }[range];
  const since = new Date(now.getTime() - sinceMs).toISOString();
  const params = new URLSearchParams({ since });
  if (dispatchedWindow) params.set("dispatched_window", "true");
  return apiGet<AutoAnnotateCohortReport>(
    `/alerts/auto-annotate-report?${params.toString()}`,
    { signal },
  );
}
