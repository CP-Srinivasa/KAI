// @data-source: /dashboard/api/ai/control (ai-control/v1), /dashboard/api/ai/control/history
//
// KI-Kontrollstation (Spec docs/superpowers/specs/2026-10-02-ki-kontrollstation-design.md).
// Typen + Anzeige-Helfer. Bewertet wird im Backend; hier wird nur dargestellt.

import { apiGet } from "./api";

export type AiControlState = "aktiv" | "bereit" | "pausiert" | "gestoert" | "ausser_kraft" | "deaktiviert";
export type StateTone = "pos" | "info" | "warn" | "neg" | "ai" | "muted";

export const STATE_META: Record<AiControlState, { label: string; symbol: string; tone: StateTone }> = {
  aktiv: { label: "AKTIV", symbol: "●", tone: "pos" },
  bereit: { label: "BEREIT", symbol: "○", tone: "info" },
  pausiert: { label: "PAUSIERT", symbol: "◐", tone: "warn" },
  gestoert: { label: "GESTÖRT", symbol: "✖", tone: "neg" },
  ausser_kraft: { label: "AUSSER KRAFT", symbol: "⊘", tone: "ai" },
  deaktiviert: { label: "DEAKTIVIERT", symbol: "─", tone: "muted" },
};

export type Verdict = { state: AiControlState | null; reason: string | null; since: string | null };

export type Attention = {
  key: string;
  severity: "warn" | "crit";
  title: string;
  detail: string;
  since: string;
  min_age_min: number;
  action: { kind: "details" | "topup"; url?: string | null };
};

export type WorkloadPart = {
  service: string;
  transport: string;
  model: string;
  calls: number;
  cost_usd: number;
  last_call: string | null;
  top_sources: [string, number][];
};

export type Workload = Verdict & {
  purpose: string;
  title: string;
  route: string;
  mode: string;
  calls_today: number;
  tokens_in_today: number;
  tokens_out_today: number;
  approx_kb_today: number;
  cost_today_usd: number;
  unknown_cost_calls_today: number;
  failure_rate_24h: number | null;
  fallbacks_today: number;
  sparfenster: string | null;
  parts: WorkloadPart[];
};

export type Account = {
  provider: string;
  status: "ok" | "fehler" | "kein_schluessel" | "kein_api";
  balance: number | null;
  currency: string | null;
  error: string | null;
  fetched_at: string;
  topup_url: string;
  runway_days: number | null;
  stale: boolean;
  detail: Record<string, unknown>;
};

export type AiControlResponse = {
  schema: "ai-control/v1";
  generated_at: string;
  summary: {
    today_usd: number;
    today_limit_usd: number | null;
    month_usd: number;
    month_limit_usd: number | null;
    projected_month_usd: number | null;
    budget_state: string;
    budget_exhausted_at: string | null;
    budget_end_estimate: string | null;
    calls_today: number;
    tokens_in_today: number;
    tokens_out_today: number;
    state_counts: Partial<Record<AiControlState, number>>;
  };
  attention: Attention[];
  connections: {
    proxy: Verdict & { version: string | null; lock_matches: boolean | null; status_code?: number | null };
    aliases: (Verdict & { alias: string; route: string; mode: string; upstream_model: string | null })[];
    providers: (Verdict & { name: string; kind: string; calls_24h: number; failures_24h: number })[];
  };
  workloads: Workload[];
  accounts: Account[];
  accounts_written_at: string | null;
  protocol: { ts: string; actor: string; kind: string; key: string; old: string | null; new: string | null }[];
  null_reasons: Record<string, string>;
};

export type AiControlHistory = {
  schema: "ai-control-history/v1";
  days: {
    day: string;
    cost_by_provider: Record<string, number>;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    budget_exhausted_at: string | null;
  }[];
};

export function fetchAiControl(signal?: AbortSignal): Promise<AiControlResponse> {
  return apiGet<AiControlResponse>("/dashboard/api/ai/control", { signal });
}

export function fetchAiControlHistory(signal?: AbortSignal): Promise<AiControlHistory> {
  return apiGet<AiControlHistory>("/dashboard/api/ai/control/history?days=14", { signal });
}

const USD = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function formatUsd(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : `${USD.format(n)} $`;
}

export function formatTokens(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}

export function runwayLabel(days: number | null): string {
  return days === null ? "∞" : `~${Math.round(days)} Tage`;
}
