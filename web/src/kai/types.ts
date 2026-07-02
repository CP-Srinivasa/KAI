// KAI Persona — Type System
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §2
// Runtime config: config/kai_persona.yaml
// Motto: Persona non grata

export type KaiLanguage = "de" | "en";

export type KaiState =
  | "IDLE"
  | "ANALYSIS"
  | "SIGNAL"
  | "WARNING"
  | "SECURITY"
  | "ERROR"
  | "OFFLINE";

export type KaiSeverity =
  | "none"
  | "info"
  | "positive_watch"
  | "system"
  | "high"
  | "critical"
  | "unknown";

export type KaiTradingMode =
  | "WATCHLIST"
  | "PAPERTRADE"
  | "LIVETRADE"
  | "SIMULATION";

export type KaiDirection =
  | "LONG"
  | "SHORT"
  | "NEUTRAL"
  | "NO_TRADE";

export type KaiRiskLevel =
  | "LOW"
  | "MEDIUM"
  | "HIGH"
  | "CRITICAL";

export type KaiDataQuality =
  | "HIGH"
  | "MEDIUM"
  | "LOW"
  | "UNKNOWN";

export type KaiAssetStatus = "available" | "placeholder";

export interface KaiStateDefinition {
  state: KaiState;
  priority: number;
  color: string;
  icon: string;
  animation: string;
  uiBehavior: string;
  severity: KaiSeverity;
  phrases: Record<KaiLanguage, string[]>;
}

export interface KaiRuntimeState {
  state: KaiState;
  severity: KaiSeverity;
  priority: number;
  statusLabel: string;
  color: string;
  icon: string;
  animation: string;
  comment: string;
  timestamp: string;
  source?: string;
  nextAction?: string;
  /** Phase-1 stub marker: the state is a fixed placeholder, not derived from
   *  live system inputs. The UI renders it as a stub, not as a live status. */
  is_stub?: boolean;
  phase?: number;
}

export interface KaiSignalCardData {
  asset: string;
  mode: KaiTradingMode;
  direction: KaiDirection;
  confidence: number;
  risk: KaiRiskLevel;
  entry: string;
  stopLoss: string;
  dataBasis: string[];
  dataQuality: KaiDataQuality;
  timestamp: string;
  comment: string;
}

export interface KaiWarningCardData {
  target: string;
  problem: string;
  risk: KaiRiskLevel;
  action: string;
  timestamp: string;
  comment: string;
}

export interface KaiSecurityCardData {
  area: string;
  status: string;
  priority: KaiRiskLevel;
  lastCheck: string;
  result: string;
  nextStep: string;
  comment: string;
}

export type KaiAgentStatusValue =
  | "OK"
  | "WARNING"
  | "ERROR"
  | "OFFLINE"
  | "UNKNOWN";

export interface KaiAgentStatus {
  agent: "SENTR" | "Watchdog" | "Architect" | "DALI" | "Neo" | "Satoshi";
  status: KaiAgentStatusValue;
  summary: string;
  priority: number;
  timestamp: string;
}

export interface KaiLiveWidgetProps {
  runtimeState: KaiRuntimeState;
  lastSignal?: KaiSignalCardData;
  lastWarning?: KaiWarningCardData;
  agentStatuses?: KaiAgentStatus[];
  compact?: boolean;
  language?: KaiLanguage;
  onOpenAuditLog?: () => void;
  onOpenDetails?: () => void;
}

// NEO-P-101-r2 audit-style event types — stays in JSONL for forensic replay.
export type KaiAuditEventType =
  | "KAI_STATE_CHANGED"
  | "KAI_SIGNAL_RENDERED"
  | "KAI_WARNING_RENDERED"
  | "KAI_SECURITY_REPORT_RENDERED"
  | "KAI_LIVETRADE_BLOCKED"
  | "KAI_LIVETRADE_CONFIRMATION_REQUESTED"
  | "KAI_ERROR_STATE_TRIGGERED"
  | "KAI_AGENT_SUMMARY_RENDERED"
  | "KAI_EXCHANGE_RESPONSE_RENDERED"
  | "KAI_ASSET_FALLBACK_USED"
  | "KAI_CONFIG_VALIDATION_FAILED";

export interface KaiAuditEvent {
  id: string;
  type: KaiAuditEventType;
  timestamp: string;
  state: KaiState;
  severity: KaiSeverity;
  source: string;
  payload: Record<string, unknown>;
  message: string;
  correlationId?: string;
}

export interface KaiAssetEntry {
  path: string;
  status: KaiAssetStatus;
  fallback_to?: string | null;
  format?: string;
  intended_use?: string[];
  production_priority?: "P0" | "P1" | "P2" | "P3";
  production_prompt_ref?: string;
  note?: string;
}

export interface KaiStateAssetSet {
  static: KaiAssetEntry;
  motion_gif: KaiAssetEntry;
  motion_webm: KaiAssetEntry;
  production_priority?: "P0" | "P1" | "P2" | "P3";
  production_prompt_ref?: string;
}

export interface KaiAssetManifest {
  version: string;
  asset_root: string;
  master_decision: {
    mode: string;
    anchor_asset: string;
    anchor_decided_at: string;
    note: string;
  };
  assets: {
    master_portrait_v1: KaiAssetEntry;
    transparent_portrait: KaiAssetEntry;
    telegram_avatar_circular: KaiAssetEntry;
    states: Record<KaiState, KaiStateAssetSet>;
    voice: {
      de: Record<string, KaiAssetEntry>;
      en: Record<string, KaiAssetEntry>;
      voice_default_enabled: boolean;
      note: string;
    };
    talking_avatar_base: KaiAssetEntry;
  };
  fallback_strategy: {
    missing_state_static: string;
    missing_state_motion: string;
    missing_voice_file: string;
    production_mode_fail_closed: string;
    audit_event_on_fallback: string;
  };
}
