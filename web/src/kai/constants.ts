// KAI Persona — Constants
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §3 + §15

import type { KaiState } from "./types";

// Priority order — strictly: ERROR > WARNING > SIGNAL > SECURITY > ANALYSIS > IDLE > OFFLINE
// Source: docs/kai_persona/final_execution_prompt_v3_4.md §5.2
// This priority is unverhandelbar (per spec): KAI must never cosmetically downgrade a critical state.
export const KAI_STATE_PRIORITY: Record<KaiState, number> = {
  ERROR: 100,
  WARNING: 90,
  SIGNAL: 80,
  SECURITY: 70,
  ANALYSIS: 50,
  IDLE: 10,
  OFFLINE: 0,
};

// Status labels rendered in the dashboard badge.
// Source: config/kai_persona.yaml § dashboard.status_badges
export const KAI_STATUS_LABEL: Record<KaiState, string> = {
  IDLE: "IDLE",
  ANALYSIS: "SCANNING",
  SIGNAL: "SIGNAL FOUND",
  WARNING: "WARNING",
  SECURITY: "SECURITY CHECK",
  ERROR: "ERROR",
  OFFLINE: "OFFLINE",
};

export const KAI_STATE_COLOR: Record<KaiState, string> = {
  IDLE: "#00B8D9",
  ANALYSIS: "#00E5FF",
  SIGNAL: "#FF2BD6",
  WARNING: "#FF6B00",
  SECURITY: "#00FFA3",
  ERROR: "#FF1744",
  OFFLINE: "#64748B",
};

// Iconographic name (resolved by asset mapper to a real file).
export const KAI_STATE_ICON: Record<KaiState, string> = {
  IDLE: "kai_idle",
  ANALYSIS: "kai_analysis",
  SIGNAL: "kai_signal",
  WARNING: "kai_warning",
  SECURITY: "kai_security",
  ERROR: "kai_error",
  OFFLINE: "kai_offline",
};

export const KAI_STATE_ANIMATION: Record<KaiState, string> = {
  IDLE: "idle_loop",
  ANALYSIS: "data_scan",
  SIGNAL: "signal_found_pulse",
  WARNING: "warning_glitch",
  SECURITY: "security_scan",
  ERROR: "error_screen_tear",
  OFFLINE: "static_fade",
};

// Forbidden financial-claim phrases — fail tests and renderer guards on these.
// Source: docs/kai_persona/final_execution_prompt_v3_4.md §12 + §17.3
export const KAI_FORBIDDEN_PHRASES_DE = [
  "sicherer Gewinn",
  "garantierter Gewinn",
  "kann nicht verlieren",
  "100% sicher",
  "100 Prozent sicher",
];

export const KAI_FORBIDDEN_PHRASES_EN = [
  "guaranteed profit",
  "risk-free profit",
  "cannot lose",
  "100% safe",
];

export const KAI_DEFAULT_LANGUAGE = "de" as const;
export const KAI_BRAND_MOTTO = "Persona non grata" as const;
export const KAI_BRAND_NAME = "KAI" as const;
export const KAI_BRAND_FULL_NAME = "KAI — Kinetic Artificial Intelligence" as const;
