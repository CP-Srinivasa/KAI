// Pure Mapper für die System-/Health-Seite (WP-2.4). Externe Health-Zustände →
// kanonische Status-Sprache (lib/status.ts). Getrennt → testbar.
import type { StatusKind } from "@/lib/status";
import type { TimerHealthResponse } from "@/lib/api";

/** Timer-Health-Gesamtzustand → StatusKind. */
export function timerStateToStatus(state: TimerHealthResponse["state"]): StatusKind {
  switch (state) {
    case "ok":
      return "operational";
    case "has_inactive":
      return "degraded";
    case "stale":
      return "stale";
    case "no_data":
      return "unverified";
    case "corrupt":
    case "critical":
      return "critical";
    default:
      return "unverified";
  }
}
