// Pure Mapper für den Command Header (WP-1.1): externe Zustände → kanonische
// Status-Sprache (lib/status.ts). Getrennt von der Komponente, damit testbar.
import type { StatusKind, StatusTone } from "@/lib/status";
import type { KaiState } from "@/kai/types";
import type { BackendStatus } from "@/lib/useBackendHealth";
import type { TruthTone } from "@/lib/truthStatus";

/** KAI-Live-Laufzustand → StatusKind. */
export function kaiStateToStatus(state: KaiState): StatusKind {
  switch (state) {
    case "IDLE":
      return "idle";
    case "ANALYSIS":
    case "SIGNAL":
      return "active";
    case "WARNING":
      return "degraded";
    case "SECURITY":
      return "urgent";
    case "ERROR":
      return "critical";
    case "OFFLINE":
      return "fail-closed"; // Backend nicht erreichbar → bewusst kein Handeln
    default:
      return "idle";
  }
}

/** Backend-Health → StatusKind. */
export function backendHealthToStatus(state: BackendStatus["state"]): StatusKind {
  switch (state) {
    case "connected":
      return "operational";
    case "checking":
      return "pending";
    case "unauthorized":
      return "blocked";
    case "offline":
      return "critical";
    default:
      return "pending";
  }
}

/** Truth-Chip-Ton → renderbarer Status-Ton (für die kompakte Truth-Anzeige). */
export function truthToneToStatusTone(tone: TruthTone): StatusTone {
  switch (tone) {
    case "critical":
      return "neg";
    case "warn":
      return "warn";
    case "info":
      return "info";
    case "readonly":
      return "muted";
    case "ok":
      return "pos";
    case "muted":
    default:
      return "muted";
  }
}
