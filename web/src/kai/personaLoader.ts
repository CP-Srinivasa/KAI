// KAI Persona Loader — Frontend reads runtime config exposed by /api/kai/persona.
// Backend (Python KaiPersonaService, Phase B) does the YAML parsing + schema validation;
// frontend only consumes the validated JSON. This avoids shipping a yaml-parser to the SPA.

import type { KaiState, KaiLanguage } from "./types";

export interface KaiPersonaSnapshot {
  id: "kai";
  name: string;
  full_name: string;
  motto: "Persona non grata";
  version: string;
  language_default: KaiLanguage;
  languages_supported: KaiLanguage[];
  state_machine: {
    default_state: KaiState;
    priority_order: KaiState[];
    states: Record<
      KaiState,
      {
        priority: number;
        color: string;
        icon: string;
        animation: string;
        ui_behavior: string;
        severity: string;
        phrases_de: string[];
        phrases_en: string[];
      }
    >;
  };
  dashboard?: {
    module_name: string;
    enabled: boolean;
    placement_recommendation?: string;
    refresh_interval_seconds?: number;
  };
  telegram?: {
    bot_persona_name: string;
    enabled: boolean;
  };
}

const SNAPSHOT_URL = "/api/kai/persona";
let _cached: KaiPersonaSnapshot | null = null;
let _inflight: Promise<KaiPersonaSnapshot> | null = null;

export async function loadKaiPersona(force = false): Promise<KaiPersonaSnapshot> {
  if (_cached && !force) return _cached;
  if (_inflight && !force) return _inflight;

  _inflight = fetch(SNAPSHOT_URL, { headers: { Accept: "application/json" } })
    .then((res) => {
      if (!res.ok) throw new Error(`KAI persona load failed: HTTP ${res.status}`);
      return res.json() as Promise<KaiPersonaSnapshot>;
    })
    .then((snapshot) => {
      assertPersonaShape(snapshot);
      _cached = snapshot;
      _inflight = null;
      return snapshot;
    })
    .catch((err) => {
      _inflight = null;
      throw err;
    });

  return _inflight;
}

function assertPersonaShape(snapshot: KaiPersonaSnapshot): void {
  if (!snapshot || typeof snapshot !== "object") {
    throw new Error("KAI persona: not an object");
  }
  if (snapshot.motto !== "Persona non grata") {
    throw new Error(`KAI persona: motto mismatch '${snapshot.motto}' (must be 'Persona non grata')`);
  }
  if (!snapshot.state_machine?.states) {
    throw new Error("KAI persona: state_machine.states missing");
  }
  const required: KaiState[] = ["IDLE", "ANALYSIS", "SIGNAL", "WARNING", "SECURITY", "ERROR", "OFFLINE"];
  for (const s of required) {
    if (!snapshot.state_machine.states[s]) {
      throw new Error(`KAI persona: state ${s} missing from config`);
    }
  }
}

export function getCachedPersona(): KaiPersonaSnapshot | null {
  return _cached;
}

export function resetKaiPersonaCache(): void {
  _cached = null;
  _inflight = null;
}
