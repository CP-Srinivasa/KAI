// Zentraler API-Client für das KAI-Dashboard.
// - Same-origin im Prod-Build (unter /dashboard), im Dev via Vite-Proxy auf :8000.
// - Bearer-Token aus localStorage("kai-api-key") ODER VITE_KAI_API_KEY (Build-Zeit).
// - Einheitliches Error-Objekt, damit Pages konsistent reagieren können.

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

const TOKEN_KEY = "kai-api-key";

function readToken(): string {
  try {
    const ls = localStorage.getItem(TOKEN_KEY);
    if (ls) return ls;
  } catch {
    // localStorage nicht erreichbar (SSR / privater Modus) — fällt auf env zurück
  }
  // Vite ersetzt import.meta.env zur Build-Zeit. Leerer String = kein Token.
  return (import.meta.env?.VITE_KAI_API_KEY as string | undefined) ?? "";
}

export function setApiToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // no-op
  }
}

export function hasApiToken(): boolean {
  return readToken().length > 0;
}

function buildHeaders(extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = readToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
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

export type DashboardQuality = {
  precision_pct: number | null;
  false_positive_pct: number | null;
  resolved_count: number;
  directional_count: number;
  hits: number;
  misses: number;
  priority_corr: number | null;
  forward_precision_pct: number | null;
  forward_resolved: number;
  forward_hits: number;
  forward_miss: number;
  paper_fills: number;
  paper_cycles: number;
  real_price_cycles: number;
  gate_status: string | null;
  blocking_reasons: string[];
  actionable_rate_pct: number | null;
  high_priority_hit_rate_pct: number | null;
  low_priority_hit_rate_pct: number | null;
  loop_status_counts: Record<string, number>;
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

// ---------------- Operator surfaces ----------------

export type OperatorStatus = {
  report_type: string;
  execution_enabled: boolean;
  write_back_allowed: boolean;
  status: string;
};

export function fetchOperatorStatus(signal?: AbortSignal): Promise<OperatorStatus> {
  return apiGet<OperatorStatus>("/operator/status", { signal });
}

export function fetchOperatorReadiness(signal?: AbortSignal): Promise<OperatorStatus> {
  return apiGet<OperatorStatus>("/operator/readiness", { signal });
}

export function fetchOperatorDecisionPack(signal?: AbortSignal): Promise<OperatorStatus> {
  return apiGet<OperatorStatus>("/operator/decision-pack", { signal });
}

export type AlertAuditEntry = {
  document_id: string;
  channel: string;
  message_id: string | null;
  is_digest: boolean;
  dispatched_at: string;
};

export type AlertAuditSummary = {
  report_type: string;
  execution_enabled: boolean;
  write_back_allowed: boolean;
  total_alerts: number;
  alerts: AlertAuditEntry[];
};

export function fetchAlertAudit(signal?: AbortSignal): Promise<AlertAuditSummary> {
  return apiGet<AlertAuditSummary>("/operator/alert-audit", { signal });
}

export type PaperPosition = {
  symbol: string;
  quantity: number;
  avg_entry_price: number;
  stop_loss: number | null;
  take_profit: number | null;
  market_price: number | null;
  market_value_usd: number | null;
  unrealized_pnl_usd: number | null;
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

export function fetchPortfolioSnapshot(signal?: AbortSignal): Promise<PortfolioSnapshot> {
  return apiGet<PortfolioSnapshot>("/operator/portfolio-snapshot", { signal });
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

export type SignalPasteStatus = "accepted" | "duplicate" | "rejected";
export type SignalPasteStage =
  | "accepted"
  | "idempotency_gate"
  | "parse"
  | "schema_validation"
  | "execution_gate";

export type SignalPasteResponse = {
  status: SignalPasteStatus;
  stage: SignalPasteStage;
  message_type: string | null;
  envelope_id: string | null;
  idempotency_key: string | null;
  errors: string[];
};

export function postSignalPaste(
  text: string,
  extra?: { operator_user_id?: string; trace_id?: string },
): Promise<SignalPasteResponse> {
  return apiPost<SignalPasteResponse>("/signals/paste", { text, ...extra });
}

export type EnvelopeRecord = {
  timestamp_utc: string | null;
  event: string | null;
  source: string | null;
  stage: string | null;
  status: string | null;
  message_type: string | null;
  envelope_id: string | null;
  idempotency_key: string | null;
  errors: string[];
};

export type EnvelopeRecentResponse = {
  count: number;
  records: EnvelopeRecord[];
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
