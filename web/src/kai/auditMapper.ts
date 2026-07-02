// KAI Persona — Audit Mapper
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §12
// Every important KAI render or state-change must produce a structured audit event so
// post-mortem replay can reconstruct what KAI showed at any point in time.
//
// Frontend creates the events; backend KaiAuditService persists them to
// artifacts/kai_audit.jsonl (Phase B). For Phase A, events are queued in-memory and
// dispatched via fetch('/api/kai/audit') — wire-up happens in service layer.

import type {
  KaiAuditEvent,
  KaiAuditEventType,
  KaiRuntimeState,
  KaiSignalCardData,
  KaiState,
  KaiSeverity,
  KaiWarningCardData,
  KaiSecurityCardData,
  KaiAgentStatus,
} from "./types";

let _seq = 0;
function newAuditId(): string {
  _seq += 1;
  const ts = Date.now().toString(36);
  return `kai_${ts}_${_seq.toString(36)}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

interface BaseAuditInput {
  source: string;
  state: KaiState;
  severity: KaiSeverity;
  correlationId?: string;
}

export function buildKaiStateChangedEvent(
  prev: KaiRuntimeState | null,
  next: KaiRuntimeState,
  source: string,
): KaiAuditEvent {
  return {
    id: newAuditId(),
    type: "KAI_STATE_CHANGED",
    timestamp: nowIso(),
    state: next.state,
    severity: next.severity,
    source,
    payload: {
      prevState: prev?.state ?? null,
      prevPriority: prev?.priority ?? null,
      nextState: next.state,
      nextPriority: next.priority,
      comment: next.comment,
    },
    message: `KAI state ${prev?.state ?? "—"} -> ${next.state}`,
  };
}

export function buildKaiSignalRenderedEvent(
  signal: KaiSignalCardData,
  ctx: BaseAuditInput,
): KaiAuditEvent {
  return {
    id: newAuditId(),
    type: "KAI_SIGNAL_RENDERED",
    timestamp: nowIso(),
    state: ctx.state,
    severity: ctx.severity,
    source: ctx.source,
    correlationId: ctx.correlationId,
    payload: {
      asset: signal.asset,
      mode: signal.mode,
      direction: signal.direction,
      confidence: signal.confidence,
      risk: signal.risk,
      dataQuality: signal.dataQuality,
      timestamp: signal.timestamp,
    },
    message: `KAI rendered signal ${signal.asset} ${signal.direction} (${signal.mode}) confidence=${signal.confidence}%`,
  };
}

export function buildKaiWarningRenderedEvent(
  warning: KaiWarningCardData,
  ctx: BaseAuditInput,
): KaiAuditEvent {
  return {
    id: newAuditId(),
    type: "KAI_WARNING_RENDERED",
    timestamp: nowIso(),
    state: ctx.state,
    severity: ctx.severity,
    source: ctx.source,
    correlationId: ctx.correlationId,
    payload: {
      target: warning.target,
      problem: warning.problem,
      risk: warning.risk,
    },
    message: `KAI rendered warning for ${warning.target}: ${warning.problem}`,
  };
}

export function buildKaiSecurityReportEvent(
  report: KaiSecurityCardData,
  ctx: BaseAuditInput,
): KaiAuditEvent {
  return {
    id: newAuditId(),
    type: "KAI_SECURITY_REPORT_RENDERED",
    timestamp: nowIso(),
    state: ctx.state,
    severity: ctx.severity,
    source: ctx.source,
    correlationId: ctx.correlationId,
    payload: {
      area: report.area,
      status: report.status,
      priority: report.priority,
    },
    message: `KAI security report ${report.area} status=${report.status}`,
  };
}

export function buildKaiLivetradeBlockedEvent(
  signal: KaiSignalCardData,
  reasons: string[],
  ctx: BaseAuditInput,
): KaiAuditEvent {
  return {
    id: newAuditId(),
    type: "KAI_LIVETRADE_BLOCKED",
    timestamp: nowIso(),
    state: ctx.state,
    severity: "critical",
    source: ctx.source,
    correlationId: ctx.correlationId,
    payload: {
      asset: signal.asset,
      mode: signal.mode,
      risk: signal.risk,
      dataQuality: signal.dataQuality,
      reasons,
    },
    message: `KAI blocked LIVETRADE for ${signal.asset}: ${reasons.join("; ")}`,
  };
}

export function buildKaiAgentSummaryEvent(
  statuses: KaiAgentStatus[],
  ctx: BaseAuditInput,
): KaiAuditEvent {
  return {
    id: newAuditId(),
    type: "KAI_AGENT_SUMMARY_RENDERED",
    timestamp: nowIso(),
    state: ctx.state,
    severity: ctx.severity,
    source: ctx.source,
    correlationId: ctx.correlationId,
    payload: {
      summary: statuses.map((s) => ({ agent: s.agent, status: s.status, priority: s.priority })),
    },
    message: `KAI agent-summary rendered for ${statuses.length} agents`,
  };
}

export function buildKaiAssetFallbackEvent(
  state: KaiState,
  reason: string,
  ctx: BaseAuditInput,
): KaiAuditEvent {
  return {
    id: newAuditId(),
    type: "KAI_ASSET_FALLBACK_USED",
    timestamp: nowIso(),
    state,
    severity: "info",
    source: ctx.source,
    correlationId: ctx.correlationId,
    payload: { state, reason },
    message: `KAI asset fallback used for ${state}: ${reason}`,
  };
}

export function buildKaiConfigValidationFailedEvent(
  validationErrors: string[],
  ctx: BaseAuditInput,
): KaiAuditEvent {
  return {
    id: newAuditId(),
    type: "KAI_CONFIG_VALIDATION_FAILED",
    timestamp: nowIso(),
    state: "ERROR",
    severity: "critical",
    source: ctx.source,
    payload: { errors: validationErrors },
    message: `KAI config validation failed: ${validationErrors.length} error(s)`,
  };
}

// Generic builder for any audit event type — kept minimal because each event needs a typed
// payload structure. Components should prefer the specialised builders above.
export function buildKaiAuditEvent(
  type: KaiAuditEventType,
  ctx: BaseAuditInput & { message: string; payload: Record<string, unknown> },
): KaiAuditEvent {
  return {
    id: newAuditId(),
    type,
    timestamp: nowIso(),
    state: ctx.state,
    severity: ctx.severity,
    source: ctx.source,
    correlationId: ctx.correlationId,
    payload: ctx.payload,
    message: ctx.message,
  };
}
