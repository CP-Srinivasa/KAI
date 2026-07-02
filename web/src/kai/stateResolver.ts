// KAI Persona — State Resolver + Fail-Closed Guards
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §3, §4
//
// Rule: ERROR > WARNING > SIGNAL > SECURITY > ANALYSIS > IDLE > OFFLINE.
// Critical states must never be cosmetically masked by lower-priority ones.

import { KAI_STATE_PRIORITY, KAI_STATE_COLOR, KAI_STATE_ICON, KAI_STATE_ANIMATION } from "./constants";
import type { KaiRuntimeState, KaiState } from "./types";

const VALID_STATES = new Set<KaiState>(Object.keys(KAI_STATE_PRIORITY) as KaiState[]);

export function resolveKaiState(states: KaiRuntimeState[]): KaiRuntimeState {
  if (!states.length) {
    return createFallbackState("OFFLINE", "Kein Signal. Keine Verbindung.");
  }

  // Defense-in-depth: filter invalid state values to OFFLINE before sorting.
  const sanitized = states.map((s) =>
    VALID_STATES.has(s.state) ? s : createFallbackState("OFFLINE", `Unbekannter State: ${String(s.state)}`),
  );

  return [...sanitized].sort((a, b) => {
    const priorityA = KAI_STATE_PRIORITY[a.state] ?? -1;
    const priorityB = KAI_STATE_PRIORITY[b.state] ?? -1;
    return priorityB - priorityA;
  })[0];
}

export function createFallbackState(state: KaiState, comment: string): KaiRuntimeState {
  const priority = KAI_STATE_PRIORITY[state] ?? 0;
  const color = KAI_STATE_COLOR[state] ?? "#FF1744";
  const icon = KAI_STATE_ICON[state] ?? "kai_error";
  const animation = KAI_STATE_ANIMATION[state] ?? "error_screen_tear";

  return {
    state,
    severity: state === "ERROR" ? "critical" : state === "OFFLINE" ? "unknown" : "info",
    priority,
    statusLabel: state,
    color,
    icon,
    animation,
    comment,
    timestamp: new Date().toISOString(),
    source: "fallback",
  };
}

// Fail-closed: never return a clean OK if something is structurally wrong.
// Source: docs/kai_persona/technical_ui_pack_v3_2.md §4.2
export function failClosedState(reason: string): KaiRuntimeState {
  return {
    state: "ERROR",
    severity: "critical",
    priority: KAI_STATE_PRIORITY.ERROR,
    statusLabel: "ERROR",
    color: KAI_STATE_COLOR.ERROR,
    icon: KAI_STATE_ICON.ERROR,
    animation: KAI_STATE_ANIMATION.ERROR,
    comment: `Da knirscht etwas im Maschinenraum. ${reason}`,
    timestamp: new Date().toISOString(),
    source: "fail_closed_guard",
    nextAction: "System pruefen und Audit-Log oeffnen.",
  };
}

export function isValidKaiState(value: unknown): value is KaiState {
  return typeof value === "string" && VALID_STATES.has(value as KaiState);
}
